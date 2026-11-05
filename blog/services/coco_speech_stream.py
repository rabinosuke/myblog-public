"""生成中の返事を最大4文節に分け、音声生成を1本ずつ並行して進める。"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event

from django.db import connections

from .coco_speech import SpeechError, spoken_text

MAX_SPEECH_CHUNKS = 4
MIN_SPEECH_CHARS = 12


class SentenceBuffer:
    def __init__(self):
        self.text = ''
        self.offset = 0
        self.count = 0

    def feed(self, delta):
        self.text += delta
        chunks = []
        # 最後の枠は残り全文に使い、細かい句点でリクエストが増え続けるのを防ぐ。
        while self.count < MAX_SPEECH_CHUNKS - 1:
            cut = self.sentence_end(self.text[self.offset:])
            if cut is None:
                break
            end = self.offset + cut
            chunk = self.text[self.offset:end].strip()
            self.offset = end
            if chunk:
                self.count += 1
                chunks.append(chunk)
        return chunks

    def finish(self, reply):
        # 完成文が途中の表示と違った場合、その続きは読まず文字だけを残す。
        if self.text.strip() != reply.strip():
            raise SpeechError('speech_text_changed', '返事の内容を確認するため、読み上げを止めました。文章で確認してね。')
        chunk = self.text[self.offset:].strip()
        self.offset = len(self.text)
        if chunk:
            self.count += 1
            return [chunk]
        return []

    @staticmethod
    def sentence_end(text):
        fence = None
        inline = False
        brackets = []
        in_tag = False
        index = 0
        while index < len(text):
            if text.startswith(('```', '~~~'), index):
                token = text[index:index + 3]
                if fence is None:
                    fence = token
                elif fence == token:
                    fence = None
                index += 3
                continue
            char = text[index]
            if fence:
                index += 1
                continue
            if char == '`':
                inline = not inline
                index += 1
                continue
            if inline:
                index += 1
                continue
            if text.startswith(('https://', 'http://'), index):
                end = index
                while end < len(text) and not text[end].isspace():
                    end += 1
                index = end
                continue
            if char == '<':
                in_tag = True
            elif in_tag and char == '>':
                in_tag = False
            elif not in_tag:
                if char in '[(':
                    brackets.append(char)
                elif brackets and char == {'[': ']', '(': ')'}[brackets[-1]]:
                    brackets.pop()
                # 後ろの一文字を待ち、閉じ括弧・小数点・URLを途中で切らない。
                elif not brackets and index + 1 < len(text) and (
                    char in '。！？!?' or (char == '.' and text[index + 1].isspace())
                ):
                    end = index + 1
                    while end < len(text) and text[end] in '」』）”’!?！？':
                        end += 1
                    if len(spoken_text(text[:end])) >= MIN_SPEECH_CHARS:
                        return end
            index += 1
        return None


class SpeechPipeline:
    """各会話に生成用スレッドは最大1本。停止後に次の文のAPIを呼ばない。"""
    def __init__(self, generate):
        self.generate = generate
        self.cancelled = Event()
        self.pool = None
        self.pending = deque()
        self.submitted = 0
        self.sent = 0
        self.failed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def submit(self, text):
        if self.cancelled.is_set() or self.failed:
            return
        if self.submitted >= MAX_SPEECH_CHUNKS:
            raise SpeechError('speech_too_many_chunks', '読み上げが長いため、残りは文章で確認してね。')
        self.pool = self.pool or ThreadPoolExecutor(max_workers=1, thread_name_prefix='coco-speech')
        self.pending.append(self.pool.submit(self.run, text))
        self.submitted += 1

    def run(self, text):
        # DjangoのDB接続をリクエスト側のスレッドと共有せず、終了時には必ず閉じる。
        connections.close_all()
        try:
            if self.cancelled.is_set():
                return None
            return self.generate(text)
        except BaseException:
            # 上限・管理者OFF・生成失敗の後に、待機中の文を生成しない。
            self.cancelled.set()
            raise
        finally:
            connections.close_all()

    def ready(self, wait=False):
        while self.pending and not self.failed:
            future = self.pending[0]
            if not wait and not future.done():
                return
            try:
                audio = future.result(timeout=0.5 if wait else 0)
            except FutureTimeout:
                yield None  # Nginxの接続を保つコメント。音声生成をやり直さない。
                return
            except SpeechError as error:
                yield self.fail(error)
                return
            except Exception:
                yield self.fail(SpeechError('speech_unavailable', '今は読み上げできません。文章で確認してね。'))
                return
            self.pending.popleft()
            if audio is not None:
                yield {'type': 'speech', 'index': self.sent, 'speech': audio}
                self.sent += 1

    def fail(self, error):
        self.failed = True
        self.cancelled.set()
        for future in self.pending:
            future.cancel()
        self.pending.clear()
        return {'type': 'speech_error', 'error': {'code': error.code, 'message': error.message}}

    def close(self):
        self.cancelled.set()
        for future in self.pending:
            future.cancel()
        if self.pool:
            # 送信済みのAPIは課金済みの可能性がある。結果・使用量を回収してから実行枠を返す。
            self.pool.shutdown(wait=True, cancel_futures=True)
        self.pending.clear()

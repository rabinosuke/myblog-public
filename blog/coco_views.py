"""会話APIのHTTP処理。記事ビューやAPIキーをブラウザ側に持ち込まない。"""

import json
import logging
from contextlib import ExitStack, closing

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import DatabaseError
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .services.coco_chat import ChatError, ensure_configured, generate_reply, stream_reply, validate_payload
from .services.coco_limits import reserve_reply
from .services.coco_identity import client_network, set_visitor_cookie, visitor_id
from .services.coco_context import get_article_context
from .services.coco_speech import SpeechError, generate_speech
from .services.coco_speech_stream import SentenceBuffer, SpeechPipeline

logger = logging.getLogger(__name__)


def error_response(error):
    response = JsonResponse({'error': {'code': error.code, 'message': error.message}}, status=error.status)
    if error.retry_after:
        response['Retry-After'] = str(error.retry_after)
    return response


def speech_result(reply):
    try:
        # 読み上げるのは検証済みの完成文だけ。従来のJSON・全文音声クライアント向け。
        return {'speech': generate_speech(reply)}
    except SpeechError as error:
        logger.info('COCO speech rejected: %s', error.code)
        return {'speech_error': {'code': error.code, 'message': error.message}}


class ChatEventStream:
    """未読み取り・通信切断を含め、HTTP応答を閉じる時に同時実行枠も返す。"""
    def __init__(self, reservation, message, history, article, read_aloud, incremental_speech=False):
        self.reservation = reservation
        self.events = self.generate(message, history, article, read_aloud, incremental_speech)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.events)

    def close(self):
        try:
            self.events.close()
        finally:
            self.reservation.close()

    @staticmethod
    def encode(event):
        return 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'

    def generate(self, message, history, article, read_aloud, incremental_speech):
        try:
            with self.reservation, SpeechPipeline(generate_speech) as audio:
                yield self.encode({'type': 'start', **({'speech_stream': True} if incremental_speech else {})})
                reply = None
                sentences = SentenceBuffer()
                # 音声生成は別スレッドで進め、文字の受信・使用量記録を止めない。
                with closing(stream_reply(message, history, article=article)) as events:
                    for event in events:
                        if event['type'] == 'delta' and incremental_speech:
                            for sentence in sentences.feed(event['delta']):
                                audio.submit(sentence)
                        if event['type'] == 'reply':
                            reply = event['reply']
                        yield self.encode(event)
                        if incremental_speech:
                            for speech_event in audio.ready():
                                yield self.encode(speech_event)
                if reply is None:
                    raise ChatError('invalid_reply', '返事を受け取れませんでした。もう一度お試しください。', 502)
                result = {'type': 'complete', 'reply': reply, 'mode': 'ai'}
                if incremental_speech:
                    if not audio.failed:
                        try:
                            for sentence in sentences.finish(reply):
                                audio.submit(sentence)
                            if audio.submitted == 0:
                                raise SpeechError('speech_empty', 'この返事は画面で確認してね。')
                        except SpeechError as error:
                            yield self.encode(audio.fail(error))
                    while audio.pending:
                        for speech_event in audio.ready(wait=True):
                            yield ': keepalive\n\n' if speech_event is None else self.encode(speech_event)
                    result.update(speech_stream=True, speech_chunks=audio.sent)
                elif read_aloud:
                    result.update(speech_result(reply))
                yield self.encode(result)
        except ChatError as error:
            logger.info('COCO stream rejected: %s', error.code)
            yield self.encode({'type': 'error', 'error': {'code': error.code, 'message': error.message}})
        except DatabaseError:
            logger.warning('COCO stream usage store unavailable')
            yield self.encode({'type': 'error', 'error': {'code': 'unavailable', 'message': '今はAI会話を利用できません。時間をおいてお試しください。'}})


def streaming_response(identity, message, history, article, read_aloud, incremental_speech=False, *, network=None):
    # 入力・公開記事・上限はヘッダー送信前に検証し、429等のHTTP応答を維持する。
    reservation = ExitStack()
    try:
        reservation.enter_context(reserve_reply(identity, network=network))
        response = StreamingHttpResponse(ChatEventStream(reservation, message, history, article, read_aloud, incremental_speech),
                                         content_type='text/event-stream; charset=utf-8')
        # Nginxが返答を最後まで溜め込まないよう、この応答だけバッファを無効化する。
        response['X-Accel-Buffering'] = 'no'
        return response
    except BaseException:
        reservation.close()
        raise


@require_POST
@never_cache
def chat(request):
    try:
        ensure_configured()
        if request.content_type != 'application/json':
            raise ChatError('invalid_content_type', 'JSON形式で送信してください。', 415)
        length = request.META.get('CONTENT_LENGTH', '')
        if length and int(length) > settings.COCO_CHAT_MAX_REQUEST_BYTES:
            raise ChatError('request_too_large', '送信する内容が長すぎます。', 413)
        body = request.body
        if len(body) > settings.COCO_CHAT_MAX_REQUEST_BYTES:
            raise ChatError('request_too_large', '送信する内容が長すぎます。', 413)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise ChatError('invalid_json', '入力形式を確認してください。') from None
        message, history, slug, read_aloud = validate_payload(payload)
        # 公開状態は毎回DBで確認する。下書き・削除済みなら利用枠を使う前に止める。
        article = get_article_context(slug, message, history) if slug else None
        # 匿名会話で管理画面用のDBセッションを新設しない。拒否時のDB増加を防ぐ。
        identity, network = visitor_id(request), client_network(request)
        if 'text/event-stream' in request.headers.get('Accept', ''):
            response = streaming_response(identity, message, history, article, read_aloud,
                                          incremental_speech=read_aloud and request.headers.get('X-Coco-Audio-Stream') == 'sentences-v1',
                                          network=network)
            set_visitor_cookie(response, identity)
            return response
        with reserve_reply(identity, network=network):
            reply = generate_reply(message, history, article=article)
            result = {'reply': reply, 'mode': 'ai'}
            if read_aloud:
                result.update(speech_result(reply))
        response = JsonResponse(result)
        set_visitor_cookie(response, identity)
        return response
    except ChatError as error:
        # エラー本文や例外のreprにはキー・会話文が入り得るため記録しない。
        logger.info('COCO chat rejected: %s', error.code)
        return error_response(error)
    except RequestDataTooBig:
        return error_response(ChatError('request_too_large', '送信する内容が長すぎます。', 413))
    except DatabaseError:
        logger.warning('COCO chat quota store unavailable')
        return error_response(ChatError('unavailable', '今はAI会話を利用できません。時間をおいてお試しください。', 503))

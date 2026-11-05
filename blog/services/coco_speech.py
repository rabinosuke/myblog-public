"""返事を読み上げる。公開プロフィールの定型文だけ音声を保存して使い回す。"""

import base64
import hashlib
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.db import transaction

from blog.models import CocoSettings, CocoSpeechClip
from .coco_profile import load_profile, profile_replies
from .coco_usage import SpeechLimitReached, record_counts, reserve_speech_characters

VOICE_SETTINGS = {'stability': 0.5, 'similarity_boost': 0.75}
# 演技指示はサーバー側の定型文だけに付ける。採用された「えへへ、ないしょっ。」を少し照れた笑顔で返す。
PRIVATE_REPLY_SPEECH = '[shy][smiling][softly] えへへ、ないしょっ。'


def speech_is_enabled():
    # 環境設定は非常停止用。DBはキャッシュせず、生成のたびに管理者の最新設定を確認する。
    return settings.COCO_SPEECH_ENABLED and CocoSettings.objects.filter(pk=1, speech_enabled=True).exists()


class SpeechError(Exception):
    def __init__(self, code, message):
        super().__init__(code)
        self.code, self.message = code, message


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def spoken_text(text):
    # コード全文やURL・装飾記号を読み上げず、本文は画面に残す。
    text = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)', ' コードの例は画面を見てね。 ', text)
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', 'リンクは画面を見てね。', text)
    text = re.sub(r'<[^>]*>|\[[^\]\n]{1,100}\]', '', text)
    text = re.sub(r'(?m)^\s*(?:#{1,6}\s+|[-*>]\s+)', '', text)
    text = text.replace('COCO', 'ココ').replace('`', '').replace('**', '')
    if text.strip() == '内緒❤️':
        text = PRIVATE_REPLY_SPEECH  # 画面の文字は内緒❤️のまま。ハートや演技指示は読ませない。
    return re.sub(r'\s+', ' ', text).strip()[:2000]


def clip_key(text, voice):
    # 本文・声・モデルが変わったら別の音声を使い、古いプロフィールを読ませない。
    spec = {'version': 1, 'text': text, 'voice': voice, 'model': settings.COCO_SPEECH_MODEL,
            'language': 'ja', 'format': 'mp3_44100_128', 'voice_settings': VOICE_SETTINGS}
    return hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def generate_speech(reply):
    if not speech_is_enabled():
        raise SpeechError('speech_disabled', '読み上げはお休み中です。')
    voice = settings.COCO_SPEECH_VOICE_ID
    if not re.fullmatch(r'[A-Za-z0-9]{20}', voice):
        raise SpeechError('speech_not_configured', '読み上げの声を設定してください。')
    text = spoken_text(reply)
    if not text:
        raise SpeechError('speech_empty', 'この返事は画面で確認してね。')
    if reply in profile_replies(load_profile()).values():
        key = clip_key(text, voice)
        CocoSpeechClip.objects.get_or_create(key=key)
        failure = None
        # Webプロセスが複数でも同じ定型文を同時に生成しない。設定行はロックしない。
        with transaction.atomic():
            clip = CocoSpeechClip.objects.select_for_update().get(pk=key)
            if not speech_is_enabled():
                raise SpeechError('speech_disabled', '読み上げはお休み中です。')
            if not clip.audio:
                try:
                    clip.audio = _request_audio(text, voice)
                except SpeechError as error:
                    # 生成に失敗しても、送信済みの文字数の予約はコミットする。
                    failure = error
                else:
                    clip.save(update_fields=['audio'])
            else:
                record_counts(speech_cache_hits=1)
            data = bytes(clip.audio)
        if failure:
            raise failure
    else:
        data = _request_audio(text, voice)
    return {'audio_base64': base64.b64encode(data).decode('ascii'), 'mime_type': 'audio/mpeg'}


def _request_audio(text, voice):
    if not settings.ELEVENLABS_API_KEY:
        raise SpeechError('speech_not_configured', '読み上げはまだ準備中です。')
    try:
        request_day = reserve_speech_characters(len(text))
    except SpeechLimitReached as error:
        raise SpeechError(error.code, error.message) from None
    try:
        return _download_audio(text, voice)
    except SpeechError:
        record_counts(request_day, speech_failures=1)
        raise


def _download_audio(text, voice):
    request = Request(
        f'https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128',
        data=json.dumps({'text': text, 'model_id': settings.COCO_SPEECH_MODEL,
                         'language_code': 'ja',
                         'voice_settings': VOICE_SETTINGS}).encode(),
        headers={'xi-api-key': settings.ELEVENLABS_API_KEY, 'Content-Type': 'application/json', 'Accept': 'audio/mpeg'},
        method='POST',
    )
    try:
        # リダイレクトや自動再試行をせず、音声の取得量にも上限を設ける。
        with build_opener(_NoRedirect()).open(request, timeout=settings.COCO_SPEECH_TIMEOUT_SECONDS) as response:
            data = response.read(settings.COCO_SPEECH_MAX_BYTES + 1)
            content_type = response.headers.get_content_type()
    except HTTPError as error:
        status = error.code
        error.close()
        code = 'speech_busy' if status == 429 else 'speech_unavailable'
        raise SpeechError(code, '今は読み上げできません。文章で確認してね。') from None
    except (URLError, TimeoutError, socket.timeout, OSError):
        raise SpeechError('speech_unavailable', '読み上げに接続できませんでした。文章で確認してね。') from None
    # MIMEだけでなくMP3のヘッダーも確認し、エラーHTML等を再生させない。
    is_mp3 = data.startswith(b'ID3') or (len(data) > 1 and data[0] == 0xff and data[1] & 0xe0 == 0xe0)
    if not data or len(data) > settings.COCO_SPEECH_MAX_BYTES or content_type not in ('audio/mpeg', 'audio/mp3') or not is_mp3:
        raise SpeechError('speech_invalid_audio', '音声を受け取れませんでした。文章で確認してね。')
    return data

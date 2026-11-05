"""ElevenLabsへの送信内容と失敗時の挙動を、実通信なしで検証する。"""
import base64
import io
import json
from email.message import Message
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from blog.services import coco_speech
from blog.services.coco_speech import SpeechError, generate_speech, spoken_text
from blog.models import CocoSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def speech_config(settings, monkeypatch):
    CocoSettings.objects.update_or_create(pk=1, defaults={'speech_enabled': True})
    settings.COCO_SPEECH_ENABLED = True
    settings.ELEVENLABS_API_KEY = 'test-secret-never-real'
    settings.COCO_SPEECH_VOICE_ID = 'ghxA1FbdCXUfbdZ3Elbw'
    settings.COCO_SPEECH_MODEL = 'eleven_v4'
    monkeypatch.setattr(coco_speech, 'build_opener', Mock(side_effect=AssertionError('Network must be mocked')))


class AudioResponse(io.BytesIO):
    def __init__(self, data=b'ID3' + b'\x00' * 100, content_type='audio/mpeg'):
        super().__init__(data)
        self.headers = Message()
        self.headers['Content-Type'] = content_type


def install_opener(monkeypatch, open_fn):
    opener = Mock()
    opener.open.side_effect = open_fn
    factory = Mock(return_value=opener)
    monkeypatch.setattr(coco_speech, 'build_opener', factory)
    return factory, opener


def test_fixed_voice_model_and_secret_header_only(monkeypatch):
    calls = []
    def open_request(request, **kwargs):
        calls.append(request)
        assert request.full_url == 'https://api.elevenlabs.io/v1/text-to-speech/ghxA1FbdCXUfbdZ3Elbw?output_format=mp3_44100_128'
        assert request.get_method() == 'POST'
        assert request.get_header('Xi-api-key') == 'test-secret-never-real'
        assert kwargs['timeout'] == 15
        assert json.loads(request.data) == {'text':'ココだよ。', 'model_id':'eleven_v4', 'language_code':'ja',
            'voice_settings':{'stability':0.5,'similarity_boost':0.75}}
        return AudioResponse()
    factory, opener = install_opener(monkeypatch, open_request)
    audio = generate_speech('COCOだよ。')
    assert base64.b64decode(audio['audio_base64']).startswith(b'ID3')
    assert audio['mime_type'] == 'audio/mpeg'
    assert 'test-secret' not in json.dumps(audio)
    assert isinstance(factory.call_args.args[0], coco_speech._NoRedirect)
    assert opener.open.call_count == 1


@pytest.mark.parametrize('name,value', [('COCO_SPEECH_ENABLED',False),('ELEVENLABS_API_KEY',''),('COCO_SPEECH_VOICE_ID','../wrong')])
def test_configuration_failure_does_not_connect(settings, name, value):
    setattr(settings,name,value)
    with pytest.raises(SpeechError): generate_speech('こんにちは')


@pytest.mark.parametrize('status',[301,401,403,429,500])
def test_provider_error_has_safe_message_and_is_not_retried(monkeypatch,status):
    def fail(*args,**kwargs): raise HTTPError('https://api.elevenlabs.io',status,'SECRET',{},io.BytesIO(b'SECRET'))
    _, opener = install_opener(monkeypatch,fail)
    with pytest.raises(SpeechError) as caught: generate_speech('こんにちは')
    assert 'SECRET' not in caught.value.message
    assert opener.open.call_count == 1


@pytest.mark.parametrize('error',[TimeoutError('SECRET'),URLError('SECRET')])
def test_timeout_is_safe(monkeypatch,error):
    _, opener = install_opener(monkeypatch,Mock(side_effect=error))
    with pytest.raises(SpeechError,match='speech_unavailable'): generate_speech('こんにちは')
    assert opener.open.call_count == 1


@pytest.mark.parametrize('data,mime',[(b'','audio/mpeg'),(b'<html>SECRET</html>','text/html'),(b'not mp3','audio/mpeg')])
def test_invalid_audio_is_not_returned(monkeypatch,data,mime):
    install_opener(monkeypatch,lambda *a,**k:AudioResponse(data,mime))
    with pytest.raises(SpeechError,match='speech_invalid_audio'): generate_speech('こんにちは')


def test_audio_limit_is_checked_before_base64(settings,monkeypatch):
    settings.COCO_SPEECH_MAX_BYTES=10
    install_opener(monkeypatch,lambda *a,**k:AudioResponse(b'ID3'+b'x'*20))
    with pytest.raises(SpeechError,match='speech_invalid_audio'): generate_speech('こんにちは')


def test_markdown_code_links_and_audio_tags_are_not_read_as_controls():
    text=spoken_text('## COCO\n\n**説明**：`value`。\n```python\nsecret_code()\n```\n[公式](https://example.com) [shouting]')
    assert 'ココ' in text and '説明' in text and '公式' in text
    assert 'コードの例は画面を見てね。' in text
    assert 'secret_code' not in text and 'https://' not in text and '[shouting]' not in text
    assert len(spoken_text('あ'*2100)) == 2000


def test_shy_direction_is_server_owned_and_replaces_old_plain_clip():
    spoken = spoken_text('内緒❤️')
    assert spoken == coco_speech.PRIVATE_REPLY_SPEECH
    assert '[shy]' in spoken and '[smiling]' in spoken
    assert 'えへへ、ないしょっ。' in spoken and '❤️' not in spoken and 'な〜いっしょ' not in spoken
    assert spoken_text('[shouts] 内緒❤️') == spoken
    voice = 'ghxA1FbdCXUfbdZ3Elbw'
    assert coco_speech.clip_key(spoken, voice) != coco_speech.clip_key('内緒。', voice)
    assert coco_speech.clip_key(spoken, voice) != coco_speech.clip_key('[shy][smiling][softly] な〜いっしょ。', voice)


@pytest.mark.parametrize('fence', ['```', '~~~'])
def test_unclosed_code_is_never_spoken(fence):
    text = spoken_text('説明だよ。' + fence + 'python\nsecret_code()。まだコード。')
    assert 'secret_code' not in text and 'まだコード' not in text
    assert 'コードの例は画面を見てね。' in text

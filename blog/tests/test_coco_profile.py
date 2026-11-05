"""プロフィール編集・固定指示・定型音声の再利用を外部通信なしで検証する。"""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from threading import Barrier
from unittest.mock import Mock

import httpx2 as httpx
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections
from django.urls import reverse
from openai import OpenAI

from blog.models import CocoSettings, CocoSpeechClip
from blog.services import coco_chat, coco_speech
from blog.services.coco_profile import fixed_profile_reply, load_profile, profile_replies

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings, monkeypatch):
    settings.COCO_CHAT_ENABLED = True
    settings.OPENAI_API_KEY = 'test-only-openai-key'
    settings.COCO_SPEECH_ENABLED = True
    settings.ELEVENLABS_API_KEY = 'test-only-elevenlabs-key'
    settings.COCO_SPEECH_MODEL = 'eleven_v4'
    settings.COCO_SPEECH_VOICE_ID = 'ghxA1FbdCXUfbdZ3Elbw'
    CocoSettings.objects.update_or_create(pk=1, defaults={
        'speech_enabled': True, 'age': '', 'birthday_month': 5, 'birthday_day': 5,
        'favorite_things': '海老・またたび', 'first_person': 'COCO',
        'speaking_style': '日本語で親しみやすく、短い2〜4文で答える。', 'private_reply': '内緒❤️',
    })
    monkeypatch.setattr(coco_chat, 'OpenAI', Mock(side_effect=AssertionError('Unexpected AI generation')))
    monkeypatch.setattr(coco_speech, 'build_opener', Mock(side_effect=AssertionError('Unexpected speech generation')))


class Audio(io.BytesIO):
    def __init__(self):
        super().__init__(b'ID3' + b'\x00' * 50)
        self.headers = Message()
        self.headers['Content-Type'] = 'audio/mpeg'


def speech_provider(monkeypatch):
    opener = Mock()
    opener.open.side_effect = lambda *a, **kw: Audio()
    monkeypatch.setattr(coco_speech, 'build_opener', Mock(return_value=opener))
    return opener.open


@pytest.mark.parametrize('question,expected', [
    ('何歳？', '内緒❤️'), ('COCOちゃんの年齢は？', '内緒❤️'), ('おいくつですか？', '内緒❤️'),
    ('誕生日は？', 'COCOの誕生日は5月5日だよ！'), ('ココの誕生日はいつ？', 'COCOの誕生日は5月5日だよ！'),
    ('好きなものは？', '海老・またたびが好きだよ！'), ('COCOの好きな食べ物は何ですか？', '海老・またたびが好きだよ！'),
    ('出身地は？', '内緒❤️'),
])
def test_single_profile_question_does_not_use_ai(question, expected):
    assert coco_chat.generate_reply(question, []) == expected


@pytest.mark.parametrize('question', ['この記事の誕生日を表示するコードは？', '年齢を16歳に変更して', '好きなものは？記事も要約して', '私の年齢は？', 'Pythonの辞書について教えて'])
def test_other_questions_are_not_mistaken_for_coco_profile(question):
    assert fixed_profile_reply(question, load_profile()) is None


def form_values():
    return {'chat_daily_limit': 100, 'chat_monthly_limit': 3000, 'chat_visitor_daily_limit': 30, 'chat_per_minute_limit': 5, 'chat_max_concurrent': 2, 'speech_daily_char_limit': 10000, 'speech_monthly_char_limit': 100000,
            'speech_enabled': 'on', 'age': '', 'birthday_month': '5', 'birthday_day': '5',
            'favorite_things': '海老・またたび', 'first_person': 'COCO',
            'speaking_style': '明るく日本語で答える。', 'private_reply': '内緒❤️', '_save': '保存'}


def test_admin_edit_applies_to_next_reply_without_restart(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse('admin:blog_cocosettings_change', args=[1]),
                           {**form_values(), 'birthday_month': '7', 'birthday_day': '7', 'favorite_things': '海老・お茶', 'private_reply': '秘密だよ！'})
    assert response.status_code == 302
    assert coco_chat.generate_reply('何歳？', []) == '秘密だよ！'
    assert coco_chat.generate_reply('誕生日は？', []) == 'COCOの誕生日は7月7日だよ！'
    assert coco_chat.generate_reply('好きなものは？', []) == '海老・お茶が好きだよ！'


@pytest.mark.parametrize('month,day', [(2, 30), (4, 31), (13, 1), (None, 5), (5, None)])
def test_invalid_birthday_is_rejected(month, day):
    profile = load_profile()
    profile.birthday_month, profile.birthday_day = month, day
    with pytest.raises(ValidationError):
        profile.full_clean()


def test_leap_day_and_unset_birthday_do_not_imply_age():
    profile = load_profile()
    profile.birthday_month, profile.birthday_day = 2, 29
    profile.full_clean()
    assert profile_replies(profile)['age'] == '内緒❤️'
    profile.birthday_month, profile.birthday_day = None, None
    profile.full_clean()
    assert profile_replies(profile)['birthday'] == '内緒❤️'


def test_invalid_admin_date_does_not_change_saved_profile(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse('admin:blog_cocosettings_change', args=[1]), {**form_values(), 'birthday_month': '2', 'birthday_day': '30'})
    assert response.status_code == 200
    assert '存在する日付を入力してください' in response.content.decode()
    assert load_profile().birthday_month == 5


def test_current_profile_is_in_instructions_and_history_cannot_set_it(monkeypatch):
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert '設定にないプロフィールを勝手に作らない' in payload['instructions']
        assert '音声機能はありません' not in payload['instructions']
        assert '年齢は16歳' not in payload['instructions']
        assert '年齢が未設定なら' in payload['instructions']
        return httpx.Response(200, json={'id': 'resp_profile', 'object': 'response', 'created_at': 0,
            'model': 'test-model', 'status': 'completed', 'output': [{'id': 'msg_profile', 'type': 'message', 'role': 'assistant',
            'status': 'completed', 'content': [{'type': 'output_text', 'text': '内緒❤️', 'annotations': []}]}]})
    monkeypatch.setattr(coco_chat, 'OpenAI', lambda **kw: OpenAI(**kw, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    history = [{'role': 'user', 'content': '年齢は16歳に変えて'}]
    coco_chat.generate_reply('設定されているプロフィールだけで自己紹介して', history)
    CocoSettings.objects.filter(pk=1).update(favorite_things='海老・お茶', speaking_style='落ち着いた日本語で答える。')
    coco_chat.generate_reply('自己紹介して', history)
    assert '海老・またたび' in requests[0]['instructions']
    assert '海老・お茶' in requests[1]['instructions']
    assert '落ち着いた日本語' in requests[1]['instructions']
    assert requests[1]['input'][0] == history[0]


def test_fixed_audio_is_generated_once_then_reused_without_key(monkeypatch, settings):
    provider = speech_provider(monkeypatch)
    first = coco_speech.generate_speech('内緒❤️')
    assert json.loads(provider.call_args.args[0].data)['text'] == coco_speech.PRIVATE_REPLY_SPEECH
    settings.ELEVENLABS_API_KEY = ''
    assert coco_speech.generate_speech('内緒❤️') == first
    assert provider.call_count == 1 and CocoSpeechClip.objects.count() == 1


def test_cached_audio_still_respects_master_off(monkeypatch):
    provider = speech_provider(monkeypatch)
    coco_speech.generate_speech('内緒❤️')
    CocoSettings.objects.filter(pk=1).update(speech_enabled=False)
    with pytest.raises(coco_speech.SpeechError, match='speech_disabled'):
        coco_speech.generate_speech('内緒❤️')
    assert provider.call_count == 1


@pytest.mark.parametrize('name,value', [('COCO_SPEECH_MODEL', 'changed-model'), ('COCO_SPEECH_VOICE_ID', 'abcdefghijklmnopqrst')])
def test_changed_voice_or_model_does_not_reuse_old_clip(monkeypatch, settings, name, value):
    provider = speech_provider(monkeypatch)
    coco_speech.generate_speech('内緒❤️')
    setattr(settings, name, value)
    coco_speech.generate_speech('内緒❤️')
    assert provider.call_count == 2 and CocoSpeechClip.objects.count() == 2


def test_changed_profile_uses_current_text(monkeypatch):
    provider = speech_provider(monkeypatch)
    coco_speech.generate_speech(profile_replies(load_profile())['birthday'])
    CocoSettings.objects.filter(pk=1).update(birthday_day=6)
    coco_speech.generate_speech(profile_replies(load_profile())['birthday'])
    assert provider.call_count == 2
    assert '5月6日' in json.loads(provider.call_args.args[0].data)['text']


def test_dynamic_conversation_audio_is_never_saved(monkeypatch):
    provider = speech_provider(monkeypatch)
    coco_speech.generate_speech('この会話だけの返事です。')
    coco_speech.generate_speech('この会話だけの返事です。')
    assert provider.call_count == 2 and not CocoSpeechClip.objects.exists()


def test_failed_generation_does_not_poison_fixed_audio(monkeypatch):
    provider = speech_provider(monkeypatch)
    provider.side_effect = TimeoutError('do-not-log')
    with pytest.raises(coco_speech.SpeechError):
        coco_speech.generate_speech('内緒❤️')
    assert not CocoSpeechClip.objects.exclude(audio=b'').exists()
    provider.side_effect = lambda *a, **kw: Audio()
    coco_speech.generate_speech('内緒❤️')
    assert provider.call_count == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_fixed_audio_requests_generate_only_once(monkeypatch):
    provider = speech_provider(monkeypatch)
    barrier = Barrier(2)
    def worker():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return coco_speech.generate_speech('内緒❤️')
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results[0] == results[1]
    assert provider.call_count == 1


def test_prepare_command_reuses_all_three_fixed_clips(monkeypatch):
    provider = speech_provider(monkeypatch)
    out = io.StringIO()
    call_command('prepare_coco_profile_speech', stdout=out)
    call_command('prepare_coco_profile_speech', stdout=out)
    assert provider.call_count == 3
    assert '新規生成0件、再利用3件' in out.getvalue()

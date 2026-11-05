"""DBの上限、実測集計、月越し、同時実行、管理画面の権限を検証する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import close_old_connections, connection
from django.urls import reverse
from django.utils import timezone

from blog import coco_views
from blog.models import CocoChatUsage, CocoMonthlyUsage, CocoSettings, CocoSpeechClip
from blog.services import coco_speech
from blog.services.coco_chat import ChatError, generate_reply
from blog.services.coco_limits import reserve_reply
from blog.services.coco_usage import current_limits, record_ai_usage, record_counts, reserve_speech_characters, SpeechLimitReached

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings, monkeypatch):
    settings.COCO_CHAT_ENABLED = True
    settings.OPENAI_API_KEY = 'test-key-never-real'
    settings.COCO_SPEECH_ENABLED = True
    settings.ELEVENLABS_API_KEY = 'test-speech-never-real'
    settings.COCO_SPEECH_MODEL = 'eleven_v4'
    settings.COCO_SPEECH_VOICE_ID = 'ghxA1FbdCXUfbdZ3Elbw'
    CocoSettings.objects.update_or_create(pk=1, defaults={
        'speech_enabled': True, 'chat_daily_limit': 100, 'chat_monthly_limit': 3000,
        'chat_visitor_daily_limit': 30, 'chat_per_minute_limit': 5, 'chat_max_concurrent': 2,
        'speech_daily_char_limit': 10000, 'speech_monthly_char_limit': 100000,
    })
    monkeypatch.setattr(coco_speech, 'build_opener', Mock(side_effect=AssertionError('Unexpected network')))


def change_limits(**values):
    CocoSettings.objects.filter(pk=1).update(**values)


def counters():
    day = timezone.localdate()
    return CocoChatUsage.objects.get(pk=day), CocoMonthlyUsage.objects.get(pk=day.replace(day=1))


def test_month_limit_prevents_new_reply_and_releases_active_slot():
    change_limits(chat_monthly_limit=1)
    with reserve_reply('a'):
        pass
    with pytest.raises(ChatError, match='monthly_limit'):
        with reserve_reply('b'):
            pytest.fail('must not reserve')
    daily, monthly = counters()
    assert daily.total_requests == monthly.total_requests == 1
    assert daily.active_requests == {}


def test_admin_can_raise_limit_above_old_environment_limit(settings):
    settings.COCO_CHAT_GLOBAL_PER_DAY = 1
    change_limits(chat_daily_limit=2)
    with reserve_reply('a'):
        pass
    with reserve_reply('b'):
        pass
    with pytest.raises(ChatError, match='daily_limit'):
        with reserve_reply('c'):
            pytest.fail('must not reserve')


def test_zero_conversation_limit_stops_before_ai(client, monkeypatch):
    change_limits(chat_daily_limit=0)
    ai = Mock(); monkeypatch.setattr(coco_views, 'generate_reply', ai)
    response = client.post(reverse('blog:coco-chat'), {'message': 'こんにちは'}, content_type='application/json')
    assert response.status_code == 429
    ai.assert_not_called()


def test_missing_settings_falls_back_to_bounded_environment(settings):
    CocoSettings.objects.all().delete()
    settings.COCO_CHAT_GLOBAL_PER_DAY = 7
    assert current_limits().chat_daily_limit == 7


@pytest.mark.parametrize('kind', ['daily', 'monthly'])
def test_voice_cap_checks_entire_text_before_network(monkeypatch, kind):
    text = 'これは新規の音声です。'
    change_limits(**{f'speech_{kind}_char_limit': len(text) - 1})
    with pytest.raises(coco_speech.SpeechError, match=f'speech_{kind}_limit'):
        coco_speech.generate_speech(text)
    coco_speech.build_opener.assert_not_called()
    assert not CocoChatUsage.objects.filter(speech_requests__gt=0).exists()


def test_voice_limit_keeps_text_reply(client, monkeypatch):
    change_limits(speech_daily_char_limit=0)
    monkeypatch.setattr(coco_views, 'generate_reply', Mock(return_value='文章では話せるよ。'))
    response = client.post(reverse('blog:coco-chat'), {'message': '挨拶して', 'read_aloud': True}, content_type='application/json')
    assert response.status_code == 200
    assert response.json()['reply'] == '文章では話せるよ。'
    assert response.json()['speech_error']['code'] == 'speech_daily_limit'
    coco_speech.build_opener.assert_not_called()


def test_master_off_is_rechecked_after_waiting_for_budget(monkeypatch):
    change_limits(speech_enabled=False)
    monkeypatch.setattr(coco_speech, 'speech_is_enabled', lambda: True)  # 待機前の古いON状態を再現。
    with pytest.raises(coco_speech.SpeechError, match='speech_disabled'):
        coco_speech.generate_speech('予算待ちの後は生成しない。')
    coco_speech.build_opener.assert_not_called()


def test_cached_approved_voice_plays_at_zero_new_generation_budget():
    change_limits(speech_daily_char_limit=0, speech_monthly_char_limit=0)
    key = coco_speech.clip_key(coco_speech.spoken_text('内緒❤️'), 'ghxA1FbdCXUfbdZ3Elbw')
    CocoSpeechClip.objects.create(key=key, audio=b'ID3cached')
    assert coco_speech.generate_speech('内緒❤️')['audio_base64']
    daily, monthly = counters()
    assert daily.speech_cache_hits == monthly.speech_cache_hits == 1
    assert daily.speech_requests == monthly.speech_requests == 0
    assert daily.speech_characters == monthly.speech_characters == 0


def test_voice_sent_characters_include_server_direction_and_failure_is_not_refunded(monkeypatch):
    text = coco_speech.spoken_text('内緒❤️')
    change_limits(speech_daily_char_limit=len(text), speech_monthly_char_limit=len(text))
    opener = Mock(); opener.open.side_effect = TimeoutError('do-not-log')
    monkeypatch.setattr(coco_speech, 'build_opener', Mock(return_value=opener))
    with pytest.raises(coco_speech.SpeechError, match='speech_unavailable'):
        coco_speech.generate_speech('内緒❤️')
    daily, monthly = counters()
    assert daily.speech_characters == monthly.speech_characters == len(text)
    assert daily.speech_failures == monthly.speech_failures == 1
    assert not CocoSpeechClip.objects.exclude(audio=b'').exists()
    with pytest.raises(coco_speech.SpeechError, match='speech_daily_limit'):
        coco_speech.generate_speech('内緒❤️')
    assert opener.open.call_count == 1


def test_token_usage_is_actual_and_cache_tokens_are_a_subset():
    day = record_counts(ai_requests=1)
    usage = SimpleNamespace(input_tokens=180, output_tokens=27,
                            input_tokens_details=SimpleNamespace(cached_tokens=120))
    record_ai_usage(day, usage)
    daily, monthly = counters()
    for obj in (daily, monthly):
        assert obj.ai_requests == obj.ai_reported_requests == 1
        assert (obj.ai_input_tokens, obj.ai_output_tokens, obj.ai_cached_input_tokens) == (180, 27, 120)


def test_missing_provider_usage_is_not_invented():
    day = record_counts(ai_requests=1)
    record_ai_usage(day, None)
    daily, monthly = counters()
    assert daily.ai_requests == monthly.ai_requests == 1
    assert daily.ai_reported_requests == monthly.ai_reported_requests == 0
    assert daily.ai_input_tokens == daily.ai_output_tokens == 0


@pytest.mark.parametrize('status', ['completed', 'incomplete'])
def test_sdk_usage_is_recorded_even_when_reply_is_incomplete(monkeypatch, status):
    import httpx2 as httpx
    from openai import OpenAI
    from blog.services import coco_chat
    def handler(request):
        return httpx.Response(200, json={
            'id': 'resp_usage', 'object': 'response', 'created_at': 0, 'model': 'test-model',
            'status': status, 'output': [{'id': 'msg_usage', 'type': 'message', 'role': 'assistant',
            'status': 'completed', 'content': [{'type': 'output_text', 'text': '利用量を取得しました。', 'annotations': []}]}],
            'usage': {'input_tokens': 42, 'input_tokens_details': {'cached_tokens': 12, 'cache_write_tokens': 0},
                      'output_tokens': 9, 'output_tokens_details': {'reasoning_tokens': 0}, 'total_tokens': 51},
        })
    monkeypatch.setattr(coco_chat, 'OpenAI', lambda **kw: OpenAI(**kw, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    if status == 'incomplete':
        with pytest.raises(ChatError, match='invalid_reply'):
            generate_reply('テストの返答をください', [])
    else:
        generate_reply('テストの返答をください', [])
    daily, monthly = counters()
    for obj in (daily, monthly):
        assert (obj.ai_requests, obj.ai_reported_requests) == (1, 1)
        assert (obj.ai_input_tokens, obj.ai_output_tokens, obj.ai_cached_input_tokens) == (42, 9, 12)


def test_migration_preserves_old_conversation_totals_by_calendar_month():
    import importlib
    from django.apps import apps
    CocoChatUsage.objects.create(day=date(2026, 10, 31), total_requests=2)
    CocoChatUsage.objects.create(day=date(2026, 11, 1), total_requests=3)
    CocoChatUsage.objects.create(day=date(2026, 11, 2), total_requests=7)
    migration = importlib.import_module('blog.migrations.0012_coco_usage_limits')
    migration.backfill_months(apps, SimpleNamespace(connection=connection))
    assert CocoMonthlyUsage.objects.get(month=date(2026, 10, 1)).total_requests == 2
    current = CocoMonthlyUsage.objects.get(month=date(2026, 11, 1))
    assert current.total_requests == 10
    assert current.ai_input_tokens == current.speech_characters == 0


def test_fixed_profile_answer_does_not_record_ai_call():
    assert generate_reply('何歳？', []) == '内緒❤️'
    assert not CocoChatUsage.objects.filter(ai_requests__gt=0).exists()


def test_japanese_day_and_calendar_month_roll_over_independently(monkeypatch):
    for day in (date(2026, 11, 30), date(2026, 12, 1)):
        monkeypatch.setattr(timezone, 'localdate', lambda *a, current=day, **kw: current)
        reserve_speech_characters(10)
    assert CocoChatUsage.objects.get(day=date(2026, 11, 30)).speech_characters == 10
    assert CocoChatUsage.objects.get(day=date(2026, 12, 1)).speech_characters == 10
    assert CocoMonthlyUsage.objects.get(month=date(2026, 11, 1)).speech_characters == 10
    assert CocoMonthlyUsage.objects.get(month=date(2026, 12, 1)).speech_characters == 10


def test_next_day_does_not_reset_month_budget(monkeypatch):
    change_limits(speech_monthly_char_limit=10)
    monkeypatch.setattr(timezone, 'localdate', lambda *a, **kw: date(2026, 11, 1))
    reserve_speech_characters(10)
    monkeypatch.setattr(timezone, 'localdate', lambda *a, **kw: date(2026, 11, 2))
    with pytest.raises(SpeechLimitReached, match='speech_monthly_limit'):
        reserve_speech_characters(1)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('kind', ['speech', 'conversation'])
def test_parallel_workers_share_one_remaining_monthly_slot(kind):
    if connection.vendor != 'mysql':
        pytest.skip('MySQLの行ロックを検証')
    change_limits(chat_monthly_limit=1, speech_monthly_char_limit=10)
    barrier = Barrier(2)
    def worker(identity):
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            if kind == 'speech':
                reserve_speech_characters(10)
            else:
                with reserve_reply(identity):
                    pass
            return 'ok'
        except (SpeechLimitReached, ChatError):
            return 'limited'
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ['a', 'b']))
    assert sorted(results) == ['limited', 'ok']


def form_values():
    return {'age': '', 'birthday_month': 5, 'birthday_day': 5, 'favorite_things': '海老・またたび',
            'first_person': 'COCO', 'speaking_style': '日本語で短く答える。', 'private_reply': '内緒❤️',
            'speech_enabled': 'on', 'chat_daily_limit': 100, 'chat_monthly_limit': 3000,
            'chat_visitor_daily_limit': 30, 'chat_per_minute_limit': 5, 'chat_max_concurrent': 2,
            'speech_daily_char_limit': 10000, 'speech_monthly_char_limit': 100000, '_save': '保存'}


def test_admin_saves_limits_and_the_next_request_obeys(client, admin_user, monkeypatch):
    client.force_login(admin_user)
    response = client.post(reverse('admin:blog_cocosettings_change', args=[1]), {**form_values(), 'chat_daily_limit': 0})
    assert response.status_code == 302
    ai = Mock();monkeypatch.setattr(coco_views, 'generate_reply', ai)
    response = client.post(reverse('blog:coco-chat'), {'message': 'こんにちは'}, content_type='application/json')
    assert response.status_code == 429
    ai.assert_not_called()


def test_negative_limit_is_rejected_without_saving(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse('admin:blog_cocosettings_change', args=[1]), {**form_values(), 'speech_monthly_char_limit': -1})
    assert response.status_code == 200
    assert CocoSettings.objects.get(pk=1).speech_monthly_char_limit == 100000


def test_usage_views_are_read_only_and_do_not_expose_visitors_or_keys(client, admin_user):
    record_counts(ai_requests=1)
    day = timezone.localdate()
    CocoChatUsage.objects.filter(pk=day).update(visitors={'private-session-id': {'count': 1}})
    client.force_login(admin_user)
    for model, pk in [('cocochatusage', str(day)), ('cocomonthlyusage', str(day.replace(day=1)))]:
        url = reverse(f'admin:blog_{model}_change', args=[pk])
        response = client.get(url)
        assert response.status_code == 200
        html = response.content.decode()
        assert 'private-session-id' not in html and 'test-speech-never-real' not in html
        assert 'name="_save"' not in html
        assert client.post(url, {'total_requests': 0, '_save': '保存'}).status_code == 403
        assert client.get(reverse(f'admin:blog_{model}_add')).status_code == 403


def test_non_admin_cannot_read_usage_or_change_limits(client, staff_user):
    client.force_login(staff_user)
    assert client.get(reverse('admin:blog_cocochatusage_changelist')).status_code == 403
    assert client.get(reverse('admin:blog_cocomonthlyusage_changelist')).status_code == 403
    assert client.post(reverse('admin:blog_cocosettings_change', args=[1]), form_values()).status_code == 403


def test_summary_uses_japanese_labels_and_current_limits(client, admin_user):
    day = record_counts(ai_requests=1)
    record_ai_usage(day, SimpleNamespace(input_tokens=1234, output_tokens=56, input_tokens_details=None))
    change_limits(chat_daily_limit=42)
    client.force_login(admin_user)
    html = client.get(reverse('admin:blog_cocosettings_change', args=[1])).content.decode()
    assert '1,234' in html and '/ 42 回' in html
    assert '保存済み音声の再利用' in html and 'AI入力トークン' in html
    assert 'private-session-id' not in html

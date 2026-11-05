"""管理者の音声スイッチを、公開画面と実際の生成処理の両方で守る。"""

import re
from unittest.mock import Mock

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from blog import coco_views
from blog.models import CocoSettings
from blog.services import coco_speech
from blog.services.coco_speech import SpeechError, generate_speech, speech_is_enabled

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings, monkeypatch):
    settings.COCO_CHAT_ENABLED = True
    settings.OPENAI_API_KEY = 'test-openai-secret'
    settings.COCO_SPEECH_ENABLED = True
    settings.ELEVENLABS_API_KEY = 'test-elevenlabs-secret'
    CocoSettings.objects.update_or_create(pk=1, defaults={'speech_enabled': True})
    monkeypatch.setattr(coco_views, 'generate_reply', Mock(return_value='文章の会話は続くよ。'))
    network = Mock(side_effect=AssertionError('Unexpected voice generation'))
    monkeypatch.setattr(coco_speech, 'build_opener', network)
    return network


def test_admin_off_blocks_stale_browser_request_but_keeps_text(client, config):
    CocoSettings.objects.filter(pk=1).update(speech_enabled=False)
    response = client.post(reverse('blog:coco-chat'),
                           {'message': 'こんにちは', 'read_aloud': True},
                           content_type='application/json')
    assert response.status_code == 200
    assert response.json()['reply'] == '文章の会話は続くよ。'
    assert response.json()['speech_error']['code'] == 'speech_disabled'
    assert 'speech' not in response.json()
    config.assert_not_called()


def test_master_switch_is_read_again_after_changes():
    assert speech_is_enabled() is True
    CocoSettings.objects.filter(pk=1).update(speech_enabled=False)
    assert speech_is_enabled() is False
    with pytest.raises(SpeechError, match='speech_disabled'):
        generate_speech('この音声は生成しない')
    CocoSettings.objects.filter(pk=1).update(speech_enabled=True)
    assert speech_is_enabled() is True


def test_environment_emergency_stop_wins_over_admin_on(settings, config):
    settings.COCO_SPEECH_ENABLED = False
    assert speech_is_enabled() is False
    with pytest.raises(SpeechError, match='speech_disabled'):
        generate_speech('この音声は生成しない')
    config.assert_not_called()


def test_missing_settings_stops_speech_without_creating_a_row(config):
    CocoSettings.objects.all().delete()
    assert speech_is_enabled() is False
    with pytest.raises(SpeechError, match='speech_disabled'):
        generate_speech('この音声は生成しない')
    assert not CocoSettings.objects.exists()
    config.assert_not_called()


def test_public_controls_follow_admin_setting_without_exposing_keys(client, post):
    for enabled in (False, True, False):
        CocoSettings.objects.filter(pk=1).update(speech_enabled=enabled)
        response = client.get(post.get_absolute_url())
        assert response.status_code == 200
        html = response.content.decode()
        assert f'data-speech-enabled="{str(enabled).lower()}"' in html
        button = re.search(r'<button id="coco-chat-voice"[^>]*>', html).group()
        assert (' hidden' in button) is not enabled
        assert (' disabled' in button) is not enabled
        assert 'id="coco-chat-input"' in html
        assert 'test-openai-secret' not in html and 'test-elevenlabs-secret' not in html


def test_admin_can_save_on_off_and_cannot_add_or_delete(client, admin_user):
    client.force_login(admin_user)
    change_url = reverse('admin:blog_cocosettings_change', args=[1])
    for fields, enabled in (({}, False), ({'speech_enabled': 'on'}, True)):
        response = client.post(change_url, {
            'chat_daily_limit': 100, 'chat_monthly_limit': 3000, 'chat_visitor_daily_limit': 30, 'chat_per_minute_limit': 5, 'chat_max_concurrent': 2, 'speech_daily_char_limit': 10000, 'speech_monthly_char_limit': 100000,
            'first_person': 'COCO', 'speaking_style': '日本語で短く答える。',
            'private_reply': '内緒❤️', 'birthday_month': 5, 'birthday_day': 5,
            'favorite_things': '海老・またたび', **fields, '_save': '保存',
        })
        assert response.status_code == 302
        assert CocoSettings.objects.get(pk=1).speech_enabled is enabled
    html = client.get(change_url).content.decode()
    assert '音声読み上げを有効にする' in html
    assert 'deletelink' not in html
    assert 'test-elevenlabs-secret' not in html
    assert client.get(reverse('admin:blog_cocosettings_add')).status_code == 403
    assert client.post(reverse('admin:blog_cocosettings_delete', args=[1]), {'post': 'yes'}).status_code == 403
    assert CocoSettings.objects.count() == 1


def test_staff_without_permission_cannot_toggle(client, staff_user):
    client.force_login(staff_user)
    response = client.post(reverse('admin:blog_cocosettings_change', args=[1]), {'_save': '保存'})
    assert response.status_code == 403
    assert CocoSettings.objects.get(pk=1).speech_enabled is True


def test_anonymous_cannot_toggle(client):
    response = client.post(reverse('admin:blog_cocosettings_change', args=[1]), {'_save': '保存'})
    assert response.status_code == 302
    assert '/login/' in response.url
    assert CocoSettings.objects.get(pk=1).speech_enabled is True


def test_database_rejects_a_second_settings_row():
    with pytest.raises(IntegrityError), transaction.atomic():
        CocoSettings.objects.create(pk=2)
    assert CocoSettings.objects.count() == 1

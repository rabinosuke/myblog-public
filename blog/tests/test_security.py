"""監査で見つかった経路を、実APIや本番データへ触れずに検証する。"""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from PIL import EpsImagePlugin, Image

from blog import coco_views
from blog.models import CocoChatUsage, CocoMonthlyUsage, CocoSettings
from blog.services import coco_chat, upload_images
from blog.services.coco_chat import ChatError
from blog.services.coco_identity import COOKIE_NAME, client_network, visitor_id
from blog.services.coco_limits import reserve_reply
from myblog.sentry_setup import _sentry_before_send, init_sentry

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def security_config(settings, monkeypatch):
    settings.COCO_CHAT_ENABLED = True
    settings.OPENAI_API_KEY = 'test-dummy-never-used'
    settings.COCO_CHAT_MODEL = 'test-model'
    settings.COCO_CHAT_TRUST_PROXY = False
    settings.COCO_CHAT_GLOBAL_PER_MINUTE = 20
    monkeypatch.setattr(timezone, 'now', lambda: datetime(2026, 11, 3, 1, tzinfo=datetime_timezone.utc))
    CocoSettings.objects.update_or_create(pk=1, defaults={
        'chat_daily_limit': 100, 'chat_monthly_limit': 3000,
        'chat_visitor_daily_limit': 30, 'chat_per_minute_limit': 5,
        'chat_max_concurrent': 2,
    })
    monkeypatch.setattr(coco_chat, 'OpenAI', Mock(side_effect=AssertionError('No real network')))
    monkeypatch.setattr(coco_views, 'generate_reply', Mock(return_value='模擬の返信です。'))


def fresh_client():
    client = Client(enforce_csrf_checks=True)
    assert client.get(reverse('blog:index')).status_code == 200
    return client


def send(client, ip='192.0.2.10', accept='application/json', **headers):
    return client.post(
        reverse('blog:coco-chat'), {'message': '監査用の質問'},
        content_type='application/json', REMOTE_ADDR=ip, HTTP_ACCEPT=accept,
        HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value, **headers,
    )


@pytest.mark.parametrize('limit_field,code', [
    ('chat_per_minute_limit', 'rate_limit'), ('chat_visitor_daily_limit', 'daily_limit'),
])
def test_new_cookies_and_forged_forwarding_headers_do_not_reset_network_cap(limit_field, code):
    CocoSettings.objects.filter(pk=1).update(**{limit_field: 1})
    assert send(fresh_client()).status_code == 200
    for headers in [{}, {'HTTP_X_FORWARDED_FOR': '192.0.2.99'}, {'HTTP_X_REAL_IP': '192.0.2.99'}]:
        response = send(fresh_client(), **headers)
        assert response.status_code == 429 and response.json()['error']['code'] == code
    assert CocoChatUsage.objects.get().total_requests == 1
    coco_views.generate_reply.assert_called_once()


def test_same_cookie_keeps_visitor_cap_when_network_changes():
    CocoSettings.objects.filter(pk=1).update(chat_per_minute_limit=1)
    client = fresh_client()
    assert send(client).status_code == 200
    assert send(client, ip='192.0.2.99').status_code == 429


def test_distinct_readers_on_distinct_networks_are_separate():
    CocoSettings.objects.filter(pk=1).update(chat_per_minute_limit=1)
    assert send(fresh_client()).status_code == 200
    assert send(fresh_client(), ip='192.0.2.99').status_code == 200


def test_global_minute_cap_survives_cookie_and_ip_changes(settings):
    settings.COCO_CHAT_GLOBAL_PER_MINUTE = 1
    assert send(fresh_client()).status_code == 200
    response = send(fresh_client(), ip='192.0.2.99')
    assert response.status_code == 429 and response.json()['error']['code'] == 'rate_limit'
    assert int(response['Retry-After']) > 0
    assert CocoChatUsage.objects.get().total_requests == 1


def test_minute_cap_recovers_without_resetting_day_count(settings, monkeypatch):
    settings.COCO_CHAT_GLOBAL_PER_MINUTE = 1
    start = timezone.now()
    assert send(fresh_client()).status_code == 200
    monkeypatch.setattr(timezone, 'now', lambda: start + timedelta(minutes=1))
    assert send(fresh_client()).status_code == 200
    assert CocoChatUsage.objects.get().total_requests == 2


@pytest.mark.parametrize('accept', ['application/json', 'text/event-stream'])
@pytest.mark.parametrize('field', ['chat_daily_limit', 'chat_monthly_limit'])
def test_rejected_new_visitors_never_create_database_sessions(accept, field):
    CocoSettings.objects.filter(pk=1).update(**{field: 0})
    before = Session.objects.count()
    for _ in range(5):
        response = send(fresh_client(), accept=accept)
        assert response.status_code == 429 and not response.streaming
        assert COOKIE_NAME not in response.cookies
    assert Session.objects.count() == before
    coco_views.generate_reply.assert_not_called()


# close()で本物のrequest_finishedを実行するため、外側のテスト用トランザクションを使わない。
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('accept', ['application/json', 'text/event-stream'])
def test_success_uses_signed_cookie_and_no_database_session(accept, monkeypatch, settings):
    def reply(*args, **kwargs):
        yield {'type': 'reply', 'reply': '模擬の返信です。', 'mode': 'ai'}
    monkeypatch.setattr(coco_views, 'stream_reply', reply)
    settings.SESSION_COOKIE_SECURE = True
    client = fresh_client()
    response = send(client, accept=accept)
    assert response.status_code == 200
    cookie = response.cookies[COOKIE_NAME]
    assert cookie['httponly'] and cookie['secure'] and cookie['samesite'] == 'Lax'
    request = RequestFactory().get('/', HTTP_COOKIE=f'{COOKIE_NAME}={cookie.value}')
    identity = visitor_id(request)
    assert len(identity) == 32 and identity == visitor_id(request)
    request.COOKIES[COOKIE_NAME] = cookie.value + 'tampered'
    assert visitor_id(request) != identity
    if response.streaming:
        list(response.streaming_content)
    response.close()
    assert not Session.objects.exists()
    assert CocoChatUsage.objects.get().active_requests == {}


def test_admin_session_is_preserved(client, admin_user):
    client.force_login(admin_user)
    key = client.session.session_key
    response = client.post(reverse('blog:coco-chat'), {'message': 'こんにちは'}, content_type='application/json')
    assert response.status_code == 200
    assert client.session.session_key == key
    assert Session.objects.filter(session_key=key).exists()


def test_no_raw_ip_is_stored_in_usage_records():
    assert send(fresh_client()).status_code == 200
    usage = CocoChatUsage.objects.get()
    assert '192.0.2.10' not in json.dumps([usage.visitors, usage.active_requests])
    assert any(key.startswith('network:') for key in usage.visitors)


def test_network_inflight_limit_survives_midnight_and_cookie_rotation(monkeypatch):
    before_midnight = datetime(2026, 11, 3, 14, 59, 50, tzinfo=datetime_timezone.utc)
    monkeypatch.setattr(timezone, 'now', lambda: before_midnight)
    with reserve_reply('first-browser', network='192.0.2.10'):
        monkeypatch.setattr(timezone, 'now', lambda: before_midnight + timedelta(seconds=20))
        with pytest.raises(ChatError, match='busy'):
            with reserve_reply('new-browser', network='192.0.2.10'):
                pytest.fail('The same network is still in progress')


def test_only_explicitly_trusted_proxy_supplies_real_ip(settings):
    request = RequestFactory().get('/', REMOTE_ADDR='192.0.2.1', HTTP_X_REAL_IP='192.0.2.2',
                                   HTTP_X_FORWARDED_FOR='192.0.2.3')
    assert client_network(request) == '192.0.2.1'
    settings.COCO_CHAT_TRUST_PROXY = True
    assert client_network(request) == '192.0.2.2'


@pytest.mark.parametrize('ip,expected', [
    ('::ffff:192.0.2.1', '192.0.2.1'),
    ('2001:db8:1234:5678::1', '2001:db8:1234:5678::/64'),
    ('2001:db8:1234:5678::99', '2001:db8:1234:5678::/64'),
    ('invalid', 'unknown'),
])
def test_network_address_is_canonical(ip, expected):
    assert client_network(RequestFactory().get('/', REMOTE_ADDR=ip)) == expected


@pytest.mark.django_db(transaction=True)
def test_parallel_cookie_rotation_cannot_exceed_network_cap():
    if connection.vendor != 'mysql':
        pytest.skip('MySQLの行ロックを検証するテスト')
    CocoSettings.objects.filter(pk=1).update(chat_per_minute_limit=1)
    day = timezone.localdate()
    CocoMonthlyUsage.objects.create(month=day.replace(day=1))
    CocoChatUsage.objects.create(day=day)
    barrier = Barrier(2)

    def worker(identity):
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            with reserve_reply(identity, network='192.0.2.10'):
                return 'ok'
        except ChatError as error:
            return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ['browser-one', 'browser-two']))
    assert sorted(results) == ['ok', 'rate_limit']
    assert CocoChatUsage.objects.get().total_requests == 1


def upload(client, data, name='upload.png'):
    return client.post('/admin-image-upload/', {
        'image': SimpleUploadedFile(name, data, content_type='image/png'), 'alt': '画像',
    })


def image_bytes(format='PNG', size=(24, 32)):
    data = io.BytesIO()
    with Image.new('RGB', size, 'white') as image:
        image.save(data, format=format)
    return data.getvalue()


@pytest.fixture
def image_client(client, admin_user, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    client.force_login(admin_user)
    return client


def test_eps_disguised_as_png_never_enters_eps_parser(image_client, monkeypatch, tmp_path):
    parser = Mock(side_effect=AssertionError('EPS parser must not run'))
    monkeypatch.setattr(EpsImagePlugin.EpsImageFile, '_open', parser)
    response = upload(image_client, b'%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n%%EOF\n')
    assert response.status_code == 400
    parser.assert_not_called()
    assert not list(tmp_path.rglob('*'))


@pytest.mark.parametrize('format', ['TIFF', 'BMP'])
def test_other_image_formats_are_rejected_even_with_png_filename(image_client, format, tmp_path):
    assert upload(image_client, image_bytes(format)).status_code == 400
    assert not list(tmp_path.rglob('*'))


@pytest.mark.parametrize('format,extension', [('PNG', '.png'), ('JPEG', '.jpg'), ('GIF', '.gif'), ('WEBP', '.webp')])
def test_supported_formats_use_actual_format_and_safe_filename(image_client, format, extension, tmp_path):
    response = upload(image_client, image_bytes(format), name='disguised.html')
    assert response.status_code == 200
    assert response.json()['url'].endswith(extension)
    files = [path for path in tmp_path.rglob('*') if path.is_file()]
    assert len(files) == 1 and len(files[0].stem) == 32
    with Image.open(files[0]) as image:
        assert image.format == format and image.size == (24, 32)


def test_image_byte_limit_rejects_before_parser(image_client, monkeypatch):
    monkeypatch.setattr(upload_images, 'MAX_UPLOAD_BYTES', 10)
    parser = Mock(side_effect=AssertionError('Must reject before parsing'))
    monkeypatch.setattr(upload_images.Image, 'open', parser)
    assert upload(image_client, b'x' * 11).status_code == 400
    parser.assert_not_called()


def test_total_pixel_limit_rejects_before_pixel_decode(image_client, monkeypatch, tmp_path):
    data = image_bytes(size=(24, 32))
    monkeypatch.setattr(upload_images, 'MAX_PIXELS', 100)
    decode = Mock(side_effect=AssertionError('Must reject before decoding'))
    monkeypatch.setattr(Image.Image, 'convert', decode)
    assert upload(image_client, data).status_code == 400
    decode.assert_not_called()
    assert not list(tmp_path.rglob('*'))


def test_excessive_source_height_is_rejected(image_client, tmp_path):
    assert upload(image_client, image_bytes(size=(1, 8193))).status_code == 400
    assert not list(tmp_path.rglob('*'))


def test_output_height_is_bounded(image_client, tmp_path):
    assert upload(image_client, image_bytes(size=(20, 3000))).status_code == 200
    file = next(path for path in tmp_path.rglob('*') if path.is_file())
    with Image.open(file) as image:
        assert image.size == (16, 2400)


def test_sentry_filter_removes_body_and_all_frame_locals():
    event = {'request': {'data': {'message': 'private', 'history': ['private']},
                         'cookies': {'sessionid': 'private'}, 'env': {}, 'query_string': 'email=test@example.com'},
             'exception': {'values': [{'stacktrace': {'frames': [{'vars': {'message': 'private'}, 'function': 'chat'}]}}]},
             'threads': {'values': [{'stacktrace': {'frames': [{'vars': {'history': 'private'}}]}}]}}
    result = _sentry_before_send(event, {})
    assert 'data' not in result['request'] and 'cookies' not in result['request'] and 'env' not in result['request']
    assert 'test@example.com' not in json.dumps(result)
    assert 'private' not in json.dumps(result)
    assert result['exception']['values'][0]['stacktrace']['frames'][0]['function'] == 'chat'


def test_sentry_initialization_disables_body_and_locals(monkeypatch):
    init = Mock()
    monkeypatch.setattr('myblog.sentry_setup.sentry_sdk.init', init)
    init_sentry('https://public@example.invalid/1')
    options = init.call_args.kwargs
    assert options['send_default_pii'] is False
    assert options['max_request_body_size'] == 'never'
    assert options['include_local_variables'] is False
    assert options['before_send'] is _sentry_before_send
    assert options['before_send_transaction'] is _sentry_before_send

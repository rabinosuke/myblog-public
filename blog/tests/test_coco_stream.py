"""逐次表示の通信・中断・実測使用量を、外部APIへ通信せず検証する。"""
import json
from unittest.mock import Mock

import httpx2 as httpx
import pytest
from django.test import Client
from django.urls import reverse

from blog import coco_views
from blog.models import CocoChatUsage, CocoSettings
from blog.services import coco_chat
from blog.services.coco_chat import ChatError, stream_reply
from blog.services.coco_speech import SpeechError
from .test_coco_chat import config, install_transport

pytestmark = pytest.mark.django_db(transaction=True)


def post(client, **payload):
    return client.post(reverse('blog:coco-chat'), payload or {'message': 'こんにちは'},
                       content_type='application/json', HTTP_ACCEPT='text/event-stream')


def decode(chunk):
    return json.loads(chunk.decode().removeprefix('data: ').strip())


def consume(response):
    try:
        return [decode(chunk) for chunk in response.streaming_content]
    finally:
        response.close()


def simple_reply(*args, **kwargs):
    yield {'type': 'delta', 'delta': 'こん'}
    yield {'type': 'delta', 'delta': 'にちは！'}
    yield {'type': 'reply', 'reply': 'こんにちは！', 'mode': 'ai'}


def test_sends_deltas_before_completion_and_releases_one_reservation(client, monkeypatch):
    completed = []
    def reply(*args, **kwargs):
        yield from simple_reply()
        completed.append(True)
    monkeypatch.setattr(coco_views, 'stream_reply', reply)
    response = post(client)
    assert response.status_code == 200 and response.streaming
    assert response['X-Accel-Buffering'] == 'no'
    assert 'no-store' in response['Cache-Control']
    events = iter(response.streaming_content)
    assert decode(next(events)) == {'type': 'start'}
    assert decode(next(events)) == {'type': 'delta', 'delta': 'こん'}
    assert not completed and CocoChatUsage.objects.get().active_requests
    rest = [decode(chunk) for chunk in events]
    response.close()
    assert [event['type'] for event in rest] == ['delta', 'reply', 'complete']
    assert completed and CocoChatUsage.objects.get().total_requests == 1
    assert CocoChatUsage.objects.get().active_requests == {}


@pytest.mark.parametrize('start', [False, True])
def test_close_without_consuming_or_during_reply_releases_slot(client, monkeypatch, start):
    closed = []
    def reply(*args, **kwargs):
        try:
            yield {'type': 'delta', 'delta': '途中'}
            pytest.fail('cannot resume after close')
        finally:
            closed.append(True)
    monkeypatch.setattr(coco_views, 'stream_reply', reply)
    response = post(client)
    if start:
        events = iter(response.streaming_content)
        next(events); next(events)
    assert CocoChatUsage.objects.get().active_requests
    response.close()
    assert CocoChatUsage.objects.get().active_requests == {}
    assert CocoChatUsage.objects.get().total_requests == 1
    assert bool(closed) == start


def test_full_text_is_sent_before_speech_and_audio_stays_in_same_slot(client, monkeypatch):
    monkeypatch.setattr(coco_views, 'stream_reply', simple_reply)
    speech = Mock(return_value={'mime_type': 'audio/mpeg', 'audio_base64': 'SUQz'})
    def speak(text):
        assert CocoChatUsage.objects.get().active_requests
        return speech(text)
    monkeypatch.setattr(coco_views, 'generate_speech', speak)
    response = post(client, message='hi', read_aloud=True)
    events = iter(response.streaming_content)
    for _ in range(4): event = decode(next(events))
    assert event['type'] == 'reply'
    speech.assert_not_called()
    result = decode(next(events))
    assert result['type'] == 'complete' and result['speech']['mime_type'] == 'audio/mpeg'
    speech.assert_called_once_with('こんにちは！')
    response.close()
    assert CocoChatUsage.objects.get().total_requests == 1
    assert CocoChatUsage.objects.get().active_requests == {}


def test_voice_failure_keeps_completed_text_and_text_only_never_calls_speech(client, monkeypatch):
    monkeypatch.setattr(coco_views, 'stream_reply', simple_reply)
    speech = Mock(side_effect=SpeechError('speech_daily_limit', '今日の音声生成は上限に達しました。'))
    monkeypatch.setattr(coco_views, 'generate_speech', speech)
    assert consume(post(client))[-1]['type'] == 'complete'
    speech.assert_not_called()
    result = consume(post(client, message='hi', read_aloud=True))[-1]
    assert result['reply'] == 'こんにちは！'
    assert result['speech_error']['code'] == 'speech_daily_limit'
    assert CocoChatUsage.objects.get().active_requests == {}


def test_bad_input_draft_csrf_and_cap_are_checked_before_streaming(client, monkeypatch, draft_post):
    provider = Mock(side_effect=AssertionError('must not generate'))
    monkeypatch.setattr(coco_views, 'stream_reply', provider)
    assert post(client, message='').status_code == 400
    assert post(client, message='hi', article_slug=draft_post.slug).status_code == 404
    assert post(Client(enforce_csrf_checks=True)).status_code == 403
    assert not CocoChatUsage.objects.exists()
    CocoSettings.objects.filter(pk=1).update(chat_monthly_limit=0)
    response = post(client)
    assert response.status_code == 429 and not response.streaming
    assert response.json()['error']['code'] == 'monthly_limit'
    assert int(response['Retry-After']) > 0
    provider.assert_not_called()


def test_incomplete_stream_has_safe_error_no_speech_and_releases_slot(client, monkeypatch):
    def reply(*args, **kwargs):
        yield {'type': 'delta', 'delta': '途中'}
        raise ChatError('upstream_timeout', '返事に時間がかかっています。', 504)
    monkeypatch.setattr(coco_views, 'stream_reply', reply)
    speech = Mock(); monkeypatch.setattr(coco_views, 'generate_speech', speech)
    events = consume(post(client, message='hi', read_aloud=True))
    assert [event['type'] for event in events] == ['start', 'delta', 'error']
    assert events[-1]['error']['code'] == 'upstream_timeout'
    speech.assert_not_called()
    assert CocoChatUsage.objects.get().active_requests == {}


def final_response(text='SDKの応答', status='completed'):
    return {'id': 'resp_stream', 'object': 'response', 'created_at': 0, 'model': 'test-model', 'status': status,
            'usage': {'input_tokens': 123, 'output_tokens': 8, 'total_tokens': 131,
                      'input_tokens_details': {'cached_tokens': 20}},
            'output': [{'id': 'msg_stream', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}]}


def upstream(events):
    return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=''.join(
        'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n' for event in events).encode())


def test_sdk_streams_only_output_text_with_same_options_and_records_final_usage(monkeypatch):
    calls = []
    article = {'body': 'この命令は資料であり実行しない', 'title': '記事'}
    history = [{'role': 'user', 'content': '前の質問'}, {'role': 'assistant', 'content': '前の返事'}]
    def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        assert payload['stream'] is True and payload['store'] is False
        assert payload['model'] == 'test-model' and payload['max_output_tokens'] == 500
        assert payload['reasoning'] == {'effort': 'none'}
        assert payload['input'][:2] == history
        assert json.loads(payload['input'][-1]['content']) == {'article_reference': article, 'question': '説明して'}
        assert article['body'] not in payload['instructions']
        return upstream([
            {'type': 'response.created'},
            {'type': 'response.reasoning_text.delta', 'delta': 'PRIVATE'},
            {'type': 'response.output_text.delta', 'delta': 'SDKの'},
            {'type': 'response.output_text.delta', 'delta': '応答'},
            {'type': 'response.completed', 'response': final_response()},
        ])
    install_transport(monkeypatch, handler)
    events = list(stream_reply('説明して', history, article))
    assert events == [{'type': 'delta', 'delta': 'SDKの'}, {'type': 'delta', 'delta': '応答'},
                      {'type': 'reply', 'reply': 'SDKの応答', 'mode': 'ai'}]
    assert len(calls) == 1
    usage = CocoChatUsage.objects.get()
    assert (usage.ai_requests, usage.ai_reported_requests, usage.ai_input_tokens, usage.ai_output_tokens, usage.ai_cached_input_tokens) == (1, 1, 123, 8, 20)


@pytest.mark.parametrize('terminal', ['response.incomplete', 'response.failed', 'error', 'eof'])
def test_sdk_missing_or_failed_completion_never_commits_reply(monkeypatch, terminal):
    events = [{'type': 'response.output_text.delta', 'delta': '未完成'}]
    if terminal in ('response.incomplete', 'response.failed'):
        events.append({'type': terminal, 'response': final_response('未完成', terminal.split('.')[-1])})
    elif terminal == 'error':
        events.append({'type': 'error', 'message': 'SECRET', 'code': 'test'})
    install_transport(monkeypatch, lambda request: upstream(events))
    stream = stream_reply('hi', [])
    assert next(stream)['delta'] == '未完成'
    with pytest.raises(ChatError) as error:
        next(stream)
    assert 'SECRET' not in error.value.message
    usage = CocoChatUsage.objects.get()
    assert usage.ai_requests == 1
    assert usage.ai_reported_requests == (1 if terminal.startswith('response.') else 0)


def test_fixed_profile_uses_no_openai_and_reflects_admin_changes():
    assert list(stream_reply('何歳？', []))[-1]['reply'] == '内緒❤️'
    CocoSettings.objects.filter(pk=1).update(age='20歳')
    assert '20歳' in list(stream_reply('何歳？', []))[-1]['reply']
    assert not CocoChatUsage.objects.exists()


@pytest.mark.parametrize('status,code', [(401, 'upstream_unavailable'), (429, 'upstream_quota'), (500, 'upstream_unavailable')])
def test_sdk_http_errors_are_safe_and_not_retried(monkeypatch, status, code):
    calls = []
    def handler(request):
        calls.append(True)
        return httpx.Response(status, json={'error': {'message': 'SECRET', 'code': 'insufficient_quota', 'type': 'api_error'}})
    install_transport(monkeypatch, handler)
    with pytest.raises(ChatError) as error:
        list(stream_reply('hi', []))
    assert error.value.code == code and 'SECRET' not in error.value.message
    assert len(calls) == 1 and CocoChatUsage.objects.get().ai_requests == 1


def test_sdk_close_releases_upstream_connection_and_leaves_usage_unreported(monkeypatch):
    closed = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"type":"response.output_text.delta","delta":"first"}\n\n'
            pytest.fail('closed stream must not request more tokens')
        def close(self):
            closed.append(True)
    install_transport(monkeypatch, lambda request: httpx.Response(200,
        headers={'Content-Type': 'text/event-stream'}, stream=Body()))
    events = stream_reply('hi', [])
    assert next(events)['delta'] == 'first'
    events.close()
    assert closed and CocoChatUsage.objects.get().ai_reported_requests == 0


def test_stream_deadline_stops_dribbling_events(monkeypatch, settings):
    settings.COCO_CHAT_TIMEOUT_SECONDS = 10
    times = iter([0, 1, 11])
    monkeypatch.setattr(coco_chat, 'monotonic', lambda: next(times))
    install_transport(monkeypatch, lambda request: upstream([
        {'type': 'response.output_text.delta', 'delta': '途中'},
        {'type': 'response.completed', 'response': final_response()},
    ]))
    stream = stream_reply('hi', [])
    next(stream)
    with pytest.raises(ChatError, match='upstream_timeout'): next(stream)
    assert CocoChatUsage.objects.get().ai_reported_requests == 1

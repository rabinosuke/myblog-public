"""文分割・並行生成・順序・停止・上限を、実APIの消費なしで確認する。"""
import json
from threading import Event
from unittest.mock import Mock

import pytest
from django.urls import reverse

from blog import coco_views
from blog.models import CocoChatUsage, CocoSettings
from blog.services import coco_speech, coco_speech_stream
from blog.services.coco_speech import SpeechError
from blog.services.coco_speech_stream import SentenceBuffer, SpeechPipeline
from .test_coco_chat import config
from .test_coco_speech import AudioResponse, install_opener

pytestmark = pytest.mark.django_db(transaction=True)

AUDIO = {'mime_type': 'audio/mpeg', 'audio_base64': 'SUQz'}
FIRST = '最初の文をここで読み上げるよ。'
SECOND = '次の文も順番どおりに読み上げるね。'


def post(client, read_aloud=True, protocol='sentences-v1'):
    return client.post(reverse('blog:coco-chat'), {'message': '短く教えて', 'read_aloud': read_aloud},
                       content_type='application/json', HTTP_ACCEPT='text/event-stream', HTTP_X_COCO_AUDIO_STREAM=protocol)


def consume(response):
    try:
        return [json.loads(chunk.decode()[6:]) for chunk in response.streaming_content if chunk.startswith(b'data: ')]
    finally:
        response.close()


def model(*args, **kwargs):
    yield {'type': 'delta', 'delta': FIRST + SECOND[:1]}
    yield {'type': 'delta', 'delta': SECOND[1:]}
    yield {'type': 'reply', 'reply': FIRST + SECOND, 'mode': 'ai'}


@pytest.mark.parametrize('characterwise', [False, True])
def test_sentences_keep_the_whole_text_and_bound_request_count(characterwise):
    text = ''.join(f'これは第{n}番目の十分な長さの文章だよ。' for n in range(1, 11))
    buffer = SentenceBuffer()
    chunks = []
    for delta in (list(text) if characterwise else [text]): chunks += buffer.feed(delta)
    chunks += buffer.finish(text)
    assert ''.join(chunks) == text
    assert len(chunks) == 4


@pytest.mark.parametrize('text', [
    'コードの例だよ。\n```python\nprint("切らないで。ここもコード。")\n```\n説明をここで続けるよ。最後の文章もあるよ。',
    '参考は[ページ。途中で切らない](https://example.com/a?q=1)。次の文章は別に読めるよ。',
    'リンクは https://example.com/path?x=1.2 で確認できるよ。次の文章も続いているよ。',
    'インラインの`example。code`は切らずに処理するよ。次の文章も続けるね。',
    'タグは[shouting]を操作命令として実行しないよ。次の文もあるよ。',
])
def test_markdown_url_and_audio_controls_do_not_split_inside_tokens(text):
    buffer = SentenceBuffer(); chunks = []
    for char in text: chunks += buffer.feed(char)
    chunks += buffer.finish(text)
    assert ''.join(chunks).replace(' ', '').replace('\n', '') == text.replace(' ', '').replace('\n', '')
    for chunk in chunks:
        assert chunk.count('```') % 2 == 0
        assert chunk.count('`') % 2 == 0
        assert chunk.count('[') == chunk.count(']')
        assert chunk.count('(') == chunk.count(')')


def test_short_fixed_profile_is_a_single_chunk_and_changed_final_text_is_rejected():
    buffer = SentenceBuffer()
    assert buffer.feed('内緒❤️') == []
    assert buffer.finish('内緒❤️') == ['内緒❤️']
    changed = SentenceBuffer(); changed.feed(FIRST + SECOND)
    with pytest.raises(SpeechError, match='speech_text_changed'): changed.finish('別の文章')


def test_pipeline_generates_sequentially_and_emits_in_order():
    calls = []
    def speak(text):
        calls.append(text)
        return {**AUDIO, 'test': text}
    with SpeechPipeline(speak) as pipeline:
        pipeline.submit(FIRST); pipeline.submit(SECOND)
        events = []
        while pipeline.pending: events += [event for event in pipeline.ready(wait=True) if event]
    assert calls == [FIRST, SECOND]
    assert [event['index'] for event in events] == [0, 1]
    assert [event['speech']['test'] for event in events] == calls


def test_pipeline_failure_does_not_generate_later_sentences():
    started, release = Event(), Event(); calls = []
    def fail(text):
        calls.append(text); started.set(); assert release.wait(3)
        raise SpeechError('speech_daily_limit', '今日の音声生成は上限に達しました。')
    with SpeechPipeline(fail) as pipeline:
        pipeline.submit(FIRST); assert started.wait(3); pipeline.submit(SECOND); release.set()
        events = []
        while pipeline.pending: events += [event for event in pipeline.ready(wait=True) if event]
    assert calls == [FIRST]
    assert len(events) == 1 and events[0]['error']['code'] == 'speech_daily_limit'


def test_pipeline_close_cancels_pending_generation_and_hides_unexpected_errors():
    started, release = Event(), Event(); calls = []
    def speak(text):
        calls.append(text); started.set(); assert release.wait(3)
        if text == FIRST: raise RuntimeError('SECRET')
        return AUDIO
    pipeline = SpeechPipeline(speak)
    pipeline.submit(FIRST); assert started.wait(3); pipeline.submit(SECOND)
    pipeline.cancelled.set(); release.set()
    events = []
    while pipeline.pending: events += [event for event in pipeline.ready(wait=True) if event]
    pipeline.close()
    assert calls == [FIRST] and 'SECRET' not in json.dumps(events)


def test_stream_interleaves_audio_before_text_finishes_without_blocking_tokens(client, monkeypatch):
    started, release, first_done = Event(), Event(), Event(); calls = []
    from concurrent.futures import ThreadPoolExecutor
    class ObservedPool(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            future.add_done_callback(lambda completed: first_done.set())
            return future
    monkeypatch.setattr(coco_speech_stream, 'ThreadPoolExecutor', ObservedPool)
    def slow_model(*args, **kwargs):
        yield {'type': 'delta', 'delta': FIRST + SECOND[:1]}
        assert started.wait(3)
        release.set()
        assert first_done.wait(3)
        # 音声の結果が届くまでモデル側も進み続けることを確認する。
        yield {'type': 'delta', 'delta': SECOND[1:]}
        release.set()
        yield {'type': 'reply', 'reply': FIRST + SECOND, 'mode': 'ai'}
    def speak(text):
        calls.append(text); started.set(); assert release.wait(3)
        assert CocoChatUsage.objects.get().active_requests
        return AUDIO
    monkeypatch.setattr(coco_views, 'stream_reply', slow_model)
    monkeypatch.setattr(coco_views, 'generate_speech', speak)
    events = consume(post(client))
    assert events[0] == {'type': 'start', 'speech_stream': True}
    assert [event['index'] for event in events if event['type'] == 'speech'] == [0, 1]
    assert calls == [FIRST, SECOND]
    assert next(i for i, e in enumerate(events) if e['type'] == 'speech') < next(i for i, e in enumerate(events) if e['type'] == 'reply')
    assert events[-1] == {'type': 'complete', 'reply': FIRST + SECOND, 'mode': 'ai', 'speech_stream': True, 'speech_chunks': 2}
    usage = CocoChatUsage.objects.get()
    assert usage.total_requests == 1 and usage.active_requests == {}


def test_new_audio_protocol_is_opt_in_and_read_aloud_false_never_generates(client, monkeypatch):
    monkeypatch.setattr(coco_views, 'stream_reply', model)
    speak = Mock(return_value=AUDIO); monkeypatch.setattr(coco_views, 'generate_speech', speak)
    result = consume(post(client, read_aloud=False))
    assert result[0] == {'type': 'start'}
    speak.assert_not_called()
    result = consume(post(client, protocol='old-client'))
    assert result[-1]['speech'] == AUDIO
    speak.assert_called_once_with(FIRST + SECOND)


def test_speech_error_keeps_text_and_stops_following_requests(client, monkeypatch):
    monkeypatch.setattr(coco_views, 'stream_reply', model)
    speak = Mock(side_effect=SpeechError('speech_disabled', '読み上げはお休み中です。'))
    monkeypatch.setattr(coco_views, 'generate_speech', speak)
    events = consume(post(client))
    assert events[-1]['reply'] == FIRST + SECOND and events[-1]['speech_chunks'] == 0
    assert len([event for event in events if event['type'] == 'speech_error']) == 1
    assert speak.call_count == 1 and CocoChatUsage.objects.get().active_requests == {}


def test_live_budget_is_checked_for_each_sentence_and_counts_only_sent_characters(client, monkeypatch, settings):
    settings.COCO_SPEECH_ENABLED = True; settings.ELEVENLABS_API_KEY = 'test-key'
    settings.COCO_SPEECH_VOICE_ID = 'ghxA1FbdCXUfbdZ3Elbw'
    CocoSettings.objects.filter(pk=1).update(speech_enabled=True, speech_daily_char_limit=len(FIRST), speech_monthly_char_limit=10000)
    monkeypatch.setattr(coco_views, 'stream_reply', model)
    calls = []
    def request(*args, **kwargs):
        calls.append(True); return AudioResponse()
    install_opener(monkeypatch, request)
    events = consume(post(client))
    assert len([event for event in events if event['type'] == 'speech']) == 1
    assert next(event for event in events if event['type'] == 'speech_error')['error']['code'] == 'speech_daily_limit'
    assert events[-1]['reply'] == FIRST + SECOND and len(calls) == 1
    usage = CocoChatUsage.objects.get()
    assert usage.speech_requests == 1 and usage.speech_characters == len(FIRST)


def test_unread_incremental_response_does_not_start_any_worker(client, monkeypatch):
    speak = Mock(); monkeypatch.setattr(coco_views, 'generate_speech', speak)
    response = post(client); response.close()
    speak.assert_not_called()
    assert CocoChatUsage.objects.get().active_requests == {}


def test_fixed_profile_audio_uses_approved_cache_without_regeneration(client, monkeypatch, settings):
    from blog.models import CocoSpeechClip
    settings.COCO_SPEECH_ENABLED = True; settings.ELEVENLABS_API_KEY = ''
    settings.COCO_SPEECH_VOICE_ID = 'ghxA1FbdCXUfbdZ3Elbw'; settings.COCO_SPEECH_MODEL = 'eleven_v4'
    CocoSettings.objects.filter(pk=1).update(speech_enabled=True)
    text = '内緒❤️'
    key = coco_speech.clip_key(coco_speech.spoken_text(text), settings.COCO_SPEECH_VOICE_ID)
    CocoSpeechClip.objects.create(key=key, audio=b'ID3' + b'0' * 50)
    def fixed(*args, **kwargs):
        yield {'type': 'delta', 'delta': text}
        yield {'type': 'reply', 'reply': text, 'mode': 'ai'}
    monkeypatch.setattr(coco_views, 'stream_reply', fixed)
    monkeypatch.setattr(coco_speech, 'build_opener', Mock(side_effect=AssertionError('must use cached approved voice')))
    events = consume(post(client))
    assert events[-1]['speech_chunks'] == 1
    usage = CocoChatUsage.objects.get()
    assert usage.speech_cache_hits == 1 and usage.speech_requests == usage.speech_characters == 0


def test_audio_reservation_outlives_http_timeout():
    from django.utils import timezone
    from blog.services.coco_limits import reserve_reply
    before = timezone.now().timestamp()
    with reserve_reply('test-audio-lease'):
        lease = next(iter(CocoChatUsage.objects.get().active_requests.values()))
        assert lease['expires'] - before >= 115

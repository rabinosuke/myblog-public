"""MySQLの行ロックで、全Webプロセス共通の回数・同時実行制限を守る。"""

from contextlib import contextmanager
from datetime import datetime, time, timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from blog.models import CocoChatUsage
from .coco_chat import ChatError
from .coco_usage import add_counts, current_limits, locked_usage


def _network_key(day, network):
    if network is None:
        return None
    return 'network:' + salted_hmac('coco-chat-network', f'{day}:{network}').hexdigest()


@contextmanager
def reserve_reply(identity, *, network=None):
    now = timezone.now()
    day = timezone.localdate(now)
    timestamp = now.timestamp()
    minute = int(timestamp // 60)
    visitor = salted_hmac('coco-chat-visitor', identity).hexdigest()
    # IPの生値は残さず、日ごとに異なるHMACだけで同じ回線の連投を数える。
    network_key = _network_key(day, network)
    previous_network_key = _network_key(day - timedelta(days=1), network)
    token = uuid4().hex
    next_day = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
    day_wait = max(1, int((next_day - now).total_seconds()))
    next_month = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_wait = max(1, int((timezone.make_aware(datetime.combine(next_month, time.min)) - now).total_seconds()))

    with locked_usage(day, previous_day=day - timedelta(days=1)) as (monthly, usage, previous):
        limits = current_limits()
        active = {key: value for key, value in usage.active_requests.items() if value['expires'] > timestamp}
        previous_active = [value for value in previous.active_requests.values() if value['expires'] > timestamp] if previous else []
        if usage.total_requests >= limits.chat_daily_limit:
            raise ChatError('daily_limit', '今日のAI会話は上限に達しました。また明日お話ししてね。', 429, day_wait)
        if monthly.total_requests >= limits.chat_monthly_limit:
            raise ChatError('monthly_limit', '今月の会話は上限に達しました。また来月お話ししてね。', 429, month_wait)
        counters = {}
        for key in (visitor, network_key):
            if key is None:
                continue
            counter = usage.visitors.get(key, {'count': 0, 'minute': minute, 'minute_count': 0})
            if counter['count'] >= limits.chat_visitor_daily_limit:
                raise ChatError('daily_limit', '今日のAI会話は上限に達しました。また明日お話ししてね。', 429, day_wait)
            if counter['minute'] != minute:
                counter.update(minute=minute, minute_count=0)
            if counter['minute_count'] >= limits.chat_per_minute_limit:
                raise ChatError('rate_limit', '少し間をあけて話しかけてね。', 429, 60 - int(timestamp) % 60)
            counters[key] = counter
        burst = usage.visitors.get('burst', {'minute': minute, 'minute_count': 0})
        if burst['minute'] != minute:
            burst.update(minute=minute, minute_count=0)
        if burst['minute_count'] >= settings.COCO_CHAT_GLOBAL_PER_MINUTE:
            raise ChatError('rate_limit', '少し間をあけて話しかけてね。', 429, 60 - int(timestamp) % 60)
        in_progress = [*active.values(), *previous_active]
        same_reader = any(
            value['visitor'] == visitor or
            (network_key is not None and value.get('network') in (network_key, previous_network_key))
            for value in in_progress
        )
        if len(in_progress) >= limits.chat_max_concurrent or same_reader:
            raise ChatError('busy', 'ほかの返事を作っています。少し待ってね。', 429, 3)
        for key, counter in counters.items():
            counter['count'] += 1
            counter['minute_count'] += 1
            usage.visitors[key] = counter
        burst['minute_count'] += 1
        usage.visitors['burst'] = burst
        add_counts(monthly, usage, total_requests=1)
        # プロセスが終了しても枠を塞ぎ続けない。回数は失敗時も戻さず、連続再試行を抑える。
        # 最大4文の音声生成を待つHTTP上限90秒より、実行枠の寿命を長くする。
        active[token] = {'visitor': visitor, 'network': network_key,
                         'expires': timestamp + max(120, settings.COCO_CHAT_TIMEOUT_SECONDS * 4)}
        usage.active_requests = active
        usage.save(update_fields=['visitors', 'active_requests'])

    try:
        yield
    finally:
        with transaction.atomic():
            usage = CocoChatUsage.objects.select_for_update().get(day=day)
            usage.active_requests.pop(token, None)
            usage.save(update_fields=['active_requests'])

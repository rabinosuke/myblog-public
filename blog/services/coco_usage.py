"""このブログの利用を日本時間の日別・暦月別で集計する。本文は保存しない。"""

from contextlib import contextmanager

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from blog.models import CocoChatUsage, CocoMonthlyUsage, CocoSettings, CocoUsageCounters

COUNTERS = tuple(field.name for field in CocoUsageCounters._meta.fields)


def current_limits():
    profile = CocoSettings.objects.filter(pk=1).first()
    if profile:
        return profile
    # 設定行がない場合も、従来の環境設定を使って無制限にはしない。
    return CocoSettings(chat_daily_limit=settings.COCO_CHAT_GLOBAL_PER_DAY,
                        chat_visitor_daily_limit=settings.COCO_CHAT_PER_VISITOR_DAY,
                        chat_per_minute_limit=settings.COCO_CHAT_PER_MINUTE,
                        chat_max_concurrent=settings.COCO_CHAT_MAX_CONCURRENT)


@contextmanager
def locked_usage(day, previous_day=None):
    """月→前日→当日の順にロック。日付が変わる瞬間も月上限を共有する。"""
    with transaction.atomic():
        month = day.replace(day=1)
        monthly, created = CocoMonthlyUsage.objects.get_or_create(month=month)
        monthly = CocoMonthlyUsage.objects.select_for_update().get(pk=month)
        if created:
            totals = CocoChatUsage.objects.filter(day__year=day.year, day__month=day.month).aggregate(**{name: Sum(name) for name in COUNTERS})
            for name in COUNTERS:
                setattr(monthly, name, totals[name] or 0)
            monthly.save(update_fields=COUNTERS)
        previous = CocoChatUsage.objects.select_for_update().filter(pk=previous_day).first() if previous_day else None
        CocoChatUsage.objects.get_or_create(day=day)
        daily = CocoChatUsage.objects.select_for_update().get(pk=day)
        yield monthly, daily, previous


def add_counts(monthly, daily, **changes):
    for obj in (monthly, daily):
        for name, amount in changes.items():
            setattr(obj, name, getattr(obj, name) + amount)
        obj.save(update_fields=list(changes))


def record_counts(day=None, **changes):
    day = day or timezone.localdate()
    with locked_usage(day) as (monthly, daily, _):
        add_counts(monthly, daily, **changes)
    return day


def record_ai_usage(day, usage):
    incoming, outgoing = getattr(usage, 'input_tokens', None), getattr(usage, 'output_tokens', None)
    if not all(type(value) is int and value >= 0 for value in (incoming, outgoing)):
        return
    cached = getattr(getattr(usage, 'input_tokens_details', None), 'cached_tokens', 0)
    cached = min(cached, incoming) if type(cached) is int and cached >= 0 else 0
    record_counts(day, ai_reported_requests=1, ai_input_tokens=incoming,
                  ai_output_tokens=outgoing, ai_cached_input_tokens=cached)


class SpeechLimitReached(Exception):
    def __init__(self, code, message):
        super().__init__(code)
        self.code, self.message = code, message


def reserve_speech_characters(count):
    day = timezone.localdate()
    with locked_usage(day) as (monthly, daily, _):
        limits = current_limits()
        if not settings.COCO_SPEECH_ENABLED or not limits.speech_enabled:
            raise SpeechLimitReached('speech_disabled', '読み上げはお休み中です。')
        if daily.speech_characters + count > limits.speech_daily_char_limit:
            raise SpeechLimitReached('speech_daily_limit', '今日の音声生成は上限に達しました。文章でお話ししよう。')
        if monthly.speech_characters + count > limits.speech_monthly_char_limit:
            raise SpeechLimitReached('speech_monthly_limit', '今月の音声生成は上限に達しました。文章でお話ししよう。')
        # 送信前に予約する。タイムアウト等でも先方で生成済みの可能性があるため戻さない。
        add_counts(monthly, daily, speech_requests=1, speech_characters=count)
    return day


def usage_overview():
    day = timezone.localdate()
    today = CocoChatUsage.objects.filter(pk=day).first() or CocoChatUsage(day=day)
    month = CocoMonthlyUsage.objects.filter(pk=day.replace(day=1)).first() or CocoMonthlyUsage(month=day.replace(day=1))
    limits = current_limits()
    return {
        'day': day, 'today': today, 'month': month, 'limits': limits,
        'today_unreported': max(0, today.ai_requests - today.ai_reported_requests),
        'month_unreported': max(0, month.ai_requests - month.ai_reported_requests),
        'today_total_tokens': today.ai_input_tokens + today.ai_output_tokens,
        'month_total_tokens': month.ai_input_tokens + month.ai_output_tokens,
    }

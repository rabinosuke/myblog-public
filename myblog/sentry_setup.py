"""Sentry の初期化とフィルタリングロジック（settings.py から呼ぶ）。

settings.py は宣言のみに保つため、Sentry の関数・パターン定義は
このモジュールに集約する（家訓「settings は宣言のみ、実行ロジックは別」）。

使い方（settings.py から）::

    from myblog.sentry_setup import init_sentry
    init_sentry(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        release=SENTRY_RELEASE,
    )

参考URL:
  - Sentry Django 公式: https://docs.sentry.io/platforms/python/integrations/django/
  - Best practices: https://docs.sentry.io/product/best-practices/
  - PII Scrubbing: https://docs.sentry.io/platforms/python/data-management/sensitive-data/
  - Filtering events: https://docs.sentry.io/platforms/python/configuration/filtering/
  - Two Scoops of Django（settings 分離原則）:
    https://www.feldroy.com/books/two-scoops-of-django-3-x
"""

import logging
import re

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration


# =====================================================================
# 個人情報（PII）スクラビングパターン
#
# 会話本文・履歴は障害調査の記録に残さない。
# URL クエリにメールが入った場合も必ず伏字にする。
# =====================================================================
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


# Sentry に送らないノイズ系例外（無料枠を浪費するだけの例外）
_IGNORED_EXCEPTIONS = (
    "DisallowedHost",       # IP 直打ちアクセス（ALLOWED_HOSTS 外）
    "InvalidPage",          # 存在しないページ番号
    "SuspiciousOperation",  # その他の不正リクエスト系
)


def _sentry_before_send(event, hint):
    """Sentry 送信前のフィルタ。個人情報除去 + ノイズ系例外の除外。

    return None でそのイベントは送信されない。
    send_default_pii=FalseだけではJSON本文や例外のローカル変数は除外されない。
    """
    # ─── ノイズ系例外は捨てる（無料枠保護） ───
    if "exc_info" in hint:
        exc_type = hint["exc_info"][0]
        if exc_type.__name__ in _IGNORED_EXCEPTIONS:
            return None

    # ─── 個人情報スクラビング ───
    request = event.get("request", {})

    # URL クエリストリングからメールアドレスを伏字化
    if "query_string" in request:
        request["query_string"] = _EMAIL_PATTERN.sub(
            "[email-redacted]",
            request["query_string"],
        )

    # 環境変数情報（メモリダンプ等）を消す
    request.pop("env", None)

    # クッキーは念のため全カット
    request.pop("cookies", None)

    # SDKの収集設定に加え、手動追加・他の連携経由の本文も必ず送信前に除く。
    request.pop("data", None)
    for key in ("exception", "threads"):
        for value in event.get(key, {}).get("values", []):
            for frame in value.get("stacktrace", {}).get("frames", []):
                frame.pop("vars", None)

    return event


def init_sentry(dsn, environment="production", release="unknown",
                traces_sample_rate=0.01):
    """Sentry を初期化する。dsn が空なら何もしない（dev/test/CI で安全）。

    引数:
        dsn: Sentry プロジェクトの DSN（空文字なら初期化スキップ）
        environment: production / staging / development 等
        release: git コミットハッシュ等（無ければ "unknown"）
        traces_sample_rate: パフォーマンス追跡サンプル率（無料枠保護で 0.01）
    """
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            DjangoIntegration(transaction_style="url"),
            LoggingIntegration(
                level=logging.INFO,         # INFO 以上をブレッドクラム収集
                event_level=logging.ERROR,  # ERROR 以上を Sentry イベント化
            ),
        ],
        traces_sample_rate=traces_sample_rate,
        environment=environment,
        release=release,
        # ─── PII 防御 ───
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        before_send=_sentry_before_send,
        before_send_transaction=_sentry_before_send,
    )

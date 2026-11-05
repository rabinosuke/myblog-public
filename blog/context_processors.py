"""
全テンプレートで自動的に使える変数を定義する。

settings.py の TEMPLATES → OPTIONS → context_processors に登録する。
"""

from django.conf import settings

from .services.coco_speech import speech_is_enabled


def site_settings(request):
    """サイト全体の設定値をテンプレートに渡す。"""

    return {
        # Google Analytics 4 の測定ID（本番デプロイ後に .env に設定する）
        'GA_TRACKING_ID': getattr(settings, 'GA_TRACKING_ID', ''),
        # テンプレート内で DEBUG モードを判定するために露出させる
        'debug': settings.DEBUG,
        # 公開するのは利用可否だけ。読者のON設定より管理者のOFF設定を優先する。
        'coco_speech_enabled': speech_is_enabled(),
    }

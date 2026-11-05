"""
プロジェクト全体のURLルーティング設定。

ここではアプリごとのURLをまとめて管理する。
各アプリの詳細なURLは各アプリの urls.py に書く。
"""

import environ

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.http import HttpResponse
from django.urls import include, path

from blog.sitemaps import sitemaps
from blog.views import page_not_found, robots_txt, server_error

# 管理画面URLを .env から読み込む（セキュリティ強化）
env = environ.Env()

# カスタムエラーハンドラー
# DEBUG=False の時のみ Django が使用する
handler404 = page_not_found
handler500 = server_error

urlpatterns = [
    # ヘルスチェック用エンドポイント
    # docker-compose.yml の healthcheck と、本番環境のロードバランサーが
    # Djangoの死活確認に使う。レスポンスは 'ok' を返すだけでよい
    path('health/', lambda request: HttpResponse('ok')),

    # Django標準の管理画面
    # デフォルトの /admin/ は攻撃者が真っ先に試す場所なので、
    # 推測しにくいランダム文字列に変更する。本番では .env で管理する
    path(env('ADMIN_URL', default='admin/'), admin.site.urls),

    # SEO関連
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),
    path('robots.txt', robots_txt),

    # blogアプリのURLを委譲する。詳細は blog/urls.py を参照
    # 末尾に置かないと slug パターンが他のURLを飲んでしまう
    path('', include('blog.urls')),
]

# DEBUG中は Django が media ファイルを配信する（本番では Nginx が配信する）
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

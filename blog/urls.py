"""
blogアプリのURLルーティング設定。

app_name で名前空間を設定することで、他のアプリと URL名が衝突しない。
テンプレートからは {% url 'blog:index' %} のように参照する。
"""

from django.urls import path

from . import coco_views, views

# 名前空間。他のアプリに同名の URL があっても区別できる
app_name = 'blog'

urlpatterns = [
    # COCOのAIテキスト会話（CSRF保護されたJSON POST）
    path('api/coco/chat/', coco_views.chat, name='coco-chat'),

    # トップページ（記事一覧）
    path('', views.index, name='index'),

    # 検索（例: /search/?q=docker）
    # 詳細ページの slug パターンに飲まれないよう posts/<slug>/ より前に置く
    path('search/', views.search, name='search'),

    # 管理画面の Toast UI Editor 用：Markdown→本番HTMLレンダリング
    path('admin-preview/', views.admin_preview, name='admin-preview'),
    # 管理画面の Toast UI Editor 用：画像アップロード
    path('admin-image-upload/', views.admin_image_upload, name='admin-image-upload'),
    # 管理画面の Toast UI Editor 用：キャラ一覧 JSON（吹き出しボタン動的生成）
    path('admin-character-list/', views.admin_character_list, name='admin-character-list'),

    # カテゴリ別一覧（例: /category/django/）
    path('category/<slug:slug>/', views.category, name='category'),

    # タグ別一覧（例: /tag/docker/）。検索エンジンには noindex で返す
    path('tag/<slug:slug>/', views.tag, name='tag'),

    # 記事詳細ページ（例: /posts/django-blog-intro/）
    path('posts/<slug:slug>/', views.detail, name='detail'),
]

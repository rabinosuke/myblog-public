"""pytest 全テストで使う共通 fixture（pytest が自動で読み込む）。"""

import pytest
from django.contrib.auth import get_user_model

from .factories import CategoryFactory, DraftPostFactory, PostFactory, TagFactory


# ------------------------------------------------------------
# テスト環境のセキュリティ設定無効化（世界標準パターン）
# ------------------------------------------------------------

@pytest.fixture(autouse=True)
def disable_https_redirect_for_tests(settings):
    """全テストで HTTPS リダイレクト関連を無効化する。

    本番では SECURE_SSL_REDIRECT=True / HSTS=31536000 だが、
    pytest の test client は HTTP でリクエストするため、
    そのままだと全テストが 301 で失敗する。

    pytest-django の `settings` fixture を使って override するのが
    Django+pytest の標準パターン。
    （参考：pytest-django 公式ドキュメント、django-webtest issue #46 解決策）

    autouse=True なので全テストに自動適用される。
    """
    settings.SECURE_SSL_REDIRECT = False
    settings.SECURE_HSTS_SECONDS = 0


# ------------------------------------------------------------
# 記事系
# ------------------------------------------------------------

@pytest.fixture
def post(db):
    """デフォルトの公開記事 1 つ。"""
    return PostFactory()


@pytest.fixture
def draft_post(db):
    """下書き記事 1 つ。"""
    return DraftPostFactory()


@pytest.fixture
def category(db):
    """カテゴリ 1 つ。"""
    return CategoryFactory()


@pytest.fixture
def tag(db):
    """タグ 1 つ。"""
    return TagFactory()


# ------------------------------------------------------------
# ユーザー系
# ------------------------------------------------------------

@pytest.fixture
def staff_user(db):
    """管理者ユーザー（is_staff=True）。"""
    User = get_user_model()
    return User.objects.create_user(
        username='staff', password='pw', is_staff=True,
    )


@pytest.fixture
def regular_user(db):
    """一般ユーザー（is_staff=False）。"""
    User = get_user_model()
    return User.objects.create_user(username='regular', password='pw')


@pytest.fixture
def admin_user(db):
    """スーパーユーザー（管理画面テスト用）。"""
    User = get_user_model()
    return User.objects.create_superuser(
        username='admin', email='admin@example.com', password='pw',
    )


# ------------------------------------------------------------
# 管理画面テスト用 request
# ------------------------------------------------------------

@pytest.fixture
def admin_request(rf, admin_user):
    """admin の save_model に渡す request を組み立てる。

    `rf` は pytest-django 標準の RequestFactory fixture。
    """
    request = rf.post('/admin/blog/post/add/')
    request.user = admin_user
    return request

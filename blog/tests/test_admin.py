"""管理画面の save_model 補正処理をテストする。"""

import pytest
from django.contrib.admin.sites import AdminSite
from django.urls import reverse
from django.utils import timezone

from blog.admin import CharacterAdmin, PostAdmin
from blog.models import Character, Post


@pytest.mark.django_db
def test_save_model_fills_published_at_when_is_published_but_null(admin_request):
    """is_published=True かつ published_at=NULL なら、save_model が現在時刻で埋める。"""
    obj = Post(title='公開予定', slug='auto-fill', body='本文', is_published=True)
    assert obj.published_at is None

    admin = PostAdmin(model=Post, admin_site=AdminSite())
    admin.save_model(admin_request, obj, form=None, change=False)

    obj.refresh_from_db()
    assert obj.published_at is not None  # NULL から埋められた


@pytest.mark.django_db
def test_save_model_does_not_overwrite_existing_published_at(admin_request):
    """published_at が既に入っていれば save_model は上書きしない。"""
    fixed = timezone.now() - timezone.timedelta(days=10)
    obj = Post(
        title='古い記事', slug='keep-date', body='本文',
        is_published=True, published_at=fixed,
    )

    admin = PostAdmin(model=Post, admin_site=AdminSite())
    admin.save_model(admin_request, obj, form=None, change=True)

    obj.refresh_from_db()
    assert obj.published_at == fixed  # 10日前のままで上書きされていない


@pytest.mark.django_db
def test_save_model_does_not_fill_when_unpublished(admin_request):
    """is_published=False なら published_at は NULL のまま放置する。"""
    obj = Post(title='下書き', slug='draft', body='本文', is_published=False)

    admin = PostAdmin(model=Post, admin_site=AdminSite())
    admin.save_model(admin_request, obj, form=None, change=False)

    obj.refresh_from_db()
    assert obj.published_at is None  # 下書きには触らない


# ------------------------------------------------------------
# CharacterAdmin の画像プレビュー
# ------------------------------------------------------------

def test_character_admin_preview_shows_saved_image_url():
    """保存済み画像を正方形と円形の両方で確認できる。"""
    character_admin = CharacterAdmin(model=Character, admin_site=AdminSite())
    character = Character(name='ココ', slug='coco')
    character.image.name = 'characters/coco-normal.png'

    html = str(character_admin.current_image_preview(character))

    assert html.count('/media/characters/coco-normal.png') == 2
    assert '保存される正方形' in html
    assert '記事上の見え方' in html
    assert 'cc-preview-images is-hidden' not in html


def test_character_admin_preview_has_empty_state_on_add_page():
    """画像未選択の追加画面では案内文を表示する。"""
    character_admin = CharacterAdmin(model=Character, admin_site=AdminSite())

    html = str(character_admin.current_image_preview(None))

    assert '画像はまだ設定されていません' in html
    assert 'cc-preview-images is-hidden' in html
    assert 'src=""' not in html


@pytest.mark.django_db
def test_character_add_page_contains_empty_image_preview(client, admin_user):
    """新規追加画面にプレビュー領域が出力される。"""
    client.force_login(admin_user)

    response = client.get(reverse('admin:blog_character_add'))

    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="cc-admin-preview"' in html
    assert '画像はまだ設定されていません' in html


@pytest.mark.django_db
def test_character_change_page_contains_current_image_preview(client, admin_user):
    """編集画面に保存済み画像のURLとプレビューが出力される。"""
    character = Character.objects.create(
        name='ココ',
        slug='coco-preview-test',
        image='characters/coco.png',
    )
    client.force_login(admin_user)

    response = client.get(
        reverse('admin:blog_character_change', args=[character.pk])
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="cc-admin-preview"' in html
    assert html.count('/media/characters/coco.png') >= 2
    assert '現在保存されている画像' in html


# ------------------------------------------------------------
# /admin-preview/ エンドポイント
# ------------------------------------------------------------

@pytest.mark.django_db
def test_post_add_page_uses_versioned_editor_script(client, admin_user):
    """30日キャッシュ中でも行同期の新しいJSを読み込める。"""
    client.force_login(admin_user)

    response = client.get(reverse('admin:blog_post_add'))

    assert response.status_code == 200
    assert '/static/blog/admin_editor.js?v=20261103-safe-characters' in (
        response.content.decode()
    )


@pytest.mark.django_db
def test_admin_preview_requires_staff(client):
    """匿名アクセスはログイン画面にリダイレクト（302）。"""
    response = client.post('/admin-preview/', {'content': '# テスト'})
    assert response.status_code in (302, 403)  # ログインリダイレクト or 拒否


@pytest.mark.django_db
def test_admin_preview_returns_rendered_html(client, admin_user):
    """staff ログイン中なら Markdown を HTML に変換して返す。"""
    client.force_login(admin_user)
    response = client.post('/admin-preview/', {
        'content': '## 見出し\n\n本文',
        'title': 'プレビューテスト',
    })
    assert response.status_code == 200
    content = response.content.decode()
    assert '<h2' in content
    assert '本文' in content


@pytest.mark.django_db
def test_admin_preview_normalizes_crlf(client, admin_user):
    """Toast UI Editor が送る CRLF も正しく処理される。"""
    client.force_login(admin_user)
    response = client.post('/admin-preview/', {
        'content': '## 見出し\r\n\r\n本文',
        'title': 'CRLF テスト',
    })
    assert response.status_code == 200
    assert '<h2' in response.content.decode()


@pytest.mark.django_db
def test_admin_preview_renders_display_math_with_source_line(client, admin_user):
    """CRLFの数式も組版用HTMLへ変換し、開始行を保つ。"""
    client.force_login(admin_user)
    response = client.post('/admin-preview/', {
        'content': '前文\r\n\r\n$$\r\n\\boxed{x}\r\n$$',
        'title': '数式プレビュー',
    })

    content = response.content.decode()
    assert response.status_code == 200
    assert 'class="arithmatex" data-source-line="3"' in content
    assert r'\boxed{x}' in content
    assert 'mathjax@4.1.3/tex-chtml.js' in content
    assert "load: ['ui/safe']" in content
    assert "URLs: 'none'" in content
    assert 'bootstrap.bundle.min.js' not in content
    assert 'googletagmanager.com' not in content
    assert '/static/blog/toc.js' not in content
    assert '/static/blog/code_copy.js' not in content
    assert 'application/ld+json' not in content


@pytest.mark.django_db
def test_admin_preview_rejects_get(client, admin_user):
    """GET は受け付けない（@require_POST）。"""
    client.force_login(admin_user)
    response = client.get('/admin-preview/')
    assert response.status_code == 405  # Method Not Allowed


# ------------------------------------------------------------
# /admin-image-upload/ の POST バリデーション
# ------------------------------------------------------------

@pytest.mark.django_db
def test_admin_image_upload_rejects_invalid_quality(client, admin_user):
    """quality に非数値を送ったら 400 を返す（500 にしない）。"""
    from django.core.files.uploadedfile import SimpleUploadedFile
    client.force_login(admin_user)
    fake_image = SimpleUploadedFile('test.png', b'\x89PNG\r\n', content_type='image/png')
    response = client.post('/admin-image-upload/', {
        'image': fake_image,
        'alt': 'test',
        'quality': 'not-a-number',   # ← 不正値
        'max_width': '1600',
    })
    # 500 ではなく 400 で返ることを担保（クライアント由来の壊れた値で落ちない）
    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_image_upload_rejects_invalid_max_width(client, admin_user):
    """max_width に非数値を送ったら 400 を返す。"""
    from django.core.files.uploadedfile import SimpleUploadedFile
    client.force_login(admin_user)
    fake_image = SimpleUploadedFile('test.png', b'\x89PNG\r\n', content_type='image/png')
    response = client.post('/admin-image-upload/', {
        'image': fake_image,
        'alt': 'test',
        'quality': '85',
        'max_width': 'huge',   # ← 不正値
    })
    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_image_upload_rejects_non_image_file(client, admin_user):
    """画像として開けないファイル（SVG偽装・拡張子偽装等）は 400 で弾く。"""
    from django.core.files.uploadedfile import SimpleUploadedFile
    client.force_login(admin_user)
    not_an_image = SimpleUploadedFile(
        'fake.png',
        b'this is not really an image, just text bytes',
        content_type='image/png',
    )
    response = client.post('/admin-image-upload/', {
        'image': not_an_image,
        'alt': 'fake',
    })
    assert response.status_code == 400

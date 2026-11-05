"""ビューのHTTPステータスと振る舞いをテストする。"""

import re

import pytest
# ↓ ファイル先頭の import 群に追加
from blog.tests.factories import DraftPostFactory, PostFactory
from blog.models import Category

# ------------------------------------------------------------
# index ビュー
# ------------------------------------------------------------

@pytest.mark.django_db
def test_index_returns_200(client):
    """トップページは常に200を返す。"""
    response = client.get('/')
    assert response.status_code == 200


@pytest.mark.django_db
def test_index_shows_published_posts(client, post, draft_post):
    """トップページには公開記事だけが含まれる。"""
    response = client.get('/')
    content = response.content.decode()
    assert post.title in content
    assert draft_post.title not in content


# ------------------------------------------------------------
# detail ビュー（公開記事）
# ------------------------------------------------------------

@pytest.mark.django_db
def test_detail_returns_200_for_published(client, post):
    """公開記事の詳細は200。"""
    response = client.get(post.get_absolute_url())
    assert response.status_code == 200


@pytest.mark.django_db
def test_detail_renders_math_and_loads_mathjax(client):
    """公開記事はTeXを数式ラッパーに変換し、MathJax本体も読み込む。"""
    post = PostFactory(
        title='M/M/1の平均待ち時間',
        slug='mathjax-detail-test',
        body='$$\nW_q = \\frac{\\rho}{\\mu - \\lambda}\n$$',
    )

    response = client.get(post.get_absolute_url())
    content = response.content.decode()

    assert response.status_code == 200
    assert 'class="arithmatex" data-source-line="1"' in content
    assert r'\frac{\rho}{\mu - \lambda}' in content
    assert 'mathjax@4.1.3/tex-chtml.js' in content
    # CSSの更新日が変わっても、キャッシュ更新用の版を付けて読み込めていればよい。
    assert re.search(r'<link[^>]+href="/static/blog/blog\.css\?v=[^"]+"', content)


# ------------------------------------------------------------
# detail ビュー（未公開記事）— セキュリティの核
# ------------------------------------------------------------

@pytest.mark.django_db
def test_detail_returns_404_for_draft_to_anonymous(client, draft_post):
    """匿名ユーザーは未公開記事を見られない（404）。"""
    response = client.get(draft_post.get_absolute_url())
    assert response.status_code == 404


@pytest.mark.django_db
def test_detail_returns_200_for_draft_to_staff(client, draft_post, staff_user):
    """管理者ログイン中なら未公開記事もプレビューできる（第5章の機能）。"""
    client.force_login(staff_user)
    response = client.get(draft_post.get_absolute_url())
    assert response.status_code == 200


@pytest.mark.django_db
def test_detail_returns_404_for_draft_to_logged_in_non_staff(client, draft_post, regular_user):
    """is_staff でない一般ユーザーは未公開記事を見られない。"""
    client.force_login(regular_user)
    response = client.get(draft_post.get_absolute_url())
    assert response.status_code == 404


# ↓ ファイル末尾に追加
# ------------------------------------------------------------
# view_count の F() インクリメント
# ------------------------------------------------------------

@pytest.mark.django_db
def test_view_count_increments_on_detail_access(client, post):
    """詳細ページを叩くと view_count が +1 される（F()式）。"""
    initial = post.view_count
    client.get(post.get_absolute_url())
    post.refresh_from_db()
    assert post.view_count == initial + 1


@pytest.mark.django_db
def test_view_count_increments_multiple_times(client, post):
    """連続アクセスで正しくカウントが増える。"""
    initial = post.view_count
    for _ in range(3):
        client.get(post.get_absolute_url())
    post.refresh_from_db()
    assert post.view_count == initial + 3


# ------------------------------------------------------------
# 検索（Qオブジェクト）
# ------------------------------------------------------------

@pytest.mark.django_db
def test_search_finds_post_by_title(client):
    """タイトルで検索ヒット。"""
    PostFactory(title='Docker入門ガイド', slug='docker-guide')
    response = client.get('/search/?q=Docker')
    assert response.status_code == 200
    assert 'Docker入門ガイド' in response.content.decode()


@pytest.mark.django_db
def test_search_finds_post_by_body(client):
    """本文で検索ヒット（Qオブジェクトで title / summary / body のOR検索）。"""
    PostFactory(title='まったく無関係なタイトル', slug='unrelated', body='Docker について')
    response = client.get('/search/?q=Docker')
    assert response.status_code == 200
    assert 'まったく無関係なタイトル' in response.content.decode()


@pytest.mark.django_db
def test_search_case_insensitive(client):
    """検索は大文字小文字を区別しない（icontains）。"""
    PostFactory(title='Django Tutorial', slug='django-tut')
    for query in ['django', 'DJANGO', 'Django']:
        response = client.get(f'/search/?q={query}')
        assert 'Django Tutorial' in response.content.decode(), \
            f'クエリ {query} でヒットしなかった'


@pytest.mark.parametrize('url,expected_status', [
    ('/', 200),
    ('/posts/nonexistent-slug/', 404),
    ('/category/nonexistent/', 404),  # 存在しないカテゴリは get_object_or_404 で404
    ('/search/?q=anything', 200),
    ('/robots.txt', 200),
    ('/sitemap.xml', 200),
])
@pytest.mark.django_db
def test_basic_url_status(client, url, expected_status):
    """主要URLが期待のステータスコードを返す。"""
    response = client.get(url)
    assert response.status_code == expected_status, \
        f'URL {url} で {expected_status} を期待したが {response.status_code} だった'

@pytest.mark.slow
@pytest.mark.django_db
def test_pagination_with_many_posts(client):
    """100件記事を作ってページネーション検証（遅い）。"""
    for _ in range(100):
        PostFactory()
    response = client.get('/?page=2')
    assert response.status_code == 200



# ------------------------------------------------------------
# カテゴリビュー
# ------------------------------------------------------------

@pytest.mark.django_db
def test_category_returns_200_for_existing(client, category):
    """存在するカテゴリページは 200 を返す。"""
    response = client.get(f'/category/{category.slug}/')
    assert response.status_code == 200


@pytest.mark.django_db
def test_category_returns_404_for_nonexistent(client):
    """存在しないカテゴリは 404 を返す（get_object_or_404）。"""
    response = client.get('/category/totally-fake-slug/')
    assert response.status_code == 404


@pytest.mark.django_db
def test_category_shows_only_posts_in_category(client, category):
    """カテゴリページの post_list には、そのカテゴリの記事だけが含まれる。

    HTML文字列で判定すると、サイドバーの「最近の投稿」等に外カテゴリ記事が
    出てしまい誤判定する。view が page_obj に詰めた中身そのものを検証する。
    """
    PostFactory(title='カテゴリ内記事', slug='in-cat', category=category)
    PostFactory(title='カテゴリ外記事', slug='out-cat')
    response = client.get(f'/category/{category.slug}/')
    titles = [p.title for p in response.context['post_list']]
    assert 'カテゴリ内記事' in titles
    assert 'カテゴリ外記事' not in titles


# ------------------------------------------------------------
# タグビュー
# ------------------------------------------------------------

@pytest.mark.django_db
def test_tag_returns_200_for_existing(client, tag):
    """存在するタグページは 200 を返す。"""
    response = client.get(f'/tag/{tag.slug}/')
    assert response.status_code == 200


@pytest.mark.django_db
def test_tag_returns_404_for_nonexistent(client):
    """存在しないタグは 404 を返す（get_object_or_404）。"""
    response = client.get('/tag/totally-fake-tag/')
    assert response.status_code == 404


@pytest.mark.django_db
def test_tag_shows_only_posts_with_tag(client, tag):
    """タグページの post_list には、そのタグが付いた記事だけが含まれる。"""
    tagged = PostFactory(title='タグ付き記事', slug='with-tag')
    tagged.tags.add(tag)
    PostFactory(title='タグ無し記事', slug='without-tag')
    response = client.get(f'/tag/{tag.slug}/')
    titles = [p.title for p in response.context['post_list']]
    assert 'タグ付き記事' in titles
    assert 'タグ無し記事' not in titles


@pytest.mark.django_db
def test_tag_page_is_noindex(client, tag):
    """タグページは検索エンジンに noindex を返す（薄いページ対策）。"""
    response = client.get(f'/tag/{tag.slug}/')
    assert 'noindex' in response.content.decode()


@pytest.mark.django_db
def test_category_page_is_not_noindex(client, category):
    """カテゴリページは noindex にしない（従来どおりインデックスさせる）。"""
    response = client.get(f'/category/{category.slug}/')
    assert 'noindex' not in response.content.decode()


# ------------------------------------------------------------
# 検索の境界ケース
# ------------------------------------------------------------

@pytest.mark.django_db
def test_search_with_empty_query_returns_200(client):
    """空クエリでも 200 を返す（落ちない）。"""
    response = client.get('/search/?q=')
    assert response.status_code == 200


@pytest.mark.django_db
def test_search_without_q_param_returns_200(client):
    """q パラメータ無しでも 200 を返す。"""
    response = client.get('/search/')
    assert response.status_code == 200


@pytest.mark.django_db
def test_search_finds_post_by_summary(client):
    """要約（summary）でも検索ヒット（title / summary / body の OR 検索）。"""
    PostFactory(
        title='普通のタイトル', slug='by-summary',
        summary='Docker のことを書いた要約', body='本文',
    )
    response = client.get('/search/?q=Docker')
    assert '普通のタイトル' in response.content.decode()


@pytest.mark.django_db
def test_search_excludes_unpublished_posts(client):
    """未公開記事は検索結果に出ない（情報漏洩防止）。"""
    DraftPostFactory(title='下書き Docker 記事', slug='draft-docker', body='Docker')
    response = client.get('/search/?q=Docker')
    assert '下書き Docker 記事' not in response.content.decode()


# ------------------------------------------------------------
# robots.txt / sitemap.xml
# ------------------------------------------------------------

@pytest.mark.django_db
def test_robots_txt_returns_plain_text(client):
    """robots.txt は text/plain で返る。"""
    response = client.get('/robots.txt')
    assert response.status_code == 200
    assert response['Content-Type'].startswith('text/plain')
    content = response.content.decode()
    assert 'User-agent' in content


@pytest.mark.django_db
def test_sitemap_xml_returns_xml(client, post):
    """sitemap.xml は XML 形式で返る。"""
    response = client.get('/sitemap.xml')
    assert response.status_code == 200
    assert 'xml' in response['Content-Type']
    content = response.content.decode()
    assert post.get_absolute_url() in content


# ------------------------------------------------------------
# DisplaySlot：下書き漏洩防止（セキュリティ回帰テスト）
# ------------------------------------------------------------
# 管理画面で「下書き記事」を Featured / Pickup スロットに紐付けても、
# トップページに露出してはいけない。過去にこの漏洩を許す実装になっていた。

@pytest.mark.django_db
def test_featured_slot_with_draft_post_does_not_leak(client, draft_post):
    """下書き記事を Featured スロットに入れても、トップに表示されない。"""
    from blog.models import DisplaySlot
    DisplaySlot.objects.create(
        slot_type=DisplaySlot.SLOT_FEATURED,
        post=draft_post,
        order=0,
    )
    response = client.get('/')
    content = response.content.decode()
    assert draft_post.title not in content


@pytest.mark.django_db
def test_pickup_slot_with_draft_post_does_not_leak(client, draft_post):
    """下書き記事を Pickup スロットに入れても、トップに表示されない。"""
    from blog.models import DisplaySlot
    DisplaySlot.objects.create(
        slot_type=DisplaySlot.SLOT_PICKUP,
        post=draft_post,
        order=0,
    )
    response = client.get('/')
    content = response.content.decode()
    assert draft_post.title not in content

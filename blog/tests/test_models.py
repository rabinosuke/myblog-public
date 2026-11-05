import pytest
from django.utils import timezone

from blog.models import Post, Tag


@pytest.mark.django_db
def test_post_str_returns_title():
    """Post の __str__ がタイトルを返す。"""
    post = Post.objects.create(title='テスト記事', slug='test-post', body='本文')
    assert str(post) == 'テスト記事'


@pytest.mark.django_db
def test_tag_str_returns_name():
    """Tag の __str__ がタグ名を返す。"""
    tag = Tag.objects.create(name='Django', slug='django')
    assert str(tag) == 'Django'


@pytest.mark.django_db
def test_post_can_have_multiple_tags():
    """1記事に複数タグを付けられ、双方向に参照できる。"""
    post = Post.objects.create(title='複数タグ記事', slug='multi-tag', body='本文')
    t1 = Tag.objects.create(name='Docker', slug='docker')
    t2 = Tag.objects.create(name='Git', slug='git')
    post.tags.add(t1, t2)
    assert post.tags.count() == 2
    # 逆参照（related_name='posts'）も効く
    assert post in t1.posts.all()


@pytest.mark.django_db
def test_get_absolute_url_uses_slug():
    """get_absolute_url が /posts/<slug>/ を返す。"""
    post = Post.objects.create(title='記事A', slug='article-a', body='本文')
    assert post.get_absolute_url() == '/posts/article-a/'


@pytest.mark.django_db
def test_meta_ordering_puts_newest_first():
    """Meta.ordering で published_at の新しい順に並ぶ。"""
    old = Post.objects.create(
        title='古い記事',
        slug='old',
        body='本文',
        is_published=True,
        published_at=timezone.now() - timezone.timedelta(days=2),
    )
    new = Post.objects.create(
        title='新しい記事',
        slug='new',
        body='本文',
        is_published=True,
        published_at=timezone.now(),
    )
    posts = list(Post.objects.all())
    assert posts[0].pk == new.pk
    assert posts[1].pk == old.pk


@pytest.mark.django_db
def test_index_queryset_pushes_null_to_end(client):
    """index ビューでは published_at が NULL の記事を必ず末尾に追いやる。

    Meta.ordering = ['-published_at', '-id'] だけだと MySQL では NULL が先頭に来てしまう。
    そこで views.index は F('published_at').desc(nulls_last=True) を使っている。
    その効果を「ビュー経由」で担保する。
    """
    null_post = Post.objects.create(
        title='公開日時なし', slug='null', body='本文', is_published=True,
    )
    with_date = Post.objects.create(
        title='公開日時あり', slug='with-date', body='本文',
        is_published=True, published_at=timezone.now(),
    )
    response = client.get('/')
    content = response.content.decode()
    # 公開日時あり記事の方が、公開日時なし記事より先に登場するはず
    assert content.index('公開日時あり') < content.index('公開日時なし')

"""
sitemap.xml 生成用の設定。

Google などの検索エンジンに「このサイトにはこんなURLがあります」と
一覧で伝えるためのXMLを自動生成する。
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Category, Post


class PostSitemap(Sitemap):
    """記事ページのサイトマップ。"""

    # 更新頻度。Googleへのヒントになる
    changefreq = 'weekly'
    # 優先度（0.0〜1.0）。サイト内での重要度
    priority = 0.8

    def items(self):
        """サイトマップに含めるオブジェクトのリスト。

        Sitemap のページネーション安定のため明示的に order_by を付ける
        （Django が UnorderedObjectListWarning を出さないように）。
        """
        return Post.objects.filter(is_published=True).order_by('-published_at', '-id')

    def lastmod(self, obj):
        """各URLの最終更新日。"""
        return obj.updated_at


class CategorySitemap(Sitemap):
    """カテゴリページのサイトマップ。"""

    changefreq = 'weekly'
    priority = 0.5

    def items(self):
        # ページネーション安定のため order_by を明示
        return Category.objects.order_by('id')

    def location(self, obj):
        """各オブジェクトのURLを返す。get_absolute_urlを定義しないモデルではこちらを使う。"""
        return reverse('blog:category', kwargs={'slug': obj.slug})


class StaticViewSitemap(Sitemap):
    """固定ページ（トップページ等）のサイトマップ。"""

    changefreq = 'daily'
    priority = 1.0

    def items(self):
        # URL名のリストを返す
        return ['blog:index']

    def location(self, item):
        return reverse(item)


# myblog/urls.py から参照する辞書
sitemaps = {
    'posts': PostSitemap,
    'categories': CategorySitemap,
    'static': StaticViewSitemap,
}

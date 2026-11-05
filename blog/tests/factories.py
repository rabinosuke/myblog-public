"""テスト用データ生成ファクトリ。"""

import factory
from django.utils import timezone

from blog.models import Category, Post, Tag


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f'カテゴリ{n}')
    slug = factory.Sequence(lambda n: f'category-{n}')


class TagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tag

    name = factory.Sequence(lambda n: f'タグ{n}')
    slug = factory.Sequence(lambda n: f'tag-{n}')


class PostFactory(factory.django.DjangoModelFactory):
    """Post のテストデータを楽に作る。"""

    class Meta:
        model = Post

    title = factory.Sequence(lambda n: f'記事{n}')
    slug = factory.Sequence(lambda n: f'post-{n}')
    body = '本文サンプル'
    summary = '要約サンプル'
    is_published = True
    published_at = factory.LazyFunction(timezone.now)


class DraftPostFactory(PostFactory):
    """下書き記事専用ファクトリ。"""
    is_published = False
    published_at = None

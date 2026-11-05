"""記事資料の公開範囲・抜粋の上限・コードの保持を検証する。"""

import pytest
from django.template.loader import render_to_string

from blog.services.coco_context import excerpt_body, get_article_context
from .factories import PostFactory

pytestmark = pytest.mark.django_db


def test_short_article_keeps_markdown_and_metadata():
    body = '# 使い方\n\n```python\n# コメント\nprint("hello")\n\nprint("world")\n```'
    post = PostFactory(title='コードの例', summary='サンプルです', body=body)
    article = get_article_context(post.slug, 'このコードの意味は？', [])
    assert article == {'title': post.title, 'url': post.get_absolute_url(), 'summary': post.summary,
                       'body': body, 'body_is_excerpt': False}


def test_long_article_can_select_a_topic_near_the_end(settings):
    settings.COCO_CHAT_MAX_ARTICLE_CHARS = 1600
    body = '\n\n'.join(f'## 日記{i}\n\n' + '今日は晴れです。' * 80 for i in range(15))
    body += '\n\n## csrf_token の使い方\n\nフォームにcsrf_tokenを入れる。\n\n```html\n<form>\n{% csrf_token %}\n</form>\n```'
    post = PostFactory(body=body)
    article = get_article_context(post.slug, 'csrf_token はどこに書くの？', [])
    assert len(article['body']) <= 1600
    assert article['body_is_excerpt'] is True
    assert '## csrf_token の使い方' in article['body']
    assert '{% csrf_token %}' in article['body']
    assert '```html' in article['body']


def test_followup_uses_previous_user_topic(settings):
    settings.COCO_CHAT_MAX_ARTICLE_CHARS = 1500
    post = PostFactory(body=('## 前置き\n\n' + '雑談。' * 4000) + '\n\n## キャッシュ\n\nRedisはメモリに保存する。')
    article = get_article_context(post.slug, 'もう少し詳しく', [{'role': 'user', 'content': 'Redisは何？'}])
    assert 'Redisはメモリに保存する。' in article['body']


def test_summary_samples_beginning_and_end_within_budget():
    body = '\n\n'.join(f'## 章{i}\n\n' + f'話題{i}。' * 200 for i in range(15))
    excerpt, partial = excerpt_body(body, 'この記事を要約して', 6000)
    assert partial and len(excerpt) <= 6000
    assert '章0' in excerpt and '章14' in excerpt


def test_large_fenced_code_keeps_its_language_and_closing_fence():
    body = '## コード\n\n````python\n' + '\n'.join(f'value_{i} = {i}' for i in range(1000)) + '\n````'
    excerpt, partial = excerpt_body(body, 'value_999 は？', 1600)
    assert partial and len(excerpt) <= 1600
    assert 'value_999 = 999' in excerpt
    assert excerpt.count('````') % 2 == 0
    assert '````python' in excerpt


def test_reference_ui_is_only_attached_to_public_article(post, draft_post):
    public = render_to_string('blog/_coco_chat.html', {'post': post})
    private = render_to_string('blog/_coco_chat.html', {'post': draft_post})
    general = render_to_string('blog/_coco_chat.html')
    assert f'data-article-slug="{post.slug}"' in public
    assert '参照中：' in public and post.title in public
    for html in (private, general):
        assert 'data-article-slug=' not in html
        assert '参照中：' not in html

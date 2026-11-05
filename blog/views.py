"""
blogアプリのビュー。

URL からリクエストを受け取り、DBからデータを取得してテンプレートに渡す。
"""

import os
import uuid
import logging

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Category, DisplaySlot, Post, Tag
from .services.upload_images import prepare_image
from .templatetags.markdown_tags import render_post_markdown

# 一覧ページの1ページあたりの表示件数
POSTS_PER_PAGE = 10

logger = logging.getLogger(__name__)


def _common_context():
    """全ページ共通のコンテキスト（サイドバー用）を返す。"""

    return {
        # サイドバー：よく読まれている記事
        'popular_posts': (
            Post.objects
            .filter(is_published=True)
            .order_by('-view_count')[:3]
        ),
        # サイドバー：カテゴリ一覧
        'categories': Category.objects.all(),
    }


def _paginate(request, queryset):
    """指定された QuerySet をページネーションして返す。"""

    paginator = Paginator(queryset, POSTS_PER_PAGE)
    # GET パラメータ ?page=2 を取得。範囲外なら最後のページにフォールバック
    return paginator.get_page(request.GET.get('page'))


def index(request):
    """トップページ（記事一覧）を表示する。"""

    # 公開済みの記事を公開日時の新しい順に取得する。
    # published_at が NULL の記事はMySQLでDESC指定するとデフォルトで先頭に来てしまうので、
    # 必ず末尾（nulls_last）に追いやって表示順が崩れないようにする。
    published = (
        Post.objects
        .filter(is_published=True)
        .order_by(F('published_at').desc(nulls_last=True))
    )

    # 特集バナー：管理画面で選ばれたスロットの先頭1件
    # スロットが未設定の場合は最新の公開記事を代わりに表示する
    # `post__is_published=True` で「下書き記事をスロットに紐付け」した時の漏洩を防ぐ
    featured_slot = (
        DisplaySlot.objects
        .filter(slot_type=DisplaySlot.SLOT_FEATURED, post__is_published=True)
        .select_related('post')
        .first()
    )
    featured = featured_slot.post if featured_slot else published.first()

    # ピックアップ：管理画面で選ばれた記事を表示順に並べる
    # `post__is_published=True` で下書き漏洩を防ぐ
    pickup = (
        DisplaySlot.objects
        .filter(slot_type=DisplaySlot.SLOT_PICKUP, post__is_published=True)
        .select_related('post')
        .order_by('order')
    )
    pickup_posts = [slot.post for slot in pickup]

    # 記事一覧：特集・ピックアップを除いた公開記事をページネーションする
    # （上部で大きく見せている記事を最新一覧に再掲すると冗長になるため）
    # featured が None でも pickup は確実に除外する（演算子優先順位の罠を回避）
    exclude_pks = [p.pk for p in pickup_posts]
    if featured:
        exclude_pks.append(featured.pk)
    post_list = published.exclude(pk__in=exclude_pks)
    page_obj = _paginate(request, post_list)

    context = {
        'featured': featured,
        'pickup': pickup_posts,
        'page_obj': page_obj,
        'post_list': page_obj.object_list,
        **_common_context(),
    }
    return render(request, 'blog/index.html', context)


def detail(request, slug):
    """記事詳細ページを表示する。"""
    logger.info(f'Article viewed: slug={slug}') 

    # 公開済みの記事のみ表示。未公開はURL直打ちでも404
    # ただし管理者（is_staff）はログインしていれば未公開記事もプレビューできる
    if request.user.is_authenticated and request.user.is_staff:
        post = get_object_or_404(Post, slug=slug)
    else:
        post = get_object_or_404(Post, slug=slug, is_published=True)

    # 閲覧数を1加算する
    # F()式を使うことで、同時アクセスでもカウントがズレない
    # （DBレベルでアトミックに +1 する）
    Post.objects.filter(pk=post.pk).update(view_count=F('view_count') + 1)

    # 前後の記事：公開日時を基準に検索する
    # 未公開（プレビュー時）は published_at が None なので前後記事なし
    prev_post = None
    next_post = None
    if post.published_at:
        # 前の記事：公開日時がこの記事より古いもののうち最新
        prev_post = (
            Post.objects
            .filter(is_published=True, published_at__lt=post.published_at)
            .order_by('-published_at')
            .first()
        )
        # 次の記事：公開日時がこの記事より新しいもののうち最古
        next_post = (
            Post.objects
            .filter(is_published=True, published_at__gt=post.published_at)
            .order_by('published_at')
            .first()
        )

    # 関連記事：同じカテゴリで、自分以外の最新3件
    related_posts = []
    if post.category:
        related_posts = (
            Post.objects
            .filter(is_published=True, category=post.category)
            .exclude(pk=post.pk)
            .order_by('-published_at')[:3]
        )

    # 本文と目次を1回の変換で生成する（重い処理を二度実行しない）
    body_html, toc_html = render_post_markdown(post.body)

    context = {
        'post': post,
        'body_html': body_html,
        'toc_html': toc_html,
        'prev_post': prev_post,
        'next_post': next_post,
        'related_posts': related_posts,
        **_common_context(),
    }
    return render(request, 'blog/detail.html', context)


def category(request, slug):
    """カテゴリ別の記事一覧ページを表示する。"""

    # URLのslugでカテゴリを取得。存在しなければ404
    category = get_object_or_404(Category, slug=slug)

    # このカテゴリに属する公開済み記事を新しい順にページネーション
    post_list = (
        Post.objects
        .filter(is_published=True, category=category)
        .order_by('-published_at')
    )
    page_obj = _paginate(request, post_list)

    context = {
        # 一覧テンプレートを使い回すため共通の変数名で渡す
        'page_title': f'カテゴリ：{category.name}',
        'page_obj': page_obj,
        'post_list': page_obj.object_list,
        **_common_context(),
    }
    return render(request, 'blog/list.html', context)


def tag(request, slug):
    """タグ別の記事一覧ページを表示する。

    タグ一覧は「記事リンクの羅列」で中身が薄く、重複コンテンツ評価に
    つながりやすいため、検索エンジンには noindex で返す（回遊専用ページ）。
    記事本体・カテゴリ一覧は従来どおりインデックスさせる。
    """

    # URLのslugでタグを取得。存在しなければ404
    tag = get_object_or_404(Tag, slug=slug)

    # このタグが付いた公開済み記事を新しい順にページネーション
    post_list = (
        Post.objects
        .filter(is_published=True, tags=tag)
        .order_by('-published_at')
    )
    page_obj = _paginate(request, post_list)

    context = {
        # 一覧テンプレートを使い回すため共通の変数名で渡す
        'page_title': f'タグ：{tag.name}',
        'page_obj': page_obj,
        'post_list': page_obj.object_list,
        # list.html 側で <meta name="robots" content="noindex,follow"> を出す
        'noindex': True,
        **_common_context(),
    }
    return render(request, 'blog/list.html', context)


def search(request):
    """キーワード検索ページを表示する。"""

    # GETパラメータ q を取得（未指定なら空文字）
    query = request.GET.get('q', '').strip()
    logger.info(f'Search executed: query={query!r}') 

    # キーワードが入力されている場合のみ検索する
    # Q オブジェクトで title・summary・body の OR検索を実現する
    if query:
        post_list = (
            Post.objects
            .filter(is_published=True)
            .filter(
                Q(title__icontains=query)
                | Q(summary__icontains=query)
                | Q(body__icontains=query)
            )
            .order_by('-published_at')
        )
    else:
        post_list = Post.objects.none()

    page_obj = _paginate(request, post_list)

    context = {
        'page_title': f'検索結果：{query}' if query else '検索',
        'query': query,
        'page_obj': page_obj,
        'post_list': page_obj.object_list,
        **_common_context(),
    }
    return render(request, 'blog/list.html', context)


def robots_txt(request):
    """robots.txt を返す。

    クローラーに「全ページOK」「sitemap.xmlはここ」と伝える。
    """
    sitemap_url = request.build_absolute_uri('/sitemap.xml')
    content = (
        'User-agent: *\n'
        'Allow: /\n'
        f'Sitemap: {sitemap_url}\n'
    )
    return HttpResponse(content, content_type='text/plain')


@staff_member_required
@require_POST
def admin_image_upload(request):
    """管理画面の Toast UI Editor から画像をアップロードして保存する。

    クライアント（Cropper.js）から渡される情報：
        image       : 画像ファイル（クロップ後）
        alt         : 代替テキスト（SEO・アクセシビリティ）
        quality     : JPEG品質（1-100, デフォルト85）
        max_width   : 最大横幅（デフォルト1600）

    クロップ自体はクライアント側（Cropper.js）で行い、
    切り抜き済みのblobが送られてくる前提。サーバーは品質調整と最終リサイズのみ。
    """
    image_file = request.FILES.get('image')
    if not image_file:
        return JsonResponse({'error': 'No image provided'}, status=400)

    alt_text = request.POST.get('alt', '').strip()

    # quality / max_width は POST 由来なので「数値じゃない・範囲外」を必ず弾く。
    # スタッフ限定とはいえ、雑なクライアントから飛んできた値で 500 を返すのは
    # 運用上ノイズになるので、ここで型と範囲を確定させてしまう。
    try:
        quality = int(request.POST.get('quality', 85))
        max_width = int(request.POST.get('max_width', 1600))
    except (TypeError, ValueError):
        return JsonResponse(
            {'error': '画像設定の値が不正です（quality / max_width は整数）'},
            status=400,
        )
    quality = max(1, min(100, quality))         # 1〜100 にクランプ
    max_width = max(200, min(2400, max_width))  # 200〜2400 にクランプ

    # 実形式・容量・展開量を、保存先の作成より前に必ず検証する。
    try:
        img, ext = prepare_image(image_file, max_width)
    except ValidationError as error:
        return JsonResponse({'error': error.messages[0]}, status=400)

    try:
        now = timezone.now()
        folder = f'post_images/{now:%Y/%m}'
        save_dir = os.path.join(settings.MEDIA_ROOT, folder)
        os.makedirs(save_dir, exist_ok=True)
        filename = f'{uuid.uuid4().hex}{ext}'
        file_path = os.path.join(save_dir, filename)
        save_kwargs = {'optimize': True}
        if ext in ['.jpg', '.jpeg']:
            save_kwargs['quality'] = quality
            save_kwargs['progressive'] = True
        elif ext == '.png':
            save_kwargs['compress_level'] = 6

        img.save(file_path, **save_kwargs)
    except (OSError, ValueError):
        logger.warning('Image upload could not be saved')
        return JsonResponse({'error': '画像を保存できませんでした。時間をおいてお試しください。'}, status=400)
    finally:
        img.close()

    url = f'{settings.MEDIA_URL}{folder}/{filename}'
    return JsonResponse({'url': url, 'alt': alt_text})


@staff_member_required
def admin_character_list(request):
    """管理画面の Toast UI Editor が起動時に叩く JSON エンドポイント。

    is_active=True のキャラを sort_order 順で返す。
    エディタはこの結果から「左/右」2 ボタンずつ動的生成する。
    """
    from .models import Character
    chars = Character.objects.filter(is_active=True).order_by('sort_order', 'name')
    data = [
        {
            'slug': c.slug,
            'name': c.name,
            'image_url': c.image.url if c.image else '',
        }
        for c in chars
    ]
    return JsonResponse({'characters': data})


@staff_member_required
@require_POST
def admin_preview(request):
    """管理画面用：Markdown原稿を本番テンプレートで即時プレビューする。

    Toast UI Editor の編集内容を受け取って、実際の blog/detail.html と
    同じ見た目でHTMLを返す。iframe 経由で「本物そのまま」のプレビューが見える。
    """
    markdown_content = request.POST.get('content', '')
    title = request.POST.get('title', 'プレビュー')
    summary = request.POST.get('summary', '')

    # Toast UI Editor がCRLFで送ってくることがある。
    # Python-Markdown の nl2br は LF 前提なので必ず統一する
    markdown_content = markdown_content.replace('\r\n', '\n').replace('\r', '\n')

    # 一時的な Post オブジェクト（DBには保存しない）
    preview_post = Post(
        title=title,
        body=markdown_content,
        summary=summary,
        slug='__preview__',
        published_at=timezone.now(),
        view_count=0,
    )

    body_html, toc_html = render_post_markdown(markdown_content)

    context = {
        'post': preview_post,
        'body_html': body_html,
        'toc_html': toc_html,
        # iframe内ではMathJax以外のスクリプトやGAを読み込ませない。
        'is_admin_preview': True,
        'prev_post': None,
        'next_post': None,
        'related_posts': [],
        **_common_context(),
    }
    return render(request, 'blog/detail.html', context)


def page_not_found(request, exception):
    """カスタム 404 ページ。存在しないURLにアクセスされたときに表示される。"""
    return render(request, 'blog/404.html', _common_context(), status=404)


def server_error(request):
    """カスタム 500 ページ。サーバーエラー発生時に表示される。

    500エラーは context_processor が動かないこともあるため、
    必要最低限のコンテキストだけを渡す。
    """
    return render(request, 'blog/500.html', status=500)

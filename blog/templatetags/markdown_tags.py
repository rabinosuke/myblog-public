"""
Markdown を HTML に変換するテンプレートタグ。

テンプレートでの使い方:
    {% load markdown_tags %}
    {{ post.body|markdownify|safe }}

特徴:
- Qiita風の「```python:filename.py」記法に対応
- `$...$` / `$$...$$` などのTeX数式に対応
- Pygmentsでシンタックスハイライト
- ファイル名ラベル付きのコードブロックを生成
"""

import re

import bleach
import markdown as md
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe


# ============================================================
# bleach サニタイズ設定
# ============================================================
# 投稿者由来の HTML を「安全な要素・属性だけ」に絞り込む allowlist。
# 単独運用なら過剰だが、将来「複数人投稿」や「読者投稿」に拡張した時に
# XSS の入り口を残さないための保険。
# ----------------------------------------------------------------
# 注意：このリストには独自実装の要素（speech-bubble / codehilite / toc-list 等）
# も含めること。allowlist から漏れると吹き出しや目次の表示が壊れる。
# ============================================================

ALLOWED_TAGS = [
    # 基本ブロック
    'p', 'br', 'hr', 'div', 'span',
    # 見出し
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    # 段落整形
    'blockquote', 'pre', 'code',
    # リスト
    'ul', 'ol', 'li',
    # テーブル
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    # インライン装飾
    'strong', 'em', 'b', 'i', 'u', 's', 'del', 'ins', 'mark', 'sub', 'sup',
    # リンク・画像
    'a', 'img',
    # 引用・コード
    'cite', 'kbd', 'samp', 'var',
    # 吹き出し用（独自）
    'figure', 'figcaption',
    # ボタン（コピーボタン用）
    'button', 'svg', 'path', 'rect', 'circle',
    # 折りたたみ
    'details', 'summary',
]

ALLOWED_ATTRIBUTES = {
    '*': ['class', 'id', 'title', 'data-source-line', 'data-char', 'data-copy-attached', 'aria-label', 'aria-hidden'],
    'a': ['href', 'rel', 'target'],
    'img': ['src', 'alt', 'width', 'height', 'loading'],
    'th': ['scope', 'colspan', 'rowspan'],
    'td': ['colspan', 'rowspan'],
    'button': ['type'],
    'svg': ['xmlns', 'width', 'height', 'viewBox', 'fill', 'stroke', 'stroke-width',
            'stroke-linecap', 'stroke-linejoin'],
    'path': ['d'],
    'rect': ['x', 'y', 'width', 'height', 'rx', 'ry'],
    'circle': ['cx', 'cy', 'r'],
}

ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']


def _sanitize_html(html):
    """bleach allowlist でサニタイズしてXSS入り口を防ぐ。"""
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=False,  # 不許可タグはエスケープ表示（黙って消すと debug 困難）
    )

register = template.Library()


# Qiita風コードブロック「```language:filename」を検出する正規表現
# 例: ```python:views.py  → language=python, filename=views.py
CODE_BLOCK_PATTERN = re.compile(
    r'^```([\w+-]+):([^\n]+)\n(.*?)^```',
    re.MULTILINE | re.DOTALL,
)


# キャラクター吹き出し「:::coco ... :::」記法を検出する正規表現
# - `:::coco` 標準位置（左にアイコン、右に吹き出し）
# - `:::coco-r` 反転位置（右にアイコン、左に吹き出し）
# - 中身は複数行可。Markdownとして処理してから吹き出しに包む
BUBBLE_PATTERN = re.compile(
    r'^:::([a-z][a-z0-9-]*)\s*\n(.*?)^:::',
    re.MULTILINE | re.DOTALL,
)




# キャラクター情報は blog.Character モデルから取得する（DB管理）。
# 旧 CHARACTERS dict は 0007_seed_characters マイグレーションで DB 化済み。
# 増減・編集は管理画面 /admin/blog/character/ から行う。
#
# 参考URL:
#   - QuerySet.in_bulk(): https://docs.djangoproject.com/en/5.0/ref/models/querysets/#in-bulk
def _fetch_characters(slugs):
    """指定 slug 群のキャラを DB から一括取得する。

    記事 1 本に吹き出し 20 個出てきても DB クエリは 1 回で済む。
    未登録 slug は dict に入らない（呼び出し側で None フォールバック）。
    """
    # 循環 import 回避のため遅延 import（templatetags は models より先に読まれる）
    from blog.models import Character
    unique_slugs = list({s for s in slugs if s})
    if not unique_slugs:
        return {}
    return Character.objects.in_bulk(unique_slugs, field_name='slug')


def _replace_named_code_blocks(text):
    """「```lang:filename」記法を見つけて、後で復元できる目印に置き換える。

    通常のmarkdownライブラリはfilename部分を理解できないので、
    一旦取り出して、HTML変換後にファイル名ラベル付きで復元する。

    フェンスの入れ子に対応する（CommonMark準拠）。
    外側の長いフェンス（例: ````markdown）の“中”にある ```python:views.py は
    「記法を見せるための文字列」なので抽出せず、そのままコードとして表示する。
    トップレベルにある ```lang:filename だけを本物のファイル名ブロックとして取り出す。
    """
    lines = text.split('\n')
    out = []
    blocks = []
    open_fence = ''  # 今いる“通常”フェンスの記号（``` や ```` ~~~）。空ならトップレベル
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()
        m = re.match(r'^(`{3,}|~{3,})', stripped)

        if not m:
            out.append(line)
            i += 1
            continue

        marker = m.group(1)
        info = stripped[len(marker):].strip()

        if open_fence:
            # 外側フェンスの内側：閉じフェンス（同記号・同長以上・記号のみ）だけ反応
            if stripped == marker and marker[0] == open_fence[0] and len(marker) >= len(open_fence):
                open_fence = ''
            out.append(line)
            i += 1
            continue

        # ここはトップレベル
        named = re.match(r'^([\w+-]+):(.+)$', info)
        if marker[0] == '`' and named:
            # 本物のファイル名付きブロック → 同記号・同長以上の閉じフェンスまで収集
            language = named.group(1)
            filename = named.group(2).strip()
            code_lines = []
            i += 1
            while i < n:
                s2 = lines[i].strip()
                cm = re.match(r'^(`{3,}|~{3,})$', s2)
                if cm and s2[0] == marker[0] and len(s2) >= len(marker):
                    break
                code_lines.append(lines[i])
                i += 1
            idx = len(blocks)
            code = '\n'.join(code_lines) + '\n'
            blocks.append((language, filename, code))
            out.append('')
            out.append(f'@@CODE_BLOCK_PLACEHOLDER_{idx}@@')
            out.append('')
            i += 1  # 閉じフェンス行をスキップ
            continue

        # 通常のフェンス開始（````markdown / ```python / ``` 等）→ 中身は触らない
        open_fence = marker
        out.append(line)
        i += 1

    return '\n'.join(out), blocks


def _render_code_block(language, filename, code):
    """ファイル名ラベル付きのコードブロックをHTML化する。"""
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound

    try:
        lexer = get_lexer_by_name(language, stripall=True)
    except ClassNotFound:
        lexer = get_lexer_by_name('text', stripall=True)

    formatter = HtmlFormatter(cssclass='codehilite', nowrap=False)
    highlighted = highlight(code, lexer, formatter)

    # ファイル名ラベル付きの構造で出力（コピーボタンは JS で全コードブロックに統一付与）
    return (
        f'<div class="codehilite-wrap">'
        f'<div class="codehilite-filename">{escape(filename)}</div>'
        f'{highlighted}'
        f'</div>'
    )


def _replace_bubbles(text):
    """`:::coco ... :::` を見つけて、後で復元する目印に置き換える。

    中身の Markdown は通常通り変換させたいので、ここでは「中身だけ」を残し、
    HTML変換後に外側の吹き出しガワを被せる。
    """
    bubbles = []

    def store_bubble(match):
        char_key_raw = match.group(1)  # 例: coco, coco-r, coco-smile
        inner = match.group(2)
        idx = len(bubbles)
        bubbles.append((char_key_raw, inner))
        # ここでは「中身だけ」をプレースホルダで包んで残す。
        # こうしておけば中身の Markdown 変換は通常通り行われる
        return f'\n\n@@BUBBLE_START_{idx}@@\n\n{inner}\n\n@@BUBBLE_END_{idx}@@\n\n'

    new_text = BUBBLE_PATTERN.sub(store_bubble, text)
    return new_text, bubbles


def _parse_bubble_key(char_key_raw):
    """`:::xxx` の xxx 部分を (lookup_slug, is_right) に分解する。

    末尾の `-r` だけを「右配置」のサフィックスとして剥がし、
    残りはすべて Character.slug として DB 検索に使う。

    例:
        'coco'         → ('coco', False)
        'coco-r'       → ('coco', True)
        'coco-smile'   → ('coco-smile', False)
        'coco-smile-r' → ('coco-smile', True)

    こうすることで、表情バリエーション（coco-smile など）も別 Character として
    DB 登録すれば自動的に独自画像でレンダリングされる。
    """
    if char_key_raw.endswith('-r'):
        return char_key_raw[:-2], True
    return char_key_raw, False


def _render_bubble_wrapper(char_key_raw, idx, character_map):
    """吹き出しの外側HTMLを生成。中身は @@BUBBLE_INNER_{idx}@@ で後挿入。

    character_map: { slug: Character } の事前取得辞書（in_bulk で 1 クエリ済み）
    """
    lookup_slug, is_right = _parse_bubble_key(char_key_raw)

    character = character_map.get(lookup_slug)
    if not character:
        # 未登録キャラはスキップ。中身だけそのまま返す
        return None

    pos_class = 'speech-bubble-right' if is_right else 'speech-bubble-left'
    # MEDIA 配信。Character.image は ImageField なので .url で /media/... が得られる
    image_url = character.image.url if character.image else ''
    name = escape(character.name)

    return (
        f'<div class="speech-bubble {pos_class}" data-char="{lookup_slug}">'
        f'<div class="speech-bubble-icon">'
        f'<img src="{image_url}" alt="{name}">'
        f'<span class="speech-bubble-name">{name}</span>'
        f'</div>'
        f'<div class="speech-bubble-body">@@BUBBLE_INNER_{idx}@@</div>'
        f'</div>'
    )


def render_post_markdown(value):
    """記事本文用：Markdown → (body_html, toc_html) のタプルを返す。

    ビューから呼んで body と toc を別々のコンテキスト変数に入れて使う。
    1リクエスト1回だけ変換して両方使い回す目的。
    """
    body = markdownify(value)
    toc = extract_toc(body)
    return body, toc


def _math_closes_at_line_end(stripped, closer, opening_line=False):
    """Arithmatexが有効とみなす行末の数式閉じ記号を判定する。"""
    if not stripped.endswith(closer):
        return False
    if not opening_line:
        return True

    # `$$` は開始記号と終了記号が同じなので、開始側そのものを
    # 閉じ記号と数えず、開始記号より後にもう一つある場合だけ閉じる。
    if closer == '$$':
        return stripped.find(closer, len(closer)) >= 0
    if closer == r'\]':
        return stripped.find(closer, len(r'\[')) >= 0
    return stripped.find(closer) > 0


def _has_complete_math_block(lines, start_index, closer):
    """空行より前に閉じ記号がある、完成済みの数式ブロックか調べる。"""
    for index in range(start_index, len(lines)):
        stripped = lines[index].strip()
        if index > start_index and not stripped:
            # Arithmatexは数式ブロック内の空行を許さない。
            return False
        if _math_closes_at_line_end(
                stripped, closer, opening_line=(index == start_index)):
            return True
    return False


def _inject_line_markers(text):
    """Markdown ソースの各ブロック先頭に行番号マーカーを挿入する。

    後段の Markdown 変換が走った後で、HTML 側にこのマーカーを
    `data-source-line="N"` 属性として転写する仕組み（VS Code/Joplin式）。

    エディタとプレビュー間の双方向ジャンプで、テキスト一致ではなく
    行番号で対応を取るための準備。重複テキスト問題が原理的に発生しない。
    """
    lines = text.split('\n')
    output = []
    in_code_fence = False  # ```...``` ブロック内かどうか
    fence_marker = ''       # 開いているコードフェンスの記号（``` や ```` ~~~ 等）
    in_bubble = False       # :::coco ... ::: ブロック内かどうか
    math_closer = ''        # $$ / \] / \end{...} のどれで数式ブロックが閉じるか
    prev_blank = True       # 直前行が空行か（段落開始判定用）
    in_blockquote = False   # > が連続中か（連続中はマーカー入れない）
    in_list = False         # リストが連続中か（連続中はマーカー入れない）

    for i, line in enumerate(lines):
        line_num = i + 1
        stripped = line.strip()

        # ----- コードフェンス処理 -----
        # 入れ子フェンス（外側 ```markdown の中に ```python:views.py 等）を誤認しないため、
        # CommonMark 準拠で判定する：
        #   - 開始フェンスは情報文字列（```python:views.py）を付けられる
        #   - 閉じフェンスは「同じ記号・同じ以上の長さ・記号のみ（情報文字列なし）」だけ
        fence_match = re.match(r'^(`{3,}|~{3,})', stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not in_code_fence:
                # 開始フェンス → マーカーを入れて in_code_fence = True
                output.append(f'<!--MDLINE:{line_num}-->')
                in_code_fence = True
                fence_marker = marker
                output.append(line)
                prev_blank = False
                continue
            if (marker[0] == fence_marker[0]
                    and len(marker) >= len(fence_marker)
                    and stripped == marker):
                # 正しい閉じフェンス → 閉じるだけ（マーカーは入れない）
                in_code_fence = False
                fence_marker = ''
                output.append(line)
                prev_blank = False
                continue
            # それ以外のフェンス風行（入れ子の内側フェンス等）は
            # コード内容として扱う（下の in_code_fence 分岐に落とす）

        # コードブロック内の各行はそのまま追加（マーカー入れない）
        if in_code_fence:
            output.append(line)
            prev_blank = False
            continue

        # ----- 吹き出しブロック処理 -----
        # :::coco / :::coco-r で開始、単独の ::: で終了
        if re.match(r'^:::[a-z]', stripped):
            output.append(f'<!--MDLINE:{line_num}-->')
            output.append(line)
            in_bubble = True
            prev_blank = False
            continue
        if stripped == ':::' and in_bubble:
            # 吹き出し終了 → マーカーは入れずに閉じる
            output.append(line)
            in_bubble = False
            prev_blank = False
            continue

        # 吹き出し内ではマーカー入れない
        if in_bubble:
            output.append(line)
            prev_blank = (stripped == '')
            continue

        # ----- TeX数式ブロック処理 -----
        # 数式内の空行を通常段落と誤認して MDLINE を差し込むと、
        # Arithmatex が数式全体を認識できなくなる。閉じ記号までは素通しする。
        if math_closer:
            output.append(line)
            if _math_closes_at_line_end(stripped, math_closer):
                math_closer = ''
            prev_blank = False
            continue

        if prev_blank and stripped:
            detected_closer = ''
            closes_on_same_line = False

            if stripped.startswith('$$'):
                detected_closer = '$$'
            elif stripped.startswith(r'\['):
                detected_closer = r'\]'
            else:
                # Arithmatexが数式環境として扱う小文字名（末尾*可）に合わせ、
                # 未対応の独自環境で後続行のアンカーを抑止しない。
                begin_match = re.match(r'^\\begin\{([a-z]+\*?)\}', stripped)
                if begin_match:
                    detected_closer = rf'\end{{{begin_match.group(1)}}}'

            if detected_closer:
                closes_on_same_line = _math_closes_at_line_end(
                    stripped, detected_closer, opening_line=True,
                )
                output.append(f'<!--MDLINE:{line_num}-->')
                output.append(line)
                # 入力途中で閉じ記号がまだ無い場合に、後続する全ブロックの
                # 行アンカーまで数式扱いで飲み込まないよう先に完成を確認する。
                has_closer = _has_complete_math_block(
                    lines, i, detected_closer,
                )
                if not closes_on_same_line and has_closer:
                    math_closer = detected_closer
                prev_blank = False
                continue

        # ----- 通常ブロックの開始判定 -----
        is_block_start = False
        is_blockquote_line = stripped.startswith('>')
        is_list_line = bool(re.match(r'^[-*+]\s+|^\d+\.\s+', stripped))

        if stripped:
            # 見出し
            if re.match(r'^#{1,6}\s+', stripped):
                is_block_start = True
            # リスト項目（連続中はマーカーを入れない＝リスト全体で1つ）
            elif is_list_line:
                if not in_list:
                    is_block_start = True
            # 引用（連続中はマーカーを入れない＝引用全体で1つ）
            elif is_blockquote_line:
                if not in_blockquote:
                    is_block_start = True
            # 水平線
            elif re.match(r'^(-{3,}|={3,}|\*{3,})$', stripped):
                is_block_start = True
            # 段落（前が空行で本文がある）
            elif prev_blank:
                is_block_start = True

        if is_block_start:
            output.append(f'<!--MDLINE:{line_num}-->')

        output.append(line)
        prev_blank = (stripped == '')
        # 連続状態の更新：空行が来たら途切れる
        if stripped == '':
            in_blockquote = False
            in_list = False
        else:
            in_blockquote = is_blockquote_line
            # リストは継続行（インデント）も許容したいので、リスト行 or インデント継続を許可
            in_list = is_list_line or (in_list and line.startswith((' ', '\t')))

    return '\n'.join(output)


def _apply_line_attributes(html):
    """`<!--MDLINE:N-->` マーカーを、直後のブロック要素の `data-source-line="N"` に変換する。"""
    # ブロック要素の開始タグの直前にあるマーカーを属性に変換
    # 対応タグ：h1〜h6, p, ul, ol, li, blockquote, pre, div, table
    pattern = re.compile(
        r'<!--MDLINE:(\d+)-->\s*'
        r'(<(h[1-6]|p|ul|ol|li|blockquote|pre|div|table|hr)(\s[^>]*)?(/?)>)',
        re.IGNORECASE,
    )

    def replace(m):
        line_num = m.group(1)
        full_tag = m.group(2)
        tag_name = m.group(3)
        existing_attrs = m.group(4) or ''
        self_close = m.group(5) or ''
        # 既に data-source-line がある場合は上書きしない
        if 'data-source-line' in existing_attrs:
            return full_tag
        return f'<{tag_name}{existing_attrs} data-source-line="{line_num}"{self_close}>'

    html = pattern.sub(replace, html)
    # 残ったマーカーは何が何でも削除する。
    # markdown ライブラリが <p><!--MDLINE:N-->本文</p> のようにインライン化する
    # ケースもあるため、空白なしでも除去できるよう \s* を付けない。
    html = re.sub(r'<!--MDLINE:\d+-->', '', html)
    # コードブロック内に紛れ込んだマーカーは Pygments に
    # `&lt;!--MDLINE:N--&gt;` とエスケープされ、上の正規表現では拾えず
    # コードに可視テキストとして残ってしまう。エスケープ後の形も必ず掃除する。
    html = re.sub(r'&lt;!--MDLINE:\d+--&gt;', '', html)
    return html


@register.filter(name='markdownify')
def markdownify(value):
    """Markdown 文字列を HTML に変換する。

    使用拡張：
    - extra: テーブル・脚注などの追加記法
    - codehilite: コードブロックを Pygments でハイライト
    - toc: 目次の生成サポート
    - pymdownx.arithmatex: TeX数式をMathJax用のラッパーに変換
    - nl2br: 改行を <br> に変換

    独自記法：
    - Qiita風 ```lang:filename コードブロック
    - :::coco ... ::: キャラクター吹き出し

    追加機能：
    - 各ブロックに `data-source-line` 属性を付与（編集画面の双方向ジャンプ用）
    """
    # 行番号マーカーを先に注入する（吹き出し・コードブロック取り出しの前に必須）
    text = _inject_line_markers(value)
    # 吹き出しを先に切り出す（中身は通常のMarkdown変換に流す）
    text, bubbles = _replace_bubbles(text)
    # Qiita風コードブロックを一旦取り出す
    text, named_blocks = _replace_named_code_blocks(text)

    # 通常のMarkdown変換
    html = md.markdown(
        text,
        extensions=['extra', 'codehilite', 'toc', 'pymdownx.arithmatex', 'nl2br'],
        extension_configs={
            'codehilite': {
                'css_class': 'codehilite',
                'linenums': False,
            },
            # scriptタグを生成する非generic形式はBleachに弾かれる。
            # div/spanだけのgeneric形式にし、表示はブラウザ側MathJaxに任せる。
            'pymdownx.arithmatex': {
                'generic': True,
                'smart_dollar': True,
            },
        },
    )

    # 取り出したコードブロックを「ファイル名ラベル付き」で復元（line attributes より先に！）
    # こうしないと apply_line_attributes が <p>placeholder</p> に line属性を付けてしまい、
    # 後でブロック要素（<div>）が <p> に包まれた状態になって invalid HTML になる
    for idx, (language, filename, code) in enumerate(named_blocks):
        placeholder = f'@@CODE_BLOCK_PLACEHOLDER_{idx}@@'
        rendered = _render_code_block(language, filename, code)
        # markdownライブラリが <p>マーカー</p> に包んでしまう場合があるので両対応
        html = html.replace(f'<p>{placeholder}</p>', rendered)
        html = html.replace(placeholder, rendered)

    # @@BUBBLE_START_n@@ ... @@BUBBLE_END_n@@ の範囲を <div class="speech-bubble">...</div> で包む
    # 同じく apply_line_attributes より先に処理することで、<p> ラッピングを除去してから line属性を付与
    #
    # キャラ情報は DB（blog.Character）から取得する。
    # ループ前に全 slug を一括取得して N+1 クエリを回避する。
    # _parse_bubble_key で `-r` を剥がした「実 lookup slug」を集める。
    bubble_char_keys = [_parse_bubble_key(raw)[0] for raw, _ in bubbles]
    character_map = _fetch_characters(bubble_char_keys)
    for idx, (char_key_raw, _inner) in enumerate(bubbles):
        wrapper = _render_bubble_wrapper(char_key_raw, idx, character_map)
        start_marker = f'@@BUBBLE_START_{idx}@@'
        end_marker = f'@@BUBBLE_END_{idx}@@'
        # マーカーが <p> に包まれているケースも吸収（属性付きの <p ...> も含む）
        pattern = re.compile(
            r'(?:<p(?:\s[^>]*)?>\s*)?' + re.escape(start_marker) + r'(?:\s*</p>)?'
            r'(.*?)'
            r'(?:<p(?:\s[^>]*)?>\s*)?' + re.escape(end_marker) + r'(?:\s*</p>)?',
            re.DOTALL,
        )
        if wrapper is None:
            # 未登録キャラ：マーカーだけ消して中身は素のまま残す
            html = pattern.sub(r'\1', html)
        else:
            inner_html = pattern.search(html)
            if inner_html:
                rendered = wrapper.replace(f'@@BUBBLE_INNER_{idx}@@', inner_html.group(1))
                html = pattern.sub(lambda _m, r=rendered: r, html, count=1)

    # マーカーを data-source-line 属性に変換(コードブロック・吹き出し復元の後！)
    # この順序にしないと、復元時に line属性付き <p> が壊れる
    html = _apply_line_attributes(html)

    # bleach の allowlist で最終サニタイズ(XSS入り口の遮断)
    # 独自要素は ALLOWED_TAGS / ALLOWED_ATTRIBUTES に含めてあるので壊れない
    html = _sanitize_html(html)

    return mark_safe(html)


# h2 / h3 を見出しとして抽出する正規表現
# toc 拡張が付ける id 属性を拾う
HEADING_PATTERN = re.compile(
    r'<h([23])\s+id="([^"]+)"[^>]*>(.*?)</h\1>',
    re.DOTALL,
)


def _toc_heading_html(heading_html):
    """見出し装飾を除き、目次でも必要な数式ラッパーだけを残す。"""
    def allow_math_class(tag, name, value):
        return tag == 'span' and name == 'class' and value == 'arithmatex'

    return bleach.clean(
        heading_html,
        tags=['span'],
        attributes=allow_math_class,
        protocols=[],
        strip=True,
    ).strip()


@register.filter(name='extract_toc')
def extract_toc(rendered_html):
    """レンダリング済みHTMLから目次を抽出する。

    Qiita風に h2 を親、h3 を子としてネスト構造で返す。
    h2 がない場合は何も返さない（目次不要のケース）。
    """
    items = []
    for match in HEADING_PATTERN.finditer(rendered_html):
        level = int(match.group(1))
        anchor = match.group(2)
        # 太字等は剥がすが、数式は目次側でもMathJaxが処理できる形を保つ。
        text_html = _toc_heading_html(match.group(3))
        items.append((level, anchor, text_html))

    if not items:
        return ''

    # Qiita風のネスト構造を組み立てる
    # h2: <li>...</li> （子の h3 があれば中に <ul> を入れる）
    # h3: 直前の h2 の中に入れ子で並べる
    html_parts = ['<ul class="toc-list">']
    h2_open = False   # 直前 h2 の <li> が開いたまま？
    in_sub = False    # h3 用の <ul class="toc-sublist"> が開いたまま？

    def close_current_h2():
        nonlocal h2_open, in_sub
        if in_sub:
            html_parts.append('</ul>')
            in_sub = False
        if h2_open:
            html_parts.append('</li>')
            h2_open = False

    for level, anchor, text_html in items:
        anchor_e = escape(anchor)
        if level == 2:
            close_current_h2()
            html_parts.append(
                f'<li class="toc-item toc-h2">'
                f'<a href="#{anchor_e}" data-toc-anchor="{anchor_e}">{text_html}</a>'
            )
            h2_open = True
        elif level == 3:
            # h2 が無いまま h3 から始まる文書のために、ダミーで包む
            if not h2_open:
                html_parts.append('<li class="toc-item toc-h2 toc-orphan">')
                h2_open = True
            if not in_sub:
                html_parts.append('<ul class="toc-sublist">')
                in_sub = True
            html_parts.append(
                f'<li class="toc-item toc-h3">'
                f'<a href="#{anchor_e}" data-toc-anchor="{anchor_e}">{text_html}</a>'
                f'</li>'
            )

    close_current_h2()
    html_parts.append('</ul>')
    return mark_safe(''.join(html_parts))

"""markdown_tags の振る舞いをテストする。

ブログの記事描画ロジックは記事の見栄えに直結するため、
吹き出し / コードブロック / 目次抽出 / 行番号マーカー を網羅的にテストする。
"""

import re

import pytest

from blog.templatetags.markdown_tags import (
    extract_toc,
    markdownify,
    render_post_markdown,
)


# ------------------------------------------------------------
# 基本変換
# ------------------------------------------------------------

def test_markdownify_basic_paragraph():
    """普通の段落が <p> タグに変換される。"""
    html = markdownify('普通のテキスト')
    assert '<p' in html
    assert '普通のテキスト' in html


def test_markdownify_headings():
    """見出しが h2/h3 タグに変換される（toc 用に id 属性付き）。"""
    text = '## 見出し2\n\n### 見出し3'
    html = markdownify(text)
    assert '<h2' in html
    assert '<h3' in html
    assert 'id=' in html  # toc 用の id 自動付与


def test_markdownify_list():
    """箇条書きが ul/li に変換される。"""
    text = '- 項目1\n- 項目2\n- 項目3'
    html = markdownify(text)
    assert '<ul' in html
    assert html.count('<li') == 3


def test_markdownify_blockquote():
    """引用が単一の <blockquote> にまとまる（連続 > の分裂バグ対策）。"""
    text = '> 行1\n> 行2\n> 行3'
    html = markdownify(text)
    assert html.count('<blockquote') == 1  # 1つにまとまる


# ------------------------------------------------------------
# TeX数式（PyMdown Arithmatex → MathJax）
# ------------------------------------------------------------

def test_display_math_renders_safe_wrapper_and_preserves_tex():
    """$$...$$ をscriptではなくgeneric形式の表示数式に変換する。"""
    html = str(markdownify('$$\n\\boxed{\\text{イ：50％}}\n$$'))

    assert re.search(r'<div class="arithmatex"[^>]*data-source-line="1"', html)
    assert r'\boxed{\text{イ：50％}}' in html
    assert r'\[' in html and r'\]' in html
    assert '<script' not in html


def test_display_math_does_not_receive_nl2br_inside_formula():
    """nl2br拡張が複数行の数式内へ <br> を差し込まない。"""
    html = str(markdownify('$$\na &= b \\\\\nc &= d\n$$'))

    assert '<div class="arithmatex"' in html
    assert '<br' not in html


def test_inline_math_renders_inside_source_mapped_paragraph():
    """文中の $...$ だけを数式化し、段落のソース行は保つ。"""
    source = r'平均待ち時間は $W_q = \frac{\rho}{\mu-\lambda}$ です。'
    html = str(markdownify(source))

    assert html.count('<span class="arithmatex">') == 1
    assert r'\(W_q = \frac{\rho}{\mu-\lambda}\)' in html
    assert '<p data-source-line="1">' in html


def test_math_syntax_is_literal_inside_inline_and_fenced_code():
    """サンプルコード内のドル記号は数式に変換しない。"""
    source = '`$x$`\n\n```text\n$$\nx + 1\n$$\n```'
    html = str(markdownify(source))

    assert 'arithmatex' not in html
    assert '<code>$x$</code>' in html
    assert '$$' in html


def test_display_math_keeps_exact_source_line_mapping():
    """数式ラッパーと後続段落に元の開始行を付与する。"""
    html = str(markdownify('前文\n\n$$\nE=mc^2\n$$\n\n後文'))

    assert re.search(r'<div class="arithmatex"[^>]*data-source-line="3"', html)
    assert '<p data-source-line="7">後文</p>' in html
    assert 'MDLINE' not in html


@pytest.mark.parametrize('math_block', [
    '$$\nE = mc^2$$',
    '\\[\nE = mc^2\\]',
    '\\begin{equation}\nE = mc^2\\end{equation}',
])
def test_math_closer_at_end_of_formula_line_keeps_following_anchor(math_block):
    """閉じ記号が式の末尾にある一般的な記法でも後続行を見失わない。"""
    html = str(markdownify(f'{math_block}\n\n## 次の見出し'))

    assert '<div class="arithmatex"' in html
    assert re.search(r'<h2[^>]*data-source-line="4"', html)


def test_unfinished_math_does_not_swallow_following_source_anchors():
    """数式を入力途中でも、それ以降の同期用アンカーを全消失させない。"""
    html = str(markdownify('$$\nE = mc^2\n## 次の見出し\n\n後続段落'))

    assert re.search(r'<h2[^>]*data-source-line="3"', html)
    assert re.search(r'<p[^>]*data-source-line="5"', html)


def test_escaped_currency_dollar_is_not_treated_as_math():
    """通貨のドルと数式のドルを併用できる。"""
    html = str(markdownify(r'料金は \$100、式は $x+1$。'))

    assert html.count('arithmatex') == 1
    assert '$100' in html
    assert r'\(x+1\)' in html


# ------------------------------------------------------------
# 吹き出し（:::coco / :::noel）
#
# Phase 4 のリファクタで CHARACTERS dict → Character モデル（DB）参照に
# 切り替わったため、これらのテストは DB アクセスが必要になった。
# @pytest.mark.django_db を付与し、fixture で coco キャラを用意する。
# ------------------------------------------------------------


@pytest.fixture
def coco_character(db):
    """:::coco レンダリングテスト用の Character。

    migration 0007 でも coco がシードされる可能性があるため、
    get_or_create で「既にあれば再利用、無ければ新規」にして冪等性を確保する。
    画像ファイルが実在しなくても ImageField の名前は保存できる（テスト用途）。
    レンダラ側は character.image.url を呼ぶだけなので URL 文字列さえ得られれば OK。
    """
    from blog.models import Character
    character, _ = Character.objects.get_or_create(
        slug='coco',
        defaults={
            'name': 'ココ',
            'image': 'characters/coco.png',
            'is_active': True,
            'sort_order': 10,
        },
    )
    return character


@pytest.mark.django_db
def test_bubble_coco_renders_speech_bubble_div(coco_character):
    """:::coco ... ::: が speech-bubble div に変換される。"""
    text = ':::coco\nこんにちは\n:::'
    html = markdownify(text)
    assert 'speech-bubble' in html
    assert 'data-char="coco"' in html
    assert 'こんにちは' in html


@pytest.mark.django_db
def test_bubble_coco_r_renders_right_position(coco_character):
    """:::coco-r が right 配置クラスを持つ。"""
    text = ':::coco-r\n右側に出る\n:::'
    html = markdownify(text)
    assert 'speech-bubble-right' in html


@pytest.mark.django_db
def test_bubble_coco_default_is_left_position(coco_character):
    """:::coco（無サフィックス）は left 配置。"""
    text = ':::coco\n左側\n:::'
    html = markdownify(text)
    assert 'speech-bubble-left' in html


@pytest.mark.django_db
def test_bubble_unknown_character_is_stripped():
    """未登録キャラ（:::unknown）はマーカーが除去され、中身だけ残る。

    DB に該当 Character が無いケースをテストするので fixture は使わない。
    """
    text = ':::unknown\nテキスト\n:::'
    html = markdownify(text)
    assert 'speech-bubble' not in html
    assert 'テキスト' in html


@pytest.mark.django_db
def test_bubble_with_markdown_inside(coco_character):
    """吹き出し内の Markdown も正しく変換される。"""
    text = ':::coco\n**太字**と*斜体*\n:::'
    html = markdownify(text)
    assert '<strong>太字</strong>' in html
    assert '<em>斜体</em>' in html


@pytest.mark.django_db
def test_bubble_with_math_inside(coco_character):
    """COCOちゃんの吹き出し内でもインライン数式を組版対象にできる。"""
    text = ':::coco\n待ち時間は $W_q = 5$ 分です。\n:::'
    html = markdownify(text)

    assert 'speech-bubble' in html
    assert '<span class="arithmatex">\\(W_q = 5\\)</span>' in html


# ------------------------------------------------------------
# Qiita風コードブロック（```lang:filename）
# ------------------------------------------------------------

def test_code_block_with_filename_renders_label():
    """```python:views.py のファイル名がラベル表示される。"""
    text = '```python:views.py\nprint("hello")\n```'
    html = markdownify(text)
    assert 'codehilite-wrap' in html
    assert 'codehilite-filename' in html
    assert 'views.py' in html


def test_code_block_with_unknown_language_falls_back_to_text():
    """未知の言語指定でもエラーにならず text として描画。"""
    text = '```unknownlang:test.txt\nsome text\n```'
    html = markdownify(text)
    # エラーにならず、codehilite-wrap で囲まれる
    assert 'codehilite-wrap' in html


def test_code_block_without_filename_uses_default_render():
    """ファイル名なしの ```python は通常のコードブロック。"""
    text = '```python\nprint("test")\n```'
    html = markdownify(text)
    assert 'codehilite' in html
    # ファイル名ラベルは無し
    assert 'codehilite-filename' not in html


# ------------------------------------------------------------
# 目次抽出
# ------------------------------------------------------------

def test_extract_toc_returns_h2_h3_structure():
    """h2 を親、h3 を子とした目次が生成される。"""
    html = markdownify('## 見出しA\n\n### 子A1\n\n### 子A2\n\n## 見出しB')
    toc = extract_toc(html)
    assert 'toc-list' in toc
    assert '見出しA' in toc
    assert '見出しB' in toc
    assert '子A1' in toc
    assert '子A2' in toc
    assert 'toc-sublist' in toc  # h3 の入れ子


def test_extract_toc_returns_empty_when_no_headings():
    """見出しが1つも無いなら目次は空文字列。"""
    html = markdownify('段落だけのテキスト')
    toc = extract_toc(html)
    assert toc == ''


def test_extract_toc_handles_orphan_h3():
    """h2 無しで h3 から始まる文書でも壊れない。"""
    html = markdownify('### いきなり h3')
    toc = extract_toc(html)
    assert 'toc-orphan' in toc or 'いきなり' in toc  # ダミー h2 で吸収


def test_extract_toc_preserves_math_wrapper_in_heading():
    """数式を含む見出しは、目次でも生のTeX記号を表示しない。"""
    body, toc = render_post_markdown('## $W_q$ の計算')

    assert '<span class="arithmatex">\\(W_q\\)</span>' in body
    assert '<span class="arithmatex">\\(W_q\\)</span>' in toc


def test_extract_toc_keeps_only_safe_math_markup():
    """目次へ残す数式spanからイベント属性や別タグを除去する。"""
    rendered = (
        '<h2 id="safe">'
        '<span class="arithmatex" onclick="alert(1)">\\(x\\)</span>'
        '<img src="https://example.com/x.png" onerror="alert(1)">題名'
        '</h2>'
    )

    toc = str(extract_toc(rendered))

    assert '<span class="arithmatex">\\(x\\)</span>' in toc
    assert 'onclick' not in toc
    assert 'onerror' not in toc
    assert '<img' not in toc


def test_unsupported_begin_environment_keeps_following_source_anchors():
    """Arithmatex対象外の環境名で、後続ブロックの行同期を壊さない。"""
    html = str(markdownify(
        '\\begin{Equation}\n'
        'x = 1\n'
        '\\end{Equation}\n\n'
        '後続段落'
    ))

    assert 'data-source-line="1"' in html
    assert 'data-source-line="5"' in html


# ------------------------------------------------------------
# 行番号マーカー（data-source-line 属性）
# ------------------------------------------------------------

def test_source_line_attribute_on_headings():
    """見出しに data-source-line 属性が付与される。"""
    html = markdownify('## 見出し')
    assert 'data-source-line=' in html


def test_source_line_attribute_on_paragraphs():
    """段落に data-source-line 属性が付与される。"""
    html = markdownify('普通の段落')
    assert 'data-source-line=' in html


def test_no_mdline_comment_leaks_to_output():
    """<!--MDLINE:N--> コメントが出力に残らない（過去のバグ）。"""
    text = '## 見出し\n\n本文\n\n> 引用'
    html = markdownify(text)
    assert '<!--MDLINE' not in html


# ------------------------------------------------------------
# render_post_markdown（本文 + 目次セット）
# ------------------------------------------------------------

def test_render_post_markdown_returns_body_and_toc():
    """記事本文用関数は (body_html, toc_html) のタプルを返す。"""
    body, toc = render_post_markdown('## 見出し\n\n本文')
    assert '<h2' in body
    assert '本文' in body
    assert 'toc-list' in toc
    assert '見出し' in toc


# ------------------------------------------------------------
# XSS サニタイズ回帰テスト（bleach allowlist 担保）
# ------------------------------------------------------------
# 将来 allowlist の TAGS / ATTRIBUTES を触った時に、誰かが
# うっかり危険な要素を許可に追加してしまわないための保険。

def test_xss_script_tag_is_neutralized():
    """<script> タグは実行可能 HTML として残らない。"""
    html = markdownify('<script>alert(1)</script>')
    # 実行可能な <script> 要素は出力されない
    assert '<script>' not in html
    assert '<script ' not in html


def test_xss_javascript_href_is_stripped():
    """[text](javascript:alert(1)) のリンク href から javascript: が削除される。"""
    html = markdownify('[click](javascript:alert(1))')
    # href="javascript:..." は bleach が ALLOWED_PROTOCOLS で弾く
    assert 'href="javascript:' not in html
    assert 'javascript:alert' not in html


def test_xss_inline_event_handlers_are_stripped():
    """onclick / onerror など on*= イベントハンドラは除去される。"""
    html = markdownify('<a href="https://example.com" onclick="alert(1)">link</a>')
    assert 'onclick' not in html


def test_xss_img_with_javascript_src_is_stripped():
    """<img src="javascript:..."> の src も javascript: は除去される。"""
    html = markdownify('<img src="javascript:alert(1)" alt="x">')
    assert 'src="javascript:' not in html


def test_xss_iframe_tag_is_stripped():
    """<iframe> は allowlist 外なので除去される。"""
    html = markdownify('<iframe src="https://evil.example.com"></iframe>')
    assert '<iframe' not in html


@pytest.mark.django_db
def test_xss_does_not_break_speech_bubble(coco_character):
    """サニタイズ後も :::coco の吹き出し要素・属性が維持される（許可漏れ防止）。"""
    html = markdownify(':::coco\nテスト\n:::')
    assert 'speech-bubble' in html
    assert 'data-char="coco"' in html
    assert 'テスト' in html


def test_xss_does_not_break_code_block():
    """サニタイズ後も Pygments ハイライトのコードブロックが維持される。"""
    html = markdownify('```python:test.py\nprint("hello")\n```')
    assert 'codehilite' in html
    assert 'test.py' in html
    # Pygments の span タグ（色付け用）が残ってる
    assert '<span' in html


def test_xss_does_not_break_data_source_line():
    """サニタイズ後も data-source-line 属性（双方向ジャンプ用）が維持される。"""
    html = markdownify('## 見出し\n\n本文')
    assert 'data-source-line=' in html


def test_nested_fence_is_shown_literally_not_extracted():
    """外側 ````markdown の中の ```python:views.py は『記法を見せる文字列』。

    本物のファイル名ブロックとして抽出されず、文字としてそのまま表示されること。
    （入れ子フェンス非対応だと、内側が本物ブロック化して二重描画＆コピーボタン重複が起きる）
    """
    md = (
        '````markdown\n'
        '```python:views.py\n'
        'def index(request):\n'
        '    ...\n'
        '```\n'
        '````'
    )
    html = markdownify(md)
    # 本物のファイル名ラベル付きブロック（codehilite-wrap）にはならない
    assert 'codehilite-wrap' not in html
    # 記法そのもの（python:views.py）が読者に見える形で残っている
    assert 'python:views.py' in html
    # MDLINE マーカーの残骸が漏れていない
    assert 'MDLINE' not in html


def test_normal_named_block_still_renders():
    """トップレベルの ```lang:filename は従来どおり本物のブロックとして描画される。"""
    html = markdownify('```python:views.py\ndef index(request):\n    return 1\n```')
    assert 'codehilite-wrap' in html
    assert 'views.py' in html

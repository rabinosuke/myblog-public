"""現在の公開記事だけを取得し、会話に渡すMarkdown資料の量を制限する。"""

import re

from django.conf import settings

from blog.models import Post
from .coco_chat import ChatError

GAP = '\n\n[…中略…]\n\n'


def _chunks(body, width):
    """見出しを添えた段落・コード断片。コード内の空行や#は見出し扱いしない。"""
    chunks, headings, lines = [], [], []
    fence = None

    def flush(code_fence=None):
        text = '\n'.join(lines).strip()
        lines.clear()
        if not text:
            return
        prefix = '\n'.join(headings)[-240:]
        # 大きな段落・コードも末尾の話題を選べるよう、小さな単位に分ける。
        while text:
            end = min(width, len(text))
            if end < len(text):
                boundary = text.rfind('\n', 0, end)
                if boundary > width // 2:
                    end = boundary
            part, text = text[:end], text[end:].lstrip('\n')
            if code_fence:
                closing = re.match(r'^(`+|~+)', code_fence)[1]
                part = f'{code_fence}\n{part}\n{closing}'
            chunks.append('\n\n'.join(filter(None, [prefix, part])))

    for line in body.splitlines():
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(re.match(r'^(`+|~+)', fence)[1]) and not marker[2].strip():
                flush(fence)
                fence = None
            else:
                lines.append(line)
            continue
        if marker:
            flush()
            fence = marker[1] + marker[2]
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading:
            flush()
            level = len(heading[1])
            headings = [h for h in headings if len(h.split(' ', 1)[0]) < level]
            headings.append(line[:240])
        elif not line.strip():
            flush()
        else:
            lines.append(line)
    flush(fence)
    return chunks


def _terms(question):
    # 日本語は分かち書き不要の2文字単位、識別子は単語単位で照合する。
    question = re.sub(r'この記事|について|教えて|ください|要約|まとめ|簡単|説明|それ|これ', '', question.casefold())
    words = set(re.findall(r'[a-z0-9_]{2,}', question))
    for run in re.findall(r'[ぁ-んァ-ヶ一-龥ー]+', question):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words


def excerpt_body(body, question, limit):
    body = body.strip()
    if len(body) <= limit:
        return body, False
    chunks = _chunks(body, min(1000, max(32, limit // 3)))
    terms = _terms(question)
    scores = [sum(min(chunk.casefold().count(term), 3) for term in terms) for chunk in chunks]
    if any(scores):
        ranked = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
        # 最も関連する箇所を先に確保し、その前後も説明・コードの補助として拾う。
        best = ranked[0]
        neighbors = [i for i in (best - 1, best + 1) if 0 <= i < len(chunks)]
        order = [best, 0, *neighbors, *ranked[1:]]
    else:
        # 要約のように特定の話題がなければ、冒頭だけでなく記事全体から拾う。
        count = min(6, len(chunks))
        order = [round(i * (len(chunks) - 1) / max(1, count - 1)) for i in range(count)]
        order += list(range(len(chunks)))
    chosen = set()
    for index in order:
        candidate = chosen | {index}
        size = sum(len(chunks[i]) for i in candidate) + len(GAP) * (len(candidate) - 1)
        if size <= limit:
            chosen = candidate
    return GAP.join(chunks[i] for i in sorted(chosen))[:limit], True


def get_article_context(slug, message, history):
    # スタッフ権限やリクエスト中のURLに関係なく、公開済みの記事に限定する。
    try:
        post = Post.objects.only('title', 'slug', 'summary', 'body').get(slug=slug, is_published=True)
    except Post.DoesNotExist:
        raise ChatError('article_unavailable', 'この記事は参照できません。公開中の記事を開き直してください。', 404) from None
    previous_questions = [entry['content'] for entry in history if entry['role'] == 'user'][-2:]
    body, is_excerpt = excerpt_body(post.body, '\n'.join([*previous_questions, message]), settings.COCO_CHAT_MAX_ARTICLE_CHARS)
    return {
        'title': post.title, 'url': post.get_absolute_url(), 'summary': post.summary,
        'body': body, 'body_is_excerpt': is_excerpt,
    }

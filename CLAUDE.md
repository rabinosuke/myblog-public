# myblog プロジェクト

DockerとDjangoで作るブログサイト。
note記事「AIと一緒に作るDjangoブログ入門」の題材として開発中。

---

## 技術スタック

- Django 6.0 + Python 3.12
- MySQL 8.0
- Nginx
- Bootstrap 5.3
- Docker Compose
- django-environ（環境変数管理）
- gunicorn（本番用WSGIサーバー）

---

## コーディング規約

### Python

- **PEP8 準拠**
- **Djangoの公式コーディングスタイル準拠**（https://docs.djangoproject.com/en/dev/internals/contributing/writing-code/coding-style/）
- import順は「標準ライブラリ → サードパーティ → 自作」を空行で区切る
- `class Meta` はフィールド定義の後、`__str__` の前に置く
- `verbose_name` でフィールドに日本語名を必ず付ける

### コメント

- **コメントは日本語で書く**
- **WHATではなくWHYを書く**（コードで分かることは書かない）
- 強い言葉で書く（「〜する」より「**必ず〜する**」）

良い例：
```python
# F()式を使うことで、同時アクセスでもカウントがズレない
Post.objects.filter(pk=post.pk).update(view_count=F('view_count') + 1)
```

悪い例：
```python
# view_countを1増やす  ← コード見れば分かる
Post.objects.filter(pk=post.pk).update(view_count=F('view_count') + 1)
```

### HTML/CSS

- Bootstrap 5.3 を使う。独自CSSは最小限
- CSSはインライン（`<style>`）にせず別ファイルに分離する
- テンプレートは `{% extends 'blog/base.html' %}` で継承して共通化する

---

## プロジェクト構造（公式チュートリアル準拠）

```
~/myblog/
├ myblog/          ← プロジェクト設定
├ blog/            ← アプリ
│  ├ templates/blog/    ← アプリ名で名前空間化（必須）
│  ├ static/blog/       ← アプリ名で名前空間化（必須）
│  ├ fixtures/          ← テストデータ
│  └ migrations/
├ docker/          ← Nginx設定
├ staticfiles/     ← collectstatic先
├ media/           ← アップロード画像
├ manage.py
├ .env             ← パスワード等。Gitにあげない
├ Dockerfile
├ docker-compose.yml
└ requirements.txt
```

**重要**：公式チュートリアルから外れた構造にしない。

---

## URL設計

- 詳細ページは **slug方式**（`/posts/django-blog-intro/`）。SEOと可読性のため
- URL名前空間 `app_name = 'blog'` を必ず設定する
- テンプレートから参照するときは `{% url 'blog:detail' post.slug %}`
- より具体的なURLを先に書く（`search/` を `<slug>/` より前）

---

## DB設計の方針

- 変更に強い設計を選ぶ（迷ったら別テーブル）
- 例：表示スロットは `Post.is_featured` フラグではなく `DisplaySlot` モデルで管理

---

# Myblog / AI COCO 公開用ソース

DjangoとDockerで作ったブログと、案内役「COCO」のアニメーション・AI会話の実装例です。
日本語の記事を読みながら実装を確認できるよう、運用中のコードから公開用ソースを切り出しています。
このリポジトリの履歴は初回コミットから始まります。

## 主な機能

- Markdownでの記事投稿・編集、コード表示、目次、数式、画像アップロード
- カテゴリ・タグ・検索・ページネーション・サイトマップ
- COCOの表示切替、待機・歩行・リアクション
- OpenAIによるテキスト会話、公開記事の参照、会話状態の引継ぎ
- ElevenLabsによる音声、文字と音声の逐次表示
- 管理画面でのプロフィール・音声・利用上限の設定

## 環境

- Docker Desktop / Docker Compose
- Python 3.12、Django 6.0、MySQL 8.0、Nginx

PythonやMySQLをホストへ直接インストールする必要はありません。

## ローカルで起動する

```bash
git clone https://github.com/rabinosuke/myblog-public.git
cd myblog-public
cp .env.example .env
```

`.env` の `SECRET_KEY`、`MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD` を、自分で生成した値へ変更します。
`DATABASE_URL` のパスワードは `MYSQL_PASSWORD` と同じ値にしてください。
ランダム文字列は次のコマンドで生成できます。項目ごとに生成し直します。

```bash
openssl rand -hex 32
```

続いて、コンテナーをビルドしてDBと静的ファイルを準備します。

```bash
docker compose build
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py loaddata initial_data
docker compose run --rm web python manage.py collectstatic --noinput
docker compose run --rm -it web python manage.py createsuperuser
docker compose up -d
```

ブログは **http://localhost:8080/** で開きます。
管理画面は `.env` の `ADMIN_URL` の値です。既定値なら http://localhost:8080/admin/ です。
Webはlocalhostだけへ公開し、MySQLのポートはホストへ公開しません。

停止するときは次を実行します。DBのボリュームは保持されます。

```bash
docker compose down
```

## AI会話・音声を使う

初期状態では外部APIを呼ばない設定です。

会話には自分の `OPENAI_API_KEY`、音声には自分の `ELEVENLABS_API_KEY` と
`COCO_SPEECH_VOICE_ID` が必要です。`.env` の対応する `COCO_CHAT_ENABLED` / `COCO_SPEECH_ENABLED`
を `True` にし、利用できるモデルを指定してください。
管理画面の「COCO設定」でも音声の有効化と利用上限を設定します。
APIの利用料は各サービス側で発生します。

`.env` を変更した後は、次のコマンドでWebコンテナーを再作成します。

```bash
docker compose up -d --force-recreate web
```

## 含まれるデータ

`blog/fixtures/initial_data.json` は動作確認用のサンプル記事です。
実際のブログDB、管理者ユーザー、会話データ、APIキー、ログ、サーバーの接続情報は含めていません。

## 検証

CIは依存パッケージの脆弱性検査、JavaScriptテスト、マイグレーションの整合性、
Pythonテストとカバレッジを確認します。

```bash
docker compose run --rm web pytest --cov=blog --cov-report=term
```

## 本番設定の見方

`docker-compose.prod.yml` と `docker/nginx.prod.conf` は `example.com` を使った構成例です。
証明書・DNS・環境変数は自分の環境に合わせて用意します。
`deploy.yml` の本番デプロイジョブは、この公開用リポジトリでは無効にしています。
本体リポジトリのActions設定・Secrets・PR・Git履歴は引き継いでいません。

## ライセンス

ソースコードはMITです。全文は [LICENSE](LICENSE) を参照してください。
COCOなどの画像・アニメーション・記事素材には同じライセンスを適用していません。
素材の扱いは [ASSETS.md](ASSETS.md) を確認してください。

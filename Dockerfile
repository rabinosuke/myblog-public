FROM python:3.15.0
WORKDIR /app

# ========================================================
# 非rootユーザー作成（セキュリティ強化）
# - Docker公式推奨：「コンテナは非rootで動かすべき」
# - rootで動かすとコンテナ脱獄時にホスト権限取得されやすい
# - UID/GID=1000 はホストの一般ユーザーと揃えてvolume権限問題回避
# 参考:
#   - https://www.docker.com/blog/understanding-the-docker-user-instruction/
#   - https://code.visualstudio.com/remote/advancedcontainers/add-nonroot-user
#   - https://nickjanetakis.com/blog/running-docker-containers-as-a-non-root-user-with-a-custom-uid-and-gid
# ========================================================
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/bash --create-home app

# requirements ファイルをコピー
# - 末尾 `*` は glob。requirements-dev.txt が無い環境（本番ビルド）でも
#   COPY failed にならない（POSIX glob は 0件マッチを許容）
COPY requirements.txt .
COPY requirements-dev.txt* ./

# 本番依存は必ず入れる
# - ARG 分岐より上に置くことで Docker レイヤキャッシュを最大化
#   （本番依存は変更頻度が低い ＝ ここを上に置くと長期間キャッシュヒット）
# - `--no-cache-dir`: pip のビルド後ゴミを残さずイメージサイズ削減
RUN pip install --no-cache-dir -r requirements.txt

# 開発依存はビルド引数で切り替え（本番イメージには入れない）
# - default=false の理由：Fail-safe 原則
#   引数を渡し忘れた本番ビルドで dev 依存が混入する事故を防ぐ
# - if の二重チェック理由：Defense in depth
#   ARG が "True"/"1" など曖昧な値で来ても厳格に false 扱い、
#   かつ requirements-dev.txt が無い環境でもクラッシュしない
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ] && [ -f requirements-dev.txt ]; then \
      pip install --no-cache-dir -r requirements-dev.txt; \
    fi

# ソースコードは最後にコピー
# - 変更頻度が最も高いので最後に置くことでレイヤキャッシュ最大化
#   （上に置くとコード1文字変えるたびに pip install からやり直しになる）
# - --chown でコピー時に所有権を app ユーザーに変更
COPY --chown=app:app . .

# logs/ staticfiles/ media/ は実行時に書き込みが必要
# - mkdir + chown でappが書き込めるようにする
RUN mkdir -p /app/logs /app/staticfiles /app/media && \
    chown -R app:app /app

# 以降のコマンドは非rootの app ユーザーで実行
# - gunicorn は 0.0.0.0:8000 で listen するが、ポート >1024 なので非rootでOK
USER app
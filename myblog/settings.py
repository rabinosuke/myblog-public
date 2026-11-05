"""
プロジェクト全体の設定ファイル。
環境ごとに変わる値（パスワード・ホスト名等）は .env に書き、
django-environ で読み込む。コードに直接書かないこと。
"""

from pathlib import Path
import environ

# プロジェクトのルートディレクトリ（manage.pyがある場所）
BASE_DIR = Path(__file__).resolve().parent.parent

# .env ファイルを BASE_DIR から読み込む
# BASE_DIR を定義した直後に書かないと NameError になるので順序に注意
env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')


# -------------------------------------------------------------------
# セキュリティ設定
# -------------------------------------------------------------------

# 暗号化やCSRFトークンの生成に使う秘密鍵。絶対に外部に漏らさないこと
SECRET_KEY = env('SECRET_KEY')

# True にするとエラーの詳細がブラウザに表示される。本番では必ず False にする
DEBUG = env.bool('DEBUG')

# Djangoがレスポンスを返すことを許可するホスト名のリスト
# 本番環境では実際のドメイン名を設定する
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

# CSRF対策で信頼するオリジン（Django 4.0以降で必須）
# 本番では https://coco-lab.io 等を指定
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# Google Analytics 4 の測定ID（例: G-XXXXXXXXXX）
# 本番デプロイ後に .env に設定する。未設定時はGAタグを出力しない
GA_TRACKING_ID = env('GA_TRACKING_ID', default='')

# COCOのAIテキスト会話。キーはサーバーだけで使用し、テンプレートへ渡さない。
COCO_CHAT_ENABLED = env.bool('COCO_CHAT_ENABLED', default=DEBUG)
OPENAI_API_KEY = env('OPENAI_API_KEY', default='')
COCO_CHAT_MODEL = env('COCO_CHAT_MODEL', default='gpt-6-luna')
COCO_CHAT_REASONING_EFFORT = env('COCO_CHAT_REASONING_EFFORT', default='none')
COCO_CHAT_TIMEOUT_SECONDS = env.int('COCO_CHAT_TIMEOUT_SECONDS', default=10)
COCO_CHAT_MAX_INPUT_CHARS = 1000
COCO_CHAT_MAX_HISTORY_MESSAGES = 12
COCO_CHAT_MAX_CONTEXT_CHARS = 8000
# 会話履歴とは別に、現在の記事から渡す本文の最大文字数。
COCO_CHAT_MAX_ARTICLE_CHARS = 6000
COCO_CHAT_MAX_REQUEST_BYTES = 32768
COCO_CHAT_MAX_OUTPUT_TOKENS = env.int('COCO_CHAT_MAX_OUTPUT_TOKENS', default=500)
# 以下の回数制限は初回移行時の値・設定行がない場合の予備値。運用中はバックヤードのCOCO設定を使う。
COCO_CHAT_PER_MINUTE = env.int('COCO_CHAT_PER_MINUTE', default=5)
COCO_CHAT_PER_VISITOR_DAY = env.int('COCO_CHAT_PER_VISITOR_DAY', default=30)
COCO_CHAT_GLOBAL_PER_DAY = env.int('COCO_CHAT_GLOBAL_PER_DAY', default=100)
COCO_CHAT_MAX_CONCURRENT = env.int('COCO_CHAT_MAX_CONCURRENT', default=2)
# Cookieを更新する連投にも、サイト全体の短時間上限を必ず適用する。
COCO_CHAT_GLOBAL_PER_MINUTE = env.int('COCO_CHAT_GLOBAL_PER_MINUTE', default=20)
# Trueはwebを外部公開せず、NginxがX-Real-IPを上書きするCompose構成だけで使う。
COCO_CHAT_TRUST_PROXY = env.bool('COCO_CHAT_TRUST_PROXY', default=False)

# 読み上げは同じ会話リクエスト内で行い、別の無制限な生成口は作らない。
COCO_SPEECH_ENABLED = env.bool('COCO_SPEECH_ENABLED', default=DEBUG)
ELEVENLABS_API_KEY = env('ELEVENLABS_API_KEY', default='')
COCO_SPEECH_VOICE_ID = env('COCO_SPEECH_VOICE_ID', default='')
COCO_SPEECH_MODEL = env('COCO_SPEECH_MODEL', default='eleven_v4')
COCO_SPEECH_TIMEOUT_SECONDS = 15
COCO_SPEECH_MAX_BYTES = 4 * 1024 * 1024

# -------------------------------------------------------------------
# 本番環境のセキュリティ設定
# DEBUG=False（本番）のときのみ有効化する
# -------------------------------------------------------------------

if not DEBUG:
    # Nginx経由のリクエストがHTTPSかどうかを判定する
    # X-Forwarded-Proto ヘッダーが 'https' なら HTTPS とみなす
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    # セッションCookieをHTTPSのみで送信（盗聴防止）
    SESSION_COOKIE_SECURE = True

    # CSRFトークンCookieをHTTPSのみで送信
    CSRF_COOKIE_SECURE = True

    # ============================================
    # 多層防御：NginxでHTTPS強制してるが、Django側にも入れる
    # → Nginxが万一設定ミスってもDjangoで守る
    # → 「Django check --deploy」のW004/W008警告も解消
    # ============================================

    # HTTPでアクセスされたら自動的にHTTPSにリダイレクト
    # （リダイレクトループ防止のため SECURE_PROXY_SSL_HEADER と組み合わせ必須）
    # CI/テスト時はconftest.pyのfixtureで自動的にFalseに上書きされる
    SECURE_SSL_REDIRECT = True

    # HSTS：ブラウザに「このサイトは今後1年間HTTPSのみで来てね」と宣言
    # max-age=31536000秒 = 1年
    SECURE_HSTS_SECONDS = 31536000

    # サブドメインもHSTS適用（運用予定があるなら）
    # 今はサブドメインなしなので False のまま安全側
    # → 将来 www.coco-lab.io 等を本格運用する時は True に変更検討
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False

    # ブラウザのHSTSプリロードリスト登録準備
    # True にすると Chromium 等の事前リストに登録可能になるが、
    # 一度登録すると解除が極めて困難なので慎重に True に変える
    # → 半年以上安定運用してから検討
    SECURE_HSTS_PRELOAD = False


# -------------------------------------------------------------------
# 意識的に False のままにしている警告を抑制（インフォメーション扱い）
# - manage.py check --deploy で警告ゼロにするための公式パターン
# - 参考：https://docs.djangoproject.com/en/stable/ref/checks/
# -------------------------------------------------------------------

SILENCED_SYSTEM_CHECKS = [
    # W005: SECURE_HSTS_INCLUDE_SUBDOMAINS=False は意図的（サブドメイン運用なし）
    'security.W005',
    # W021: SECURE_HSTS_PRELOAD=False は意図的（preloadリスト登録は一方通行で慎重に判断したい）
    'security.W021',
]


# -------------------------------------------------------------------
# アプリケーション定義
# -------------------------------------------------------------------

INSTALLED_APPS = [
    # 自作アプリ
    'blog.apps.BlogConfig',
    # サードパーティアプリ
    'imagekit',                   # 画像の自動リサイズ・サムネイル生成
    # Django標準アプリ
    'django.contrib.admin',       # 管理画面
    'django.contrib.auth',        # 認証（ログイン・ログアウト）
    'django.contrib.contenttypes',
    'django.contrib.sessions',    # セッション管理
    'django.contrib.messages',    # フラッシュメッセージ
    'django.contrib.humanize',    # 利用量を3桁区切りで表示
    'django.contrib.staticfiles', # 静的ファイル配信
    'django.contrib.sitemaps',    # sitemap.xml 生成用
    'django.contrib.sites',       # サイトのドメイン管理（sitemapが内部で使用）
]

# sitemapが使うサイトのID。複数サイト運営しないので 1 固定でOK
SITE_ID = 1

# リクエストとレスポンスの間で処理を挟む仕組み。上から順に実行される
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',         # CSRF攻撃対策
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# プロジェクトのメインURLファイルを指定する
ROOT_URLCONF = 'myblog.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        # True にすると各アプリの templates/ フォルダを自動で探索する
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # 全テンプレートに GA_TRACKING_ID と debug を渡す
                'blog.context_processors.site_settings',
            ],
        },
    },
]

# WSGIサーバー（gunicorn）のエントリーポイント
WSGI_APPLICATION = 'myblog.wsgi.application'


# -------------------------------------------------------------------
# データベース設定
# -------------------------------------------------------------------

# DATABASE_URL を .env から読み込んでMySQLに接続する
# 例: mysql://user:password@host:port/dbname
DATABASES = {
    'default': env.db()
}


# -------------------------------------------------------------------
# パスワードバリデーション
# -------------------------------------------------------------------

# ユーザーが設定するパスワードの強度チェック
AUTH_PASSWORD_VALIDATORS = [
    {
        # ユーザー名・メールアドレスと似たパスワードを弾く
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        # 8文字未満を弾く
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        # よく使われる単純なパスワードを弾く
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        # 数字だけのパスワードを弾く
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# -------------------------------------------------------------------
# 国際化・タイムゾーン
# -------------------------------------------------------------------

LANGUAGE_CODE = 'ja'       # 管理画面を日本語表示にする
TIME_ZONE = 'Asia/Tokyo'   # 日時を日本時間で扱う
USE_I18N = True            # 国際化を有効にする
USE_TZ = True              # タイムゾーンを有効にする（UTCで保存してTIME_ZONEで表示）


# -------------------------------------------------------------------
# 静的ファイル（CSS・JS・画像）
# -------------------------------------------------------------------

# ブラウザからアクセスするときのURLプレフィックス（例: /static/blog/blog.css）
STATIC_URL = 'static/'

# collectstatic コマンドで各アプリの static/ を集める場所
# Nginx はこのフォルダを直接配信する
STATIC_ROOT = BASE_DIR / 'staticfiles'


# -------------------------------------------------------------------
# メディアファイル（ユーザーがアップロードする画像）
# -------------------------------------------------------------------

# ブラウザからアクセスするときのURLプレフィックス
MEDIA_URL = 'media/'

# アップロードされたファイルの保存先
# docker-compose.yml で ./media をボリュームマウントして Nginx と共有している
MEDIA_ROOT = BASE_DIR / 'media'

# =====================================================================
# Sentry 設定（本番のみ有効化）
#
# 設定値はここに、初期化ロジックは myblog/sentry_setup.py に。
# 家訓「settings.py は宣言のみ、ロジックは別」+ Two Scoops of Django 準拠。
#
# 参考URL:
#   - Two Scoops of Django: https://www.feldroy.com/books/two-scoops-of-django-3-x
#   - Sentry Django: https://docs.sentry.io/platforms/python/integrations/django/
# =====================================================================

SENTRY_DSN = env('SENTRY_DSN', default='')
SENTRY_ENVIRONMENT = env('SENTRY_ENVIRONMENT', default='production')
SENTRY_RELEASE = env('SENTRY_RELEASE', default='unknown')

# 開発・テスト環境は誤送信しないよう DEBUG=False 時だけ init を試みる。
# init_sentry 側でも dsn 空チェックがあるため二重防御。
if not DEBUG:
    from myblog.sentry_setup import init_sentry
    init_sentry(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        release=SENTRY_RELEASE,
    )

# ===========================================
# LOGGING 設定
# ===========================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,

    # ログのフォーマット
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}:{lineno} - {message}',
            'style': '{',
        },
        'simple': {
            'format': '[{levelname}] {message}',
            'style': '{',
        },
    },

    # ログの出力先
    'handlers': {
        # 標準出力（docker logs で確認できる）
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        # ファイル（ローテーション付き）：本番で使う
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'app.log',
            'maxBytes': 10 * 1024 * 1024,  # 10MB
            'backupCount': 5,               # 古いやつは5世代まで
            'formatter': 'verbose',
        },
    },

    # ロガーの定義
    'loggers': {
        # Django 本体のログ
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',  # DEBUG にすると SQL も出る（本番ではNG）
            'propagate': False,
        },
        # Django のリクエスト処理（404/500等）
        'django.request': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
        # 自分のアプリのログ
        'blog': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# logs ディレクトリを起動時に自動作成
(BASE_DIR / 'logs').mkdir(exist_ok=True)

from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from imagekit.models import ImageSpecField, ProcessedImageField
from imagekit.processors import ResizeToFill


class Category(models.Model):
    """記事のカテゴリ。"""

    name = models.CharField(max_length=100, verbose_name='カテゴリ名')
    # URLに使うため半角英数字・ハイフンのみ。uniqueで重複を防ぐ
    slug = models.SlugField(unique=True, verbose_name='スラッグ')

    class Meta:
        verbose_name = 'カテゴリ'
        verbose_name_plural = 'カテゴリ'

    def __str__(self):
        return self.name


class Tag(models.Model):
    """記事のタグ。

    カテゴリ（記事の主分類・1記事1つ）とは別に、
    記事に出てくる技術キーワードを複数ぶら下げるための軽い分類。
    例: Django / Docker / Git / nginx など。
    """

    name = models.CharField(max_length=50, verbose_name='タグ名')
    # URLに使うため半角英数字・ハイフンのみ。uniqueで重複を防ぐ
    slug = models.SlugField(unique=True, verbose_name='スラッグ')

    class Meta:
        verbose_name = 'タグ'
        verbose_name_plural = 'タグ'
        ordering = ['name']

    def __str__(self):
        return self.name


class Post(models.Model):
    """ブログ記事。"""

    title = models.CharField(max_length=200, verbose_name='タイトル')
    # URL に使う識別子。例: /posts/django-tutorial/
    # SEOと可読性のため記事ごとに固有の英数字を設定する
    slug = models.SlugField(max_length=200, unique=True, verbose_name='URL用スラッグ')
    # アイキャッチ画像。1フォルダに溜まりすぎないよう年月別に振り分ける
    # アップロード時に 1200x630（OGP標準サイズ）に自動リサイズ・JPEG圧縮する
    eyecatch = ProcessedImageField(
        upload_to='eyecatches/%Y/%m/',
        processors=[ResizeToFill(1200, 630)],
        format='JPEG',
        options={'quality': 85},
        blank=True,
        null=True,
        verbose_name='アイキャッチ画像',
    )
    # 一覧用サムネイル。eyecatchから自動生成される（DBには保存されない）
    # 初回アクセス時に生成・キャッシュされる
    eyecatch_thumbnail = ImageSpecField(
        source='eyecatch',
        processors=[ResizeToFill(600, 340)],
        format='JPEG',
        options={'quality': 80},
    )
    # 一覧ページ用の短い説明。省略可能
    summary = models.CharField(
        max_length=300,
        verbose_name='要約',
        blank=True,
    )
    # 本文（Markdown形式）。管理画面では Toast UI Editor で編集する
    body = models.TextField(verbose_name='本文')
    # カテゴリを削除しても記事は残す（SET_NULL）
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='カテゴリ',
    )
    # タグ（複数可）。カテゴリと違い1記事に何個でも付けられる。
    # 記事が消えてもタグ自体は残す（M2Mなので中間テーブルの行だけ消える）。
    tags = models.ManyToManyField(
        Tag,
        blank=True,
        related_name='posts',
        verbose_name='タグ',
    )
    is_published = models.BooleanField(default=False, verbose_name='公開')
    # 管理者が手動でトップの特集バナーに表示する記事を選ぶ（WordPress方式）
    # 複数にフラグが立っている場合は published_at の新しい順で先頭1件を使う
    is_featured = models.BooleanField(default=False, verbose_name='特集')
    # 未公開の場合はNullを許容する
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='公開日時',
    )
    # 記事が表示された回数。記事詳細ページを開くたびに1加算する
    view_count = models.PositiveIntegerField(default=0, verbose_name='閲覧数')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='作成日時')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新日時')

    class Meta:
        verbose_name = '記事'
        verbose_name_plural = '記事'
        # 公開日時の新しい順に並べる。
        # ただし published_at が NULL の記事を先頭に出さないよう、第2キーで id 昇順を併用。
        # （NULL を末尾に追いやるのはビュー側で F('published_at').desc(nulls_last=True) を使う）
        ordering = ['-published_at', '-id']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        """この記事の詳細ページURLを返す。

        sitemap.xml や管理画面の「サイトを表示」リンクで使われる。
        """
        return reverse('blog:detail', kwargs={'slug': self.slug})


class DisplaySlot(models.Model):
    """トップページの表示スロット管理。

    どのスロット（特集・ピックアップ等）にどの記事を置くかを管理する。
    スロットの種類や数をコードを変えずに管理画面から追加・変更できる。
    """

    SLOT_FEATURED = 'featured'
    SLOT_PICKUP = 'pickup'

    SLOT_CHOICES = [
        (SLOT_FEATURED, '特集バナー'),
        (SLOT_PICKUP, 'ピックアップ'),
    ]

    slot_type = models.CharField(
        max_length=50,
        choices=SLOT_CHOICES,
        verbose_name='スロット',
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        verbose_name='記事',
    )
    # 同じスロット内での並び順。数字が小さいほど先に表示する
    order = models.IntegerField(default=0, verbose_name='表示順')

    class Meta:
        verbose_name = '表示スロット'
        verbose_name_plural = '表示スロット'
        # スロット種別→表示順で並べる
        ordering = ['slot_type', 'order']

    def __str__(self):
        return f'{self.get_slot_type_display()} / {self.post.title}'


class Character(models.Model):
    """記事内で吹き出しに使うキャラクター。

    Markdown本文の :::<slug> ... ::: 記法で呼び出される。
    例: :::coco の内容 :::

    既存のハードコード（templatetags/markdown_tags.py の CHARACTERS dict）を
    DBで管理できるよう移行したもの。管理画面からキャラ追加が可能。
    """

    name = models.CharField(
        max_length=50,
        verbose_name='表示名',
        help_text='読者に見せる名前（例：ココちゃん）',
    )
    slug = models.SlugField(
        unique=True,
        verbose_name='スラッグ',
        help_text=':::xxx 記法で呼ぶ識別子（半角英数）',
    )
    image = models.ImageField(
        upload_to='characters/',
        verbose_name='アイコン画像',
        help_text='正方形・円形クロップ推奨。256x256px 以上',
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='編集画面に表示',
        help_text='オフにすると編集画面の挿入ボタンに出ない（既存記事は引き続き表示OK）',
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        verbose_name='表示順',
        help_text='編集画面での並び順（小さい順）',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='作成日時')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新日時')

    class Meta:
        verbose_name = 'キャラクター'
        verbose_name_plural = 'キャラクター'
        # 表示順 → 名前順で並べる
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class CocoSettings(models.Model):
    """管理者が全読者向けのCOCO機能を切り替える、サイト共通の1件。"""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    speech_enabled = models.BooleanField(
        default=True,
        verbose_name='音声読み上げを有効にする',
        help_text='OFFで全読者の音声生成を停止します。文章の会話とCOCOの動きは続きます。',
    )
    age = models.CharField(max_length=60, blank=True, verbose_name='年齢', help_text='空欄なら未設定。「内緒」の返答を使い、誕生日から年齢を推測しません。')
    birthday_month = models.PositiveSmallIntegerField(default=5, blank=True, null=True, validators=[MinValueValidator(1), MaxValueValidator(12)], verbose_name='誕生日（月）')
    birthday_day = models.PositiveSmallIntegerField(default=5, blank=True, null=True, validators=[MinValueValidator(1), MaxValueValidator(31)], verbose_name='誕生日（日）', help_text='初期設定は5月5日。未設定にするときは月・日を両方空欄にしてください。')
    favorite_things = models.CharField(max_length=200, blank=True, default='海老・またたび', verbose_name='好きなもの', help_text='空欄なら未設定。')
    first_person = models.CharField(max_length=20, default='COCO', verbose_name='一人称')
    speaking_style = models.TextField(max_length=1000, default='日本語で親しみやすく、短い2〜4文で答える。', verbose_name='性格・話し方', help_text='口調や性格を記入してください。プロフィールの事実は上の各項目を使います。')
    private_reply = models.CharField(max_length=200, default='内緒❤️', verbose_name='未設定のプロフィールへの返答', help_text='設定のない年齢・出身などには、この文で答えます。同じ定型文の音声は保存して使い回します。')
    chat_daily_limit = models.PositiveIntegerField(default=100, verbose_name='サイト全体・1日の会話受付上限', help_text='定型返答も含みます。0で新しい会話の受付を停止。')
    chat_monthly_limit = models.PositiveIntegerField(default=3000, verbose_name='サイト全体・1か月の会話受付上限', help_text='日本時間の暦月で集計。0で新しい会話の受付を停止。')
    chat_visitor_daily_limit = models.PositiveIntegerField(default=30, verbose_name='読者1人・1日の会話受付上限')
    chat_per_minute_limit = models.PositiveIntegerField(default=5, verbose_name='読者1人・1分の会話受付上限')
    chat_max_concurrent = models.PositiveSmallIntegerField(default=2, verbose_name='サイト全体・同時に処理する会話数', help_text='同じ読者の同時処理は1件です。')
    speech_daily_char_limit = models.PositiveIntegerField(default=10000, verbose_name='音声・1日の新規生成文字数上限', help_text='演技指示や空白も含む送信文字数。0で新規生成を停止。保存済み音声の再生は利用できます。')
    speech_monthly_char_limit = models.PositiveIntegerField(default=100000, verbose_name='音声・1か月の新規生成文字数上限', help_text='日本時間の暦月で集計。上限に達しても文章の返答は続きます。')
    usage_tracking_started_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name='詳細集計の開始日時')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新日時')

    class Meta:
        verbose_name = 'COCO設定'
        verbose_name_plural = 'COCO設定'
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name='coco_settings_singleton'),
        ]

    def __str__(self):
        return 'COCOちゃんの設定'

    def clean(self):
        super().clean()
        month, day = self.birthday_month, self.birthday_day
        if (month is None) != (day is None):
            raise ValidationError({'birthday_day': '誕生日は月・日を両方入力するか、両方空欄にしてください。'})
        if month is not None and day is not None:
            try:
                date(2000, month, day)  # 年齢を設定しない。2月29日も誕生日として許可する。
            except (ValueError, TypeError):
                raise ValidationError({'birthday_day': '存在する日付を入力してください。'}) from None


class CocoSpeechClip(models.Model):
    """公開プロフィールの定型文だけを保存。会話本文の音声は保存しない。"""

    key = models.CharField(max_length=64, primary_key=True, editable=False)
    audio = models.BinaryField(default=bytes, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)


class CocoUsageCounters(models.Model):
    """日別・月別で同じ利用項目を集計する。本文やAPIキーは保存しない。"""

    total_requests = models.PositiveIntegerField(default=0, verbose_name='会話受付数')
    ai_requests = models.PositiveIntegerField(default=0, verbose_name='AI呼び出し数')
    ai_reported_requests = models.PositiveIntegerField(default=0, verbose_name='トークン取得済みのAI呼び出し数')
    ai_input_tokens = models.PositiveBigIntegerField(default=0, verbose_name='入力トークン数')
    ai_output_tokens = models.PositiveBigIntegerField(default=0, verbose_name='出力トークン数')
    ai_cached_input_tokens = models.PositiveBigIntegerField(default=0, verbose_name='入力のうちキャッシュ済みトークン数')
    speech_requests = models.PositiveIntegerField(default=0, verbose_name='音声の新規生成試行数')
    speech_characters = models.PositiveBigIntegerField(default=0, verbose_name='音声の新規生成文字数')
    speech_cache_hits = models.PositiveIntegerField(default=0, verbose_name='保存済み音声の再利用数')
    speech_failures = models.PositiveIntegerField(default=0, verbose_name='音声生成の失敗数')

    class Meta:
        abstract = True


class CocoChatUsage(CocoUsageCounters):
    """会話本文を残さず、日ごとの利用数・一時的な実行枠だけを共有する。"""

    day = models.DateField(primary_key=True, verbose_name='日付')
    visitors = models.JSONField(default=dict)
    active_requests = models.JSONField(default=dict)

    class Meta:
        verbose_name = 'COCO日別利用量'
        verbose_name_plural = 'COCO日別利用量'
        ordering = ['-day']

    def __str__(self):
        return self.day.strftime('%Y年%m月%d日')


class CocoMonthlyUsage(CocoUsageCounters):
    month = models.DateField(primary_key=True, verbose_name='対象月（1日）')

    class Meta:
        verbose_name = 'COCO月別利用量'
        verbose_name_plural = 'COCO月別利用量'
        ordering = ['-month']

    def __str__(self):
        return self.month.strftime('%Y年%m月')

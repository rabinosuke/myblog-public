"""ハードコードされていた coco / noel を DB 化するシードマイグレーション。

これまで blog/templatetags/markdown_tags.py の CHARACTERS dict に
ハードコードされていた 2 キャラを Character モデルに移行する。

このマイグレーションを実行すると：
  1. blog/static/blog/img/characters/{coco,noel}-normal.png を
     MEDIA_ROOT/characters/{coco,noel}.png にコピー
  2. Character レコード（coco, noel）を作成

戻し（reverse）時はレコードと媒体ファイルの両方を削除する。

このマイグレーションが先に当たることが Phase 4（レンダラを DB 参照化）の
前提条件。順序を逆にすると既存記事 :::coco / :::noel が一瞬壊れる。

参考URL:
  - Django Data Migrations: https://docs.djangoproject.com/en/5.0/topics/migrations/#data-migrations
  - RunPython の reverse_code: https://docs.djangoproject.com/en/5.0/ref/migration-operations/#runpython
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.db import migrations


# (slug, name, 元の静的ファイル名, sort_order)
SEED_CHARACTERS = [
    ('coco', 'ココ', 'coco-normal.png', 10),
    ('noel', 'ノエル', 'noel-normal.png', 20),
]


def _copy_static_to_media(static_filename, slug):
    """静的ファイルを MEDIA_ROOT/characters/ にコピーする。

    戻り値: ImageField に保存する相対パス（'characters/coco.png' など）
    """
    src = Path(settings.BASE_DIR) / 'blog' / 'static' / 'blog' / 'img' / 'characters' / static_filename
    dest_dir = Path(settings.MEDIA_ROOT) / 'characters'
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f'{slug}.png'

    if src.exists():
        shutil.copy2(src, dest)
    # src が見つからない場合はファイルコピーをスキップして DB レコードだけ作る
    # （管理画面から後で画像を差し替えてもらう想定）
    return f'characters/{slug}.png'


def seed_forward(apps, schema_editor):
    """coco / noel を作成する。既に同一 slug があれば skip（冪等性確保）。"""
    Character = apps.get_model('blog', 'Character')
    for slug, name, static_filename, sort_order in SEED_CHARACTERS:
        if Character.objects.filter(slug=slug).exists():
            continue
        image_rel = _copy_static_to_media(static_filename, slug)
        Character.objects.create(
            slug=slug,
            name=name,
            image=image_rel,
            is_active=True,
            sort_order=sort_order,
        )


def seed_reverse(apps, schema_editor):
    """シードしたキャラを削除する。媒体ファイルも消す。"""
    Character = apps.get_model('blog', 'Character')
    for slug, _name, _static_filename, _sort_order in SEED_CHARACTERS:
        try:
            char = Character.objects.get(slug=slug)
        except Character.DoesNotExist:
            continue
        # 媒体ファイルがあれば削除
        if char.image:
            media_path = Path(settings.MEDIA_ROOT) / char.image.name
            if media_path.exists():
                media_path.unlink()
        char.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0006_character_alter_post_options'),
    ]

    operations = [
        migrations.RunPython(seed_forward, seed_reverse),
    ]

from django.db import migrations, models


def create_settings(apps, schema_editor):
    # 既存の読み上げ機能を維持したまま、以降は管理画面で切り替える。
    apps.get_model('blog', 'CocoSettings').objects.using(schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('blog', '0009_cocochatusage')]

    operations = [
        migrations.CreateModel(
            name='CocoSettings',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('speech_enabled', models.BooleanField(default=True, help_text='OFFで全読者の音声生成を停止します。文章の会話とCOCOの動きは続きます。', verbose_name='音声読み上げを有効にする')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新日時')),
            ],
            options={
                'verbose_name': 'COCO設定',
                'verbose_name_plural': 'COCO設定',
                'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='coco_settings_singleton')],
            },
        ),
        migrations.RunPython(create_settings, migrations.RunPython.noop),
    ]

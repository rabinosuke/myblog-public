import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('blog', '0010_cocosettings')]

    operations = [
        migrations.AddField(model_name='cocosettings', name='age', field=models.CharField(blank=True, max_length=60, verbose_name='年齢', help_text='空欄なら未設定。「内緒」の返答を使い、誕生日から年齢を推測しません。')),
        migrations.AddField(model_name='cocosettings', name='birthday_month', field=models.PositiveSmallIntegerField(blank=True, default=5, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(12)], verbose_name='誕生日（月）')),
        migrations.AddField(model_name='cocosettings', name='birthday_day', field=models.PositiveSmallIntegerField(blank=True, default=5, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(31)], verbose_name='誕生日（日）', help_text='初期設定は5月5日。未設定にするときは月・日を両方空欄にしてください。')),
        migrations.AddField(model_name='cocosettings', name='favorite_things', field=models.CharField(blank=True, default='海老・またたび', max_length=200, verbose_name='好きなもの', help_text='空欄なら未設定。')),
        migrations.AddField(model_name='cocosettings', name='first_person', field=models.CharField(default='COCO', max_length=20, verbose_name='一人称')),
        migrations.AddField(model_name='cocosettings', name='speaking_style', field=models.TextField(default='日本語で親しみやすく、短い2〜4文で答える。', max_length=1000, verbose_name='性格・話し方', help_text='口調や性格を記入してください。プロフィールの事実は上の各項目を使います。')),
        migrations.AddField(model_name='cocosettings', name='private_reply', field=models.CharField(default='内緒❤️', max_length=200, verbose_name='未設定のプロフィールへの返答', help_text='設定のない年齢・出身などには、この文で答えます。同じ定型文の音声は保存して使い回します。')),
        migrations.CreateModel(name='CocoSpeechClip', fields=[
            ('key', models.CharField(editable=False, max_length=64, primary_key=True, serialize=False)),
            ('audio', models.BinaryField(default=bytes, editable=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
        ]),
    ]

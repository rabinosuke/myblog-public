from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('blog', '0008_tag_post_tags')]

    operations = [
        migrations.CreateModel(
            name='CocoChatUsage',
            fields=[
                ('day', models.DateField(primary_key=True, serialize=False)),
                ('total_requests', models.PositiveIntegerField(default=0)),
                ('visitors', models.JSONField(default=dict)),
                ('active_requests', models.JSONField(default=dict)),
            ],
        ),
    ]

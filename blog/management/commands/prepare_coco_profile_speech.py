"""管理者が設定した定型文の音声を準備する。APIキーや会話本文は出力しない。"""

from django.core.management.base import BaseCommand, CommandError

from blog.models import CocoSpeechClip
from blog.services.coco_profile import load_profile, profile_replies
from blog.services.coco_speech import SpeechError, generate_speech


class Command(BaseCommand):
    help = 'COCOの年齢・誕生日・好きなもの・未設定の定型音声を一度生成して保存する'

    def handle(self, *args, **options):
        replies = list(dict.fromkeys(profile_replies(load_profile()).values()))
        before = CocoSpeechClip.objects.exclude(audio=b'').count()
        try:
            for reply in replies:
                generate_speech(reply)
        except SpeechError as error:
            raise CommandError(error.message) from None
        generated = CocoSpeechClip.objects.exclude(audio=b'').count() - before
        self.stdout.write(self.style.SUCCESS(f'定型音声を{len(replies)}件準備しました（新規生成{generated}件、再利用{len(replies)-generated}件）。'))

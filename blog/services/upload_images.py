"""画像の実形式と展開量を、デコード・保存より前に制限する。"""

import warnings

from django.core.exceptions import ValidationError
from PIL import Image

ALLOWED_FORMATS = {'JPEG': '.jpg', 'PNG': '.png', 'GIF': '.gif', 'WEBP': '.webp'}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 16_000_000
MAX_OUTPUT_HEIGHT = 2400


def _check_dimensions(image):
    if image.format not in ALLOWED_FORMATS:
        raise ValidationError('PNG・JPEG・GIF・WebP形式の画像を選んでください。')
    width, height = image.size
    if min(width, height) < 1 or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
        raise ValidationError('画像が大きすぎます。各辺8192px以内・1600万画素以内にしてください。')


def prepare_image(upload, max_width):
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError('画像ファイルは10MB以内にしてください。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            # 拡張子では形式を判定しない。EPS等の不要なパーサーへ必ず到達させない。
            with Image.open(upload, formats=list(ALLOWED_FORMATS)) as image:
                _check_dimensions(image)
                image.verify()
            upload.seek(0)
            with Image.open(upload, formats=list(ALLOWED_FORMATS)) as image:
                _check_dimensions(image)
                extension = ALLOWED_FORMATS[image.format]
                # GIF等は従来同様に先頭フレームだけを保存する。
                mode = 'RGB' if extension == '.jpg' or image.mode not in ('RGBA', 'LA', 'P') else 'RGBA'
                prepared = image.convert(mode)
        prepared.info.clear()
        prepared.thumbnail((max_width, MAX_OUTPUT_HEIGHT), Image.Resampling.LANCZOS)
        return prepared, extension
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValidationError('PNG・JPEG・GIF・WebP形式の正常な画像を選んでください。') from None

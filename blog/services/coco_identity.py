"""匿名会話の識別を、管理画面のDBセッションから分離する。"""

import re
from ipaddress import ip_address, ip_network
from uuid import uuid4

from django.conf import settings

COOKIE_NAME = 'coco_visitor'
COOKIE_SALT = 'blog.coco.visitor.v1'
COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def visitor_id(request):
    value = request.get_signed_cookie(
        COOKIE_NAME, default='', salt=COOKIE_SALT, max_age=COOKIE_MAX_AGE,
    )
    return value if re.fullmatch(r'[0-9a-f]{32}', value) else uuid4().hex


def set_visitor_cookie(response, identity):
    response.set_signed_cookie(
        COOKIE_NAME, identity, salt=COOKIE_SALT, max_age=COOKIE_MAX_AGE,
        httponly=True, secure=settings.SESSION_COOKIE_SECURE, samesite='Lax',
    )


def client_network(request):
    address = request.META.get('REMOTE_ADDR', '')
    # Composeはwebを外部公開せず、Nginxがこのヘッダーを必ず上書きする。
    # 直接runserver等で受ける場合は、利用者のヘッダーを信頼しない。
    if settings.COCO_CHAT_TRUST_PROXY:
        address = request.META.get('HTTP_X_REAL_IP', address)
    try:
        address = ip_address(address)
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        # IPv6の末尾だけを変更する回避を防ぎ、同じ回線はIPv4 NATと同様に扱う。
        return str(ip_network(f'{address}/64', strict=False)) if address.version == 6 else str(address)
    except ValueError:
        # 不正・欠落を毎回別の識別子にせず、共通の制限へ閉じる。
        return 'unknown'

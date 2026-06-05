"""API-key authentication for the v7 package API.

Keys look like `kpf_<prefix>_<secret>`. Only the `prefix` (for lookup)
and a salted hash of the full key are stored; the plaintext is shown
once at issuance and never persisted.

A successful authentication stashes the key's organisation on the
request (`request.organisation`) for the permission/view layer. Org
*service* keys have no associated user, so `request.user` stays
`AnonymousUser` while `request.organisation` is still set.
"""
from __future__ import annotations

import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import ApiKey


def generate_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, hash). The plaintext is shown once."""
    prefix = secrets.token_hex(4)          # 8 hex chars
    secret = secrets.token_urlsafe(32)
    plaintext = f'kpf_{prefix}_{secret}'
    return plaintext, prefix, make_password(plaintext)


def _lookup_and_verify(raw: str) -> ApiKey | None:
    parts = raw.split('_', 2)
    if len(parts) != 3 or parts[0] != 'kpf':
        return None
    prefix = parts[1]
    for key in ApiKey.objects.filter(prefix=prefix, revoked_at__isnull=True):
        if check_password(raw, key.hash):
            return key
    return None


class ApiKeyAuthentication(BaseAuthentication):
    keyword = 'Api-Key'

    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith(self.keyword + ' '):
            return None                    # fall through to SessionAuthentication
        raw = header[len(self.keyword) + 1:].strip()
        key = _lookup_and_verify(raw)
        if key is None:
            raise AuthenticationFailed('invalid API key')
        request.organisation = key.organisation
        return (key.user or AnonymousUser(), key)

    def authenticate_header(self, request):
        # Returning a value makes DRF answer 401 (not 403) on auth failure.
        return self.keyword

"""Encrypted model field (Fernet) for Zalo tokens and login sessions."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

_PREFIX = "enc$"


@lru_cache(maxsize=4)
def _fernet(key: str) -> Fernet:
    return Fernet(key.encode())


def get_fernet() -> Fernet:
    key = getattr(settings, "ENCRYPTION_KEY", None)
    if not key:
        if settings.DEBUG:
            # Dev only: derive a key from SECRET_KEY. Production requires ENCRYPTION_KEY.
            digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
            key = base64.urlsafe_b64encode(digest).decode()
        else:
            raise ImproperlyConfigured("ENCRYPTION_KEY is required to store encrypted fields")
    return _fernet(key)


def encrypt_value(value: str) -> str:
    return _PREFIX + get_fernet().encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    if not value.startswith(_PREFIX):
        return value
    try:
        return get_fernet().decrypt(value[len(_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise ImproperlyConfigured("Cannot decrypt field: wrong ENCRYPTION_KEY") from exc


class EncryptedTextField(models.TextField):
    """Stores text encrypted at rest; transparently decrypts on load."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return encrypt_value(value)

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        return decrypt_value(value)

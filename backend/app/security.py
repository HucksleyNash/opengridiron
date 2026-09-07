from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import settings

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.resolved_secret().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt secret with the configured APP_SECRET") from exc


def create_session(owner_id: int, ttl_seconds: int = 60 * 60 * 24 * 14) -> str:
    payload = {
        "owner_id": owner_id,
        "exp": int(time.time()) + ttl_seconds,
        "nonce": secrets.token_hex(8),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    signature = hmac.new(
        settings.resolved_secret().encode(), raw.encode(), hashlib.sha256
    ).hexdigest()
    return f"{raw}.{signature}"


def parse_session(token: str) -> dict[str, Any] | None:
    try:
        raw, signature = token.rsplit(".", 1)
        expected = hmac.new(
            settings.resolved_secret().encode(), raw.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        if int(payload["exp"]) < int(time.time()):
            return None
        return payload
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)

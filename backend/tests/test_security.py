from __future__ import annotations

from app.security import (
    create_session,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    parse_session,
    verify_password,
)


def test_secret_encryption_and_password_hashing() -> None:
    encrypted = encrypt_secret("provider-secret")
    assert encrypted != "provider-secret"
    assert decrypt_secret(encrypted) == "provider-secret"
    hashed = hash_password("a-long-owner-password")
    assert verify_password(hashed, "a-long-owner-password")
    assert not verify_password(hashed, "wrong-password")


def test_signed_session_rejects_tampering() -> None:
    token = create_session(42)
    assert parse_session(token)["owner_id"] == 42
    assert parse_session(f"{token[:-1]}x") is None

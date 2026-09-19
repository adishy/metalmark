"""Password hashing (argon2id) and secure token helpers (ARCHITECTURE §5)."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id defaults are sane for a small self-host


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


def new_token(nbytes: int = 32) -> str:
    """A URL-safe random token (session ids, invite tokens, CSRF tokens)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Hash a bearer token (invite/session lookup) with SHA-256.

    Tokens are high-entropy random values, so a fast hash is appropriate and
    lets us index the digest for lookup without storing the raw token.
    """
    return hashlib.sha256(token.encode()).hexdigest()

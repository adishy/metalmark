"""Authenticated symmetric encryption for secrets at rest (ARCHITECTURE §5).

Used for SimpleFIN ``access_url`` (a Basic-auth URL that must never be logged
or returned). Key is derived from ``KESTREL_SECRET_KEY`` (a docker secret).

We use Fernet (AES-128-CBC + HMAC-SHA256) from ``cryptography``. The secret
file holds a urlsafe-base64 32-byte key; if it isn't already a valid Fernet
key we derive one deterministically so operators can supply arbitrary
high-entropy strings.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet_key_from_secret(secret: str) -> bytes:
    raw = secret.encode()
    try:
        decoded = base64.urlsafe_b64decode(raw)
        if len(decoded) == 32:
            return raw
    except Exception:  # noqa: BLE001 - fall through to derivation
        pass
    # Derive a stable 32-byte key from an arbitrary secret.
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    def __init__(self, secret: str) -> None:
        self._fernet = Fernet(_fernet_key_from_secret(secret))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:  # pragma: no cover - defensive
            raise ValueError("Could not decrypt secret (wrong key or corrupt data)") from exc

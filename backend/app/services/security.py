import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

from app.core.config import settings


PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_SALT_BYTES = 16


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _urlsafe_b64decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("utf-8"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        settings.password_hash_iterations,
    )
    return (
        f"{PBKDF2_ALGORITHM}"
        f"${settings.password_hash_iterations}"
        f"${_urlsafe_b64encode(salt)}"
        f"${_urlsafe_b64encode(derived_key)}"
    )


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False

    if password_hash.startswith(f"{PBKDF2_ALGORITHM}$"):
        try:
            _, iterations_text, salt_text, expected_text = password_hash.split("$", 3)
            iterations = int(iterations_text)
            salt = _urlsafe_b64decode(salt_text)
            expected = _urlsafe_b64decode(expected_text)
        except (TypeError, ValueError):
            return False

        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)

    legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy_hash, password_hash)


def needs_password_rehash(password_hash: str) -> bool:
    if not password_hash.startswith(f"{PBKDF2_ALGORITHM}$"):
        return True

    try:
        _, iterations_text, _, _ = password_hash.split("$", 3)
        return int(iterations_text) < settings.password_hash_iterations
    except (TypeError, ValueError):
        return True


def create_token(user_id: int, username: str, role: str) -> str:
    issued_at = int(time.time())
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "iat": issued_at,
        "exp": issued_at + settings.access_token_ttl_seconds,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return _urlsafe_b64encode(raw) + "." + signature


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload_part, signature = token.split(".", 1)
        raw = _urlsafe_b64decode(payload_part)
        expected = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(raw.decode("utf-8"))
        expires_at = int(payload["exp"])
        if expires_at <= int(time.time()):
            return None
        return payload
    except Exception:
        return None

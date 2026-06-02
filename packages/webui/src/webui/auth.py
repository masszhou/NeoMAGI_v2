"""Single-operator password and signed-cookie helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass


class AuthConfigError(RuntimeError):
    """Raised when WebUI auth configuration is unsafe or missing."""


@dataclass(frozen=True, slots=True)
class SessionCookie:
    name: str = "webui_session"
    max_age_seconds: int = 12 * 60 * 60


PBKDF2_PREFIX = "pbkdf2_sha256"
MIN_ITERATIONS = 600_000
DEFAULT_ITERATIONS = MIN_ITERATIONS
CSRF_COOKIE_NAME = "webui_csrf"
SESSION_COOKIE = SessionCookie()


def hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = DEFAULT_ITERATIONS,
) -> str:
    if not password:
        raise AuthConfigError("password must be non-empty")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "$".join(
        (
            PBKDF2_PREFIX,
            str(iterations),
            _b64(salt),
            _b64(digest),
        )
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        prefix, iterations_s, salt_s, digest_s = encoded_hash.split("$", 3)
        if prefix != PBKDF2_PREFIX:
            return False
        iterations = int(iterations_s)
        salt = _unb64(salt_s)
        expected = _unb64(digest_s)
    except (ValueError, TypeError, binascii.Error):
        return False
    if iterations < MIN_ITERATIONS:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(actual, expected)


def validate_password_hash(encoded_hash: str) -> None:
    try:
        prefix, iterations_s, salt_s, digest_s = encoded_hash.split("$", 3)
        if prefix != PBKDF2_PREFIX:
            raise AuthConfigError("WEBUI_PASSWORD_HASH must use pbkdf2_sha256 format")
        if int(iterations_s) < MIN_ITERATIONS:
            raise AuthConfigError(
                f"WEBUI_PASSWORD_HASH iterations must be at least {MIN_ITERATIONS}"
            )
        if not _unb64(salt_s) or not _unb64(digest_s):
            raise AuthConfigError("WEBUI_PASSWORD_HASH is malformed")
    except (ValueError, TypeError, binascii.Error) as exc:
        raise AuthConfigError("WEBUI_PASSWORD_HASH is malformed") from exc


def sign_session(secret: str, *, now: int | None = None) -> str:
    timestamp = str(now if now is not None else int(time.time()))
    nonce = secrets.token_urlsafe(18)
    payload = f"v1.{timestamp}.{nonce}"
    return f"{payload}.{_signature(secret, payload)}"


def verify_session(
    secret: str,
    cookie_value: str | None,
    *,
    now: int | None = None,
    max_age_seconds: int = SESSION_COOKIE.max_age_seconds,
) -> bool:
    if not cookie_value:
        return False
    parts = cookie_value.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return False
    payload = ".".join(parts[:3])
    if not hmac.compare_digest(parts[3], _signature(secret, payload)):
        return False
    try:
        issued_at = int(parts[1])
    except ValueError:
        return False
    current = now if now is not None else int(time.time())
    return 0 <= current - issued_at <= max_age_seconds


def new_csrf_pair(secret: str) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(18)
    token = _signature(secret, f"csrf.{nonce}")
    return nonce, token


def verify_csrf(secret: str, nonce: str | None, token: str | None) -> bool:
    if not nonce or not token:
        return False
    expected = _signature(secret, f"csrf.{nonce}")
    return hmac.compare_digest(token, expected)


def _signature(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return _b64(digest)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode())


__all__ = [
    "AuthConfigError",
    "CSRF_COOKIE_NAME",
    "SESSION_COOKIE",
    "hash_password",
    "new_csrf_pair",
    "sign_session",
    "validate_password_hash",
    "verify_csrf",
    "verify_password",
    "verify_session",
]

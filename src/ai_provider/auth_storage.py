"""Local auth storage for reusable provider credentials."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

from .oauth import (
    OAuthCredentials,
    TOKEN_REFRESH_SKEW_MS,
    refresh_openai_oauth_credentials_sync,
)

AUTH_PATH_ENV = "NEOMAGI_AUTH_PATH"
AUTH_FILE_MODE = 0o600
AUTH_DIR_MODE = 0o700
DEFAULT_AUTH_PATH = Path.home() / ".neomagi" / "auth.json"
OPENAI_CODEX_PROVIDER = "openai-codex"


class AuthStorageError(RuntimeError):
    """Raised when local auth storage cannot be read or written safely."""


def resolve_auth_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    configured = os.environ.get(AUTH_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_AUTH_PATH


def load_auth_storage(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, dict[str, Any]]:
    resolved = resolve_auth_path(path)
    if not resolved.exists():
        return {}
    with _locked_existing_auth_file(resolved) as file:
        return _read_storage(file)


def save_auth_storage(
    storage: Mapping[str, Mapping[str, Any]],
    path: str | os.PathLike[str] | None = None,
) -> None:
    resolved = resolve_auth_path(path)
    with _locked_auth_file(resolved) as file:
        _write_storage(file, _normalize_storage(storage))


def save_oauth_credentials(
    provider: str,
    credentials: OAuthCredentials,
    path: str | os.PathLike[str] | None = None,
) -> None:
    def mutate(storage: dict[str, dict[str, Any]]) -> None:
        storage[provider] = {"type": "oauth", **credentials.to_mapping()}

    _mutate_auth_storage(mutate, path)


def save_api_key(
    provider: str,
    api_key: str,
    path: str | os.PathLike[str] | None = None,
) -> None:
    def mutate(storage: dict[str, dict[str, Any]]) -> None:
        storage[provider] = {"type": "api_key", "key": api_key}

    _mutate_auth_storage(mutate, path)


def delete_credential(
    provider: str,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    deleted = False

    def mutate(storage: dict[str, dict[str, Any]]) -> None:
        nonlocal deleted
        deleted = provider in storage
        storage.pop(provider, None)

    _mutate_auth_storage(mutate, path)
    return deleted


def list_credentials(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, dict[str, Any]]:
    storage = load_auth_storage(path)
    return {provider: redact_credential_entry(entry) for provider, entry in storage.items()}


def credential_status(
    provider: str,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    entry = load_auth_storage(path).get(provider)
    if entry is None:
        return None
    return redact_credential_entry(entry)


def redact_credential_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    entry_type = entry.get("type")
    redacted: dict[str, Any] = {"type": entry_type}
    if entry_type == "api_key":
        redacted["key"] = _redact_secret(entry.get("key"))
    elif entry_type == "oauth":
        redacted["access"] = _redact_secret(entry.get("access"))
        redacted["refresh"] = _redact_secret(entry.get("refresh"))
        if "expires" in entry:
            redacted["expires"] = entry["expires"]
        account_id = entry.get("accountId") or entry.get("account_id")
        if account_id:
            redacted["accountId"] = account_id
    else:
        redacted.update({"status": "unknown"})
    return redacted


def resolve_stored_api_key(
    provider: str,
    path: str | os.PathLike[str] | None = None,
    *,
    now_ms: Callable[[], int] | None = None,
) -> str | None:
    resolved = resolve_auth_path(path)
    if not resolved.exists():
        return None
    with _locked_existing_auth_file(resolved) as file:
        storage = _read_storage(file)
        entry = storage.get(provider)
        if not entry:
            return None
        api_key = _resolve_entry_api_key(provider, entry, now_ms or _now_ms)
        if isinstance(api_key, OAuthCredentials):
            storage[provider] = {"type": "oauth", **api_key.to_mapping()}
            _write_storage(file, storage)
            return api_key.access
        return api_key


def _resolve_entry_api_key(
    provider: str,
    entry: Mapping[str, Any],
    now_ms: Callable[[], int],
) -> str | OAuthCredentials | None:
    entry_type = entry.get("type")
    if entry_type == "api_key":
        key = entry.get("key")
        return key if isinstance(key, str) and key else None
    if entry_type != "oauth" or provider != OPENAI_CODEX_PROVIDER:
        return None

    credentials = OAuthCredentials.from_mapping(entry)
    if credentials.expires <= now_ms() + TOKEN_REFRESH_SKEW_MS:
        return refresh_openai_oauth_credentials_sync(credentials, now_ms=now_ms)
    return credentials.access


def _mutate_auth_storage(
    mutate: Callable[[dict[str, dict[str, Any]]], None],
    path: str | os.PathLike[str] | None,
) -> None:
    resolved = resolve_auth_path(path)
    with _locked_auth_file(resolved) as file:
        storage = _read_storage(file)
        mutate(storage)
        _write_storage(file, storage)


@contextmanager
def _locked_auth_file(path: Path) -> Iterator[TextIO]:
    _ensure_auth_file(path)
    with path.open("r+", encoding="utf-8") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            yield file
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked_existing_auth_file(path: Path) -> Iterator[TextIO]:
    with path.open("r+", encoding="utf-8") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            yield file
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _ensure_auth_file(path: Path) -> None:
    path.parent.mkdir(mode=AUTH_DIR_MODE, parents=True, exist_ok=True)
    _chmod_if_possible(path.parent, AUTH_DIR_MODE)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, AUTH_FILE_MODE)
    except FileExistsError:
        _chmod_if_possible(path, AUTH_FILE_MODE)
        return
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write("{}\n")
    _chmod_if_possible(path, AUTH_FILE_MODE)


def _read_storage(file: TextIO) -> dict[str, dict[str, Any]]:
    file.seek(0)
    raw = file.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthStorageError("auth storage is not valid JSON") from exc
    return _normalize_storage(parsed)


def _write_storage(file: TextIO, storage: Mapping[str, Mapping[str, Any]]) -> None:
    file.seek(0)
    file.truncate()
    json.dump(storage, file, indent=2, sort_keys=True)
    file.write("\n")
    file.flush()
    os.fsync(file.fileno())
    _chmod_if_possible(Path(file.name), AUTH_FILE_MODE)


def _normalize_storage(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise AuthStorageError("auth storage root must be a JSON object")
    storage: dict[str, dict[str, Any]] = {}
    for provider, entry in raw.items():
        if not isinstance(provider, str) or not isinstance(entry, Mapping):
            raise AuthStorageError("auth storage entries must be JSON objects")
        storage[provider] = dict(entry)
    return storage


def _chmod_if_possible(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        return


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _redact_secret(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


__all__ = [
    "AUTH_PATH_ENV",
    "AuthStorageError",
    "DEFAULT_AUTH_PATH",
    "credential_status",
    "delete_credential",
    "list_credentials",
    "load_auth_storage",
    "redact_credential_entry",
    "resolve_auth_path",
    "resolve_stored_api_key",
    "save_api_key",
    "save_auth_storage",
    "save_oauth_credentials",
]

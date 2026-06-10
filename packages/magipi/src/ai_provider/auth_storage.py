"""Local auth storage for reusable provider credentials.

The default location follows ADR-0019: ``$XDG_CONFIG_HOME/neomagi/auth.json``,
Windows ``%APPDATA%\\neomagi\\auth.json``, or ``~/.config/neomagi/auth.json``.
A legacy ``~/.neomagi/auth.json`` from earlier installs is migrated on first
resolve so users don't lose login state across upgrades.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TextIO

from .oauth import (
    OAuthCredentials,
    TOKEN_REFRESH_SKEW_MS,
    # Resolved at call time via globals() in _resolve_entry_api_key so tests can
    # monkeypatch the module-level refreshers; imported here to bind the name.
    refresh_openai_oauth_credentials_sync,  # noqa: F401
)
from .oauth_github_copilot import (
    GITHUB_COPILOT_PROVIDER_ID,
    refresh_github_copilot_credentials_sync,  # noqa: F401 - resolved via globals() dispatch
)

AUTH_PATH_ENV = "NEOMAGI_AUTH_PATH"
AUTH_FILE_MODE = 0o600
AUTH_DIR_MODE = 0o700
_USER_CONFIG_SUBDIR = "neomagi"
LEGACY_AUTH_PATH = Path.home() / ".neomagi" / "auth.json"
OPENAI_CODEX_PROVIDER = "openai-codex"
GITHUB_COPILOT_PROVIDER = GITHUB_COPILOT_PROVIDER_ID
KEYRING_SERVICE = "neomagi-pi"
KEYRING_USERNAME = "auth-storage-v1"
_MIGRATED_MARKER_KEY = "migrated_to_keyring"
_LOGGER = logging.getLogger("magipi.auth")
_WARNED_FILE_FALLBACK = False


class AuthStorageError(RuntimeError):
    """Raised when local auth storage cannot be read or written safely."""


class CredentialStore(Protocol):
    backend: str

    def load(self) -> dict[str, dict[str, Any]]: ...
    def save(self, storage: Mapping[str, Mapping[str, Any]]) -> None: ...
    def delete_all(self) -> None: ...
    def status(self) -> dict[str, Any]: ...


class FileCredentialStore:
    backend = "file"

    def __init__(self, path: Path, *, forced: bool = False) -> None:
        self.path = path
        self.forced = forced

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        with _locked_existing_auth_file(self.path) as file:
            return _read_storage(file)

    def save(self, storage: Mapping[str, Mapping[str, Any]]) -> None:
        with _locked_auth_file(self.path) as file:
            _write_storage(file, _normalize_storage(storage))

    def delete_all(self) -> None:
        self.save({})

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "path": str(self.path),
            "forced": self.forced,
            "secure": False,
        }


class KeyringCredentialStore:
    backend = "keyring"

    def __init__(self, keyring_module: Any) -> None:
        self._keyring = keyring_module

    def load(self) -> dict[str, dict[str, Any]]:
        raw = self._keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthStorageError("keyring auth storage is not valid JSON") from exc
        return _normalize_storage(parsed)

    def save(self, storage: Mapping[str, Mapping[str, Any]]) -> None:
        payload = json.dumps(_normalize_storage(storage), indent=2, sort_keys=True)
        self._keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, payload)

    def delete_all(self) -> None:
        try:
            self._keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            return

    def status(self) -> dict[str, Any]:
        backend = self._keyring.get_keyring()
        return {
            "backend": self.backend,
            "service": KEYRING_SERVICE,
            "username": KEYRING_USERNAME,
            "keyringBackend": f"{type(backend).__module__}.{type(backend).__name__}",
            "secure": True,
        }


def _user_config_auth_path(env: Mapping[str, str] | None = None) -> Path:
    """Return ``<config-root>/neomagi/auth.json`` per ADR-0019."""

    env_values = env if env is not None else os.environ
    xdg = (env_values.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / _USER_CONFIG_SUBDIR / "auth.json"
    if sys.platform == "win32":
        appdata = (env_values.get("APPDATA") or "").strip()
        if appdata:
            return Path(appdata) / _USER_CONFIG_SUBDIR / "auth.json"
    return Path.home() / ".config" / _USER_CONFIG_SUBDIR / "auth.json"


def default_auth_path(env: Mapping[str, str] | None = None) -> Path:
    """Resolved default auth path (post-migration)."""

    new_path = _user_config_auth_path(env)
    if new_path.exists() or not LEGACY_AUTH_PATH.exists():
        return new_path
    if _migrate_legacy_auth(new_path):
        return new_path
    # Migration failed: only fall back to legacy if it still has data,
    # otherwise the new path is canonical and `_ensure_auth_file` will
    # create it on demand (avoids recreating the legacy file post-failure).
    if LEGACY_AUTH_PATH.exists():
        return LEGACY_AUTH_PATH
    return new_path


# Backwards-compatible module attribute: the post-ADR-0019 default location,
# computed without migration so importing this module is side-effect-free.
# Callers that want migration semantics should use :func:`default_auth_path`
# or :func:`resolve_auth_path`.
DEFAULT_AUTH_PATH = _user_config_auth_path()


def resolve_auth_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    configured = os.environ.get(AUTH_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return default_auth_path()


def _migrate_legacy_auth(new_path: Path) -> bool:
    """Move ``~/.neomagi/auth.json`` to the user config dir.

    Returns ``True`` when the post-migration invariant holds (new path is a
    file and the legacy path is gone). If our own ``replace()`` raises but
    a concurrent process completed the move, we still return ``True``;
    real failures (permissions, filesystem errors) return ``False`` and the
    caller falls back to the legacy path so users don't lose login state.
    """

    try:
        new_path.parent.mkdir(mode=AUTH_DIR_MODE, parents=True, exist_ok=True)
        _chmod_if_possible(new_path.parent, AUTH_DIR_MODE)
        LEGACY_AUTH_PATH.replace(new_path)
        _chmod_if_possible(new_path, AUTH_FILE_MODE)
    except OSError as exc:
        # Another process may have completed the move between our existence
        # check and replace(); detect that via the post-migration invariant.
        if new_path.is_file() and not LEGACY_AUTH_PATH.exists():
            return True
        sys.stderr.write(
            f"warning: could not migrate {LEGACY_AUTH_PATH} to {new_path}: "
            f"{exc}; continuing with legacy path\n"
        )
        return False
    try:
        LEGACY_AUTH_PATH.parent.rmdir()
    except OSError:
        pass
    return True


def load_auth_storage(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, dict[str, Any]]:
    return _credential_store(path).load()


def save_auth_storage(
    storage: Mapping[str, Mapping[str, Any]],
    path: str | os.PathLike[str] | None = None,
) -> None:
    _credential_store(path).save(storage)


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


@dataclass(frozen=True, slots=True)
class StoredCredential:
    """A resolved stored credential: the api key plus OAuth ``extra`` metadata.

    ``extra`` carries provider-specific fields (e.g. GitHub Copilot's
    ``enterpriseUrl``) so callers can derive a base url from the same single
    resolution without re-reading storage or triggering a second refresh.
    Empty for api-key entries.
    """

    api_key: str
    extra: dict[str, Any]


_OAUTH_ENTRY_KNOWN_KEYS = frozenset(
    {"type", "access", "refresh", "expires", "accountId", "account_id"}
)


def resolve_stored_credential(
    provider: str,
    path: str | os.PathLike[str] | None = None,
    *,
    now_ms: Callable[[], int] | None = None,
) -> StoredCredential | None:
    storage = load_auth_storage(path)
    entry = storage.get(provider)
    if not entry:
        return None
    resolved = _resolve_entry_api_key(provider, entry, now_ms or _now_ms)
    if isinstance(resolved, OAuthCredentials):
        storage[provider] = {"type": "oauth", **resolved.to_mapping()}
        save_auth_storage(storage, path)
        return StoredCredential(api_key=resolved.access, extra=dict(resolved.extra))
    if not resolved:
        return None
    return StoredCredential(api_key=resolved, extra=_oauth_entry_extra(entry))


def resolve_stored_api_key(
    provider: str,
    path: str | os.PathLike[str] | None = None,
    *,
    now_ms: Callable[[], int] | None = None,
) -> str | None:
    resolved = resolve_stored_credential(provider, path, now_ms=now_ms)
    return resolved.api_key if resolved else None


def _oauth_entry_extra(entry: Mapping[str, Any]) -> dict[str, Any]:
    if entry.get("type") != "oauth":
        return {}
    return {key: value for key, value in entry.items() if key not in _OAUTH_ENTRY_KNOWN_KEYS}


# Per-provider synchronous OAuth refreshers used by the runtime credential
# boundary. Mapped by *name* (not function object) so the refresher is resolved
# from module globals at call time — this keeps monkeypatching the module-level
# refresher functions working in tests. Each refresher accepts
# ``(credentials, *, now_ms=...)`` and returns refreshed ``OAuthCredentials``;
# the ``now_ms`` keyword is required so callers can inject a deterministic clock.
# Providers absent from this map are not resolvable through the stored path.
_SYNC_OAUTH_REFRESHER_NAMES: dict[str, str] = {
    OPENAI_CODEX_PROVIDER: "refresh_openai_oauth_credentials_sync",
    GITHUB_COPILOT_PROVIDER: "refresh_github_copilot_credentials_sync",
}


def _resolve_entry_api_key(
    provider: str,
    entry: Mapping[str, Any],
    now_ms: Callable[[], int],
) -> str | OAuthCredentials | None:
    entry_type = entry.get("type")
    if entry_type == "api_key":
        key = entry.get("key")
        return key if isinstance(key, str) and key else None
    if entry_type != "oauth":
        return None
    refresher_name = _SYNC_OAUTH_REFRESHER_NAMES.get(provider)
    if refresher_name is None:
        return None

    credentials = OAuthCredentials.from_mapping(entry)
    if credentials.expires <= now_ms() + TOKEN_REFRESH_SKEW_MS:
        refresher: Callable[..., OAuthCredentials] = globals()[refresher_name]
        return refresher(credentials, now_ms=now_ms)
    return credentials.access


def _mutate_auth_storage(
    mutate: Callable[[dict[str, dict[str, Any]]], None],
    path: str | os.PathLike[str] | None,
) -> None:
    storage = load_auth_storage(path)
    mutate(storage)
    save_auth_storage(storage, path)


def auth_storage_status(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return _credential_store(path).status()


def _credential_store(path: str | os.PathLike[str] | None) -> CredentialStore:
    if path is not None or os.environ.get(AUTH_PATH_ENV):
        return FileCredentialStore(resolve_auth_path(path), forced=True)
    keyring_module = _load_keyring_module()
    if keyring_module is not None and _keyring_is_available(keyring_module):
        store = KeyringCredentialStore(keyring_module)
        _migrate_file_auth_to_keyring(store)
        return store
    _warn_file_fallback()
    return FileCredentialStore(resolve_auth_path(path), forced=False)


def _load_keyring_module() -> Any | None:
    try:
        import keyring
    except ModuleNotFoundError:
        return None
    return keyring


def _keyring_is_available(keyring_module: Any) -> bool:
    try:
        backend = keyring_module.get_keyring()
    except Exception:
        return False
    backend_ref = f"{type(backend).__module__}.{type(backend).__name__}".lower()
    if "keyring.backends.fail" in backend_ref or "plaintext" in backend_ref or "keyrings.alt.file" in backend_ref:
        return False
    probe_user = f"{KEYRING_USERNAME}-probe-{os.getpid()}"
    try:
        keyring_module.set_password(KEYRING_SERVICE, probe_user, "ok")
        keyring_module.get_password(KEYRING_SERVICE, probe_user)
        keyring_module.delete_password(KEYRING_SERVICE, probe_user)
    except Exception:
        return False
    return True


def _migrate_file_auth_to_keyring(store: KeyringCredentialStore) -> None:
    file_path = default_auth_path()
    if not file_path.exists():
        return
    file_store = FileCredentialStore(file_path)
    try:
        storage = file_store.load()
    except AuthStorageError:
        return
    if not storage:
        return
    store.save(storage)
    marker = {
        _MIGRATED_MARKER_KEY: True,
        "migrated_at": datetime.now(UTC).isoformat(),
        "backend": "keyring",
        "service": KEYRING_SERVICE,
        "username": KEYRING_USERNAME,
    }
    with _locked_auth_file(file_path) as file:
        file.seek(0)
        file.truncate()
        json.dump(marker, file, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())


def _warn_file_fallback() -> None:
    global _WARNED_FILE_FALLBACK
    if _WARNED_FILE_FALLBACK:
        return
    _WARNED_FILE_FALLBACK = True
    message = "secure keyring unavailable; falling back to 0600 JSON auth storage"
    _LOGGER.warning(message)
    sys.stderr.write(f"warning: {message}\n")


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
    if raw.get(_MIGRATED_MARKER_KEY) is True:
        return {}
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
    "GITHUB_COPILOT_PROVIDER",
    "LEGACY_AUTH_PATH",
    "OPENAI_CODEX_PROVIDER",
    "credential_status",
    "auth_storage_status",
    "default_auth_path",
    "delete_credential",
    "list_credentials",
    "load_auth_storage",
    "redact_credential_entry",
    "StoredCredential",
    "resolve_auth_path",
    "resolve_stored_api_key",
    "resolve_stored_credential",
    "save_api_key",
    "save_auth_storage",
    "save_oauth_credentials",
]

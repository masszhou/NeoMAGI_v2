"""Database configuration for Postgres-backed durable session storage.

Lookup order is fixed by ADR-0019/0020: ``--env-file`` → shell
``DATABASE_*`` → ``NEOMAGI_ENV_FILE`` → user config
``secrets/database.env`` → repo ``.env`` (only inside an editable repo
checkout). Sources are picked as a *group*: required fields never merge
across sources. This protects against "stale shell host + old file password"
connecting to the wrong database (per ADR-0007).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Mapping


class DatabaseConfigError(RuntimeError):
    """Raised when database configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    schema: str = "neomagi"

    def connect_kwargs(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "dbname": self.database,
        }


@dataclass(frozen=True, slots=True)
class ConfigSource:
    """Where the active DATABASE_* group came from."""

    kind: str  # "env" | "file"
    label: str  # "environment" or absolute file path

    def format(self) -> str:
        if self.kind == "env":
            return "source=env"
        return f"source=file:{self.label}"


_REQUIRED_KEYS: tuple[str, ...] = (
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "DATABASE_NAME",
)
_SCHEMA_KEY = "DATABASE_SCHEMA"
_ENV_FILE_KEY = "NEOMAGI_ENV_FILE"
_USER_CONFIG_SUBDIR = "neomagi"
_REPO_MARKER_FILE = "pyproject.toml"
_REPO_MARKER_DIR = Path("packages") / "magipi"
_REMEDIATION_HINT = (
    "Run `magipi config init` to write a fresh database.env template to the "
    "user config directory, or `magipi config path` to inspect the active source."
)


def load_database_config(
    *,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> DatabaseConfig:
    """Resolve a complete ``DatabaseConfig`` per ADR-0019.

    - ``env_file``: explicit path from ``--env-file`` / equivalent. Must
      exist and be complete; otherwise fail-fast.
    - ``env``: caller-supplied environment mapping (defaults to
      ``os.environ``). Used for shell ``DATABASE_*`` and
      ``NEOMAGI_ENV_FILE``.
    """

    config, _ = resolve_database_config(env=env, env_file=env_file)
    return config


def resolve_database_config(
    *,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> tuple[DatabaseConfig, ConfigSource]:
    """Same as :func:`load_database_config` but also reports the active source."""

    env_values = env if env is not None else os.environ
    attempts: list[str] = []

    if env_file is not None:
        path = Path(env_file).expanduser()
        attempts.append(f"--env-file {path}")
        values = _read_explicit_file(path, "--env-file")
        return _build_config(values), ConfigSource(kind="file", label=str(path))

    shell = _collect_shell_values(env_values)
    if shell is not None:
        attempts.append("environment DATABASE_*")
        return _build_config(shell), ConfigSource(kind="env", label="environment")

    configured = (env_values.get(_ENV_FILE_KEY) or "").strip()
    if configured:
        path = Path(configured).expanduser()
        attempts.append(f"{_ENV_FILE_KEY}={path}")
        values = _read_explicit_file(path, _ENV_FILE_KEY)
        return _build_config(values), ConfigSource(kind="file", label=str(path))

    user_path = user_database_env_path(env_values)
    attempts.append(f"user config {user_path}")
    if user_path.is_file():
        values = _read_auto_file(user_path, "user config database.env")
        return _build_config(values), ConfigSource(kind="file", label=str(user_path))

    repo_path = _app_root_dotenv_path()
    if repo_path is not None:
        attempts.append(f"repo {repo_path}")
        if repo_path.is_file():
            values = _read_auto_file(repo_path, "repo .env")
            return _build_config(values), ConfigSource(
                kind="file", label=str(repo_path)
            )

    raise DatabaseConfigError(_no_source_error(attempts))


def describe_database_config_source(
    *,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> ConfigSource:
    """Return where the active DATABASE_* group would come from.

    Raises ``DatabaseConfigError`` if no source resolves.
    """

    _, source = resolve_database_config(env=env, env_file=env_file)
    return source


def would_fall_back_to(
    *,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> str | None:
    """Return the next file source path after stripping shell DATABASE_*.

    Used by ``magipi config path`` to surface the file we'd consult if the
    shell environment were not present.
    """

    env_values = env if env is not None else os.environ
    if env_file is not None:
        return None
    stripped = {
        k: v
        for k, v in env_values.items()
        if k not in _REQUIRED_KEYS and k != _SCHEMA_KEY
    }
    configured = (stripped.get(_ENV_FILE_KEY) or "").strip()
    if configured:
        path = Path(configured).expanduser()
        suffix = "" if path.is_file() else " (missing)"
        return f"{_ENV_FILE_KEY}={path}{suffix}"
    user_path = user_database_env_path(stripped)
    if user_path.is_file():
        return f"user config {user_path}"
    repo_path = _app_root_dotenv_path()
    if repo_path is not None and repo_path.is_file():
        return f"repo {repo_path}"
    return f"user config {user_path} (missing)"


def read_env_template() -> str:
    """Return the bundled ``database.env`` template shipped as a package resource.

    Reads via :mod:`importlib.resources` so the template is available in
    wheel installs without requiring access to the source repo.
    """

    return (
        resources.files("storage.templates")
        .joinpath("database.env.template")
        .read_text(encoding="utf-8")
    )


def user_database_env_path(env_values: Mapping[str, str]) -> Path:
    """Resolve the user config ``secrets/database.env`` per ADR-0019/0020.

    Order: ``XDG_CONFIG_HOME`` (any platform) → Windows ``APPDATA`` →
    ``~/.config/neomagi/secrets/database.env``.
    """

    xdg = (env_values.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / _USER_CONFIG_SUBDIR / "secrets" / "database.env"
    if sys.platform == "win32":
        appdata = (env_values.get("APPDATA") or "").strip()
        if appdata:
            return Path(appdata) / _USER_CONFIG_SUBDIR / "secrets" / "database.env"
    return Path.home() / ".config" / _USER_CONFIG_SUBDIR / "secrets" / "database.env"


def _user_config_dotenv_path(env_values: Mapping[str, str]) -> Path:
    """Backward-compatible alias for tests and older internal callers."""

    return user_database_env_path(env_values)


def _app_root_dotenv_path() -> Path | None:
    """Return repo ``.env`` only when invoked from a NeoMAGI checkout.

    Wheel / non-editable installs have no marker; they get ``None`` and the
    caller skips this source (per ADR-0019).
    """

    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if (
            (parent / _REPO_MARKER_FILE).is_file()
            and (parent / _REPO_MARKER_DIR).is_dir()
        ):
            return parent / ".env"
    return None


def _collect_shell_values(env_values: Mapping[str, str]) -> dict[str, str] | None:
    """Return a complete shell DATABASE_* group, or ``None`` if none present.

    Raises ``DatabaseConfigError`` when *some* required keys are exported but
    not all five (per ADR-0019: any required key triggers the all-or-nothing
    rule).
    """

    present = [k for k in _REQUIRED_KEYS if (env_values.get(k) or "").strip()]
    if not present:
        return None
    if len(present) < len(_REQUIRED_KEYS):
        missing = [k for k in _REQUIRED_KEYS if not (env_values.get(k) or "").strip()]
        raise DatabaseConfigError(
            "shell environment has incomplete DATABASE_* configuration; "
            f"present: {', '.join(present)}; missing: {', '.join(missing)}. "
            "Either export all of "
            f"{', '.join(_REQUIRED_KEYS)} together, or unset the partial set "
            "to fall back to a configured .env file."
        )
    values = {k: str(env_values[k]).strip() for k in _REQUIRED_KEYS}
    schema = (env_values.get(_SCHEMA_KEY) or "").strip()
    if schema:
        values[_SCHEMA_KEY] = schema
    return values


def _read_explicit_file(path: Path, label: str) -> dict[str, str]:
    if not path.is_file():
        raise DatabaseConfigError(
            f"{label} points to a missing file: {path}. "
            f"Create the file or update the path. {_REMEDIATION_HINT}"
        )
    values = _read_dotenv(path)
    missing = _missing_required_keys(values)
    if missing:
        raise DatabaseConfigError(
            f"{label} ({path}) is missing required keys: {', '.join(missing)}. "
            f"Fill in the values from `database.env.template`. {_REMEDIATION_HINT}"
        )
    return values


def _read_auto_file(path: Path, label: str) -> dict[str, str]:
    values = _read_dotenv(path)
    missing = _missing_required_keys(values)
    if missing:
        raise DatabaseConfigError(
            f"{label} ({path}) is missing required keys: {', '.join(missing)}. "
            f"Edit the file to add them or remove it to fall back to other "
            f"sources. {_REMEDIATION_HINT}"
        )
    return values


def _missing_required_keys(values: Mapping[str, str]) -> list[str]:
    return [k for k in _REQUIRED_KEYS if not (values.get(k) or "").strip()]


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _build_config(values: Mapping[str, str]) -> DatabaseConfig:
    try:
        port = int(str(values["DATABASE_PORT"]))
    except ValueError as exc:
        raise DatabaseConfigError("DATABASE_PORT must be an integer") from exc
    if port <= 0 or port > 65535:
        raise DatabaseConfigError("DATABASE_PORT must be between 1 and 65535")

    schema = (values.get(_SCHEMA_KEY) or "neomagi").strip() or "neomagi"
    if not _is_identifier(schema):
        raise DatabaseConfigError(
            "DATABASE_SCHEMA must be a simple PostgreSQL identifier "
            "(letters, numbers, and underscores; cannot start with a number)"
        )

    return DatabaseConfig(
        host=str(values["DATABASE_HOST"]),
        port=port,
        user=str(values["DATABASE_USER"]),
        password=str(values["DATABASE_PASSWORD"]),
        database=str(values["DATABASE_NAME"]),
        schema=schema,
    )


def _no_source_error(attempts: list[str]) -> str:
    listed = "\n  - ".join(attempts) if attempts else "(no sources tried)"
    return (
        "missing database configuration; tried sources in order:\n  - "
        f"{listed}\n"
        "Provide one of: --env-file <path>, all of "
        f"{', '.join(_REQUIRED_KEYS)} in the shell, "
        f"{_ENV_FILE_KEY}, or a complete user config secrets/database.env. "
        f"{_REMEDIATION_HINT}"
    )


def _is_identifier(value: str | None) -> bool:
    if not value:
        return False
    first = value[0]
    if not (first.isalpha() or first == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in value)


__all__ = [
    "ConfigSource",
    "DatabaseConfig",
    "DatabaseConfigError",
    "describe_database_config_source",
    "load_database_config",
    "read_env_template",
    "resolve_database_config",
    "user_database_env_path",
    "would_fall_back_to",
]

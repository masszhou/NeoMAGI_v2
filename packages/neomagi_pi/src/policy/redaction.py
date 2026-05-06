"""Shared redaction helpers for audit records and local exports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REDACTED_VALUE = "[redacted]"
REDACTED_PATH = "[redacted-path]"

SECRET_KEY_RULE_ID = "secret_like_key"
SECRET_VALUE_RULE_ID = "secret_like_value"
SENSITIVE_PATH_RULE_ID = "sensitive_path"
SENSITIVE_CONTENT_RULE_ID = "sensitive_path_content"
OUTSIDE_CWD_PATH_RULE_ID = "outside_cwd_path"

_SECRET_KEY_RE = re.compile(
    r"token|secret|password|api[_-]?key|authorization|cookie|access[_-]?token|"
    r"refresh[_-]?token|id[_-]?token",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._/+\-]{12,}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}|"
    r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)"
)
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_/+\-]{24,}")
_ENV_REF_RE = re.compile(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)")
_BASH_PREVIEW_LIMIT = 512

_SAFE_REFERENCE_SUFFIXES = ("env", "header")
_SECRET_EXACT_KEYS = {
    "access",
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "id_token",
    "idtoken",
    "key",
    "password",
    "refresh",
    "refresh_token",
    "refreshtoken",
    "secret",
    "token",
}
_PATH_KEYS = {
    "path",
    "filepath",
    "filename",
    "resolvedpath",
    "fulloutputpath",
    "outputpath",
}
_CONTENT_KEYS = {"content", "text", "output", "value", "data"}
_SENSITIVE_PATH_MARKERS = (
    ".env",
    "/.env",
    "\\.env",
    "auth.json",
    "credentials.json",
    "credential.json",
    ".netrc",
    "id_rsa",
    "id_ed25519",
)


@dataclass(slots=True)
class RedactionReport:
    counts: dict[str, int] = field(default_factory=dict)
    paths: dict[str, list[str]] = field(default_factory=dict)

    def record(self, rule_id: str, path: tuple[str, ...]) -> None:
        self.counts[rule_id] = self.counts.get(rule_id, 0) + 1
        values = self.paths.setdefault(rule_id, [])
        rendered = ".".join(path) if path else "$"
        if rendered not in values and len(values) < 20:
            values.append(rendered)

    @property
    def status(self) -> str:
        return "applied" if self.counts else "not_required"


def redact_secret_keys(value: Any) -> tuple[Any, bool]:
    """Redact recursively by secret-like key name.

    This is intentionally narrow for audit argument summaries: it preserves
    non-secret values and only masks fields whose key is itself sensitive.
    """

    report = RedactionReport()
    redacted = _redact_for_export(
        value,
        report,
        path=(),
        cwd=None,
        key_only=True,
        sensitive_context=False,
    )
    return redacted, bool(report.counts)


def redact_for_export(
    value: Any,
    *,
    cwd: str | Path | None = None,
    report: RedactionReport | None = None,
) -> tuple[Any, RedactionReport]:
    active_report = report or RedactionReport()
    redacted = _redact_for_export(
        value,
        active_report,
        path=(),
        cwd=Path(cwd).resolve(strict=False) if cwd is not None else None,
        key_only=False,
        sensitive_context=False,
    )
    return redacted, active_report


def redacted_command_preview(command: str) -> tuple[str, bool]:
    env_refs: list[str] = []

    def protect_env_ref(match: re.Match[str]) -> str:
        env_refs.append(match.group(0))
        return f"\x00ENV{len(env_refs) - 1}\x00"

    protected = _ENV_REF_RE.sub(protect_env_ref, command)
    applied = False

    def redact_long_token(match: re.Match[str]) -> str:
        nonlocal applied
        applied = True
        return f"<redacted:{len(match.group(0))}>"

    redacted = _LONG_TOKEN_RE.sub(redact_long_token, protected)
    for index, value in enumerate(env_refs):
        redacted = redacted.replace(f"\x00ENV{index}\x00", value)
    if len(redacted) > _BASH_PREVIEW_LIMIT:
        applied = True
        redacted = redacted[:_BASH_PREVIEW_LIMIT]
    return redacted, applied


def _redact_for_export(
    value: Any,
    report: RedactionReport,
    *,
    path: tuple[str, ...],
    cwd: Path | None,
    key_only: bool,
    sensitive_context: bool,
) -> Any:
    if isinstance(value, dict):
        dict_sensitive = _dict_has_sensitive_path(value, cwd)
        result: dict[str, Any] = {}
        for key, item in value.items():
            item_path = (*path, str(key))
            if _is_secret_key(str(key)):
                report.record(SECRET_KEY_RULE_ID, item_path)
                result[key] = REDACTED_VALUE
                continue
            if not key_only and _is_path_key(str(key)) and isinstance(item, str):
                redacted_path = _redact_path_if_needed(item, report, item_path, cwd)
                result[key] = redacted_path
                continue
            child_sensitive = (sensitive_context and _is_content_key(str(key))) or (
                not key_only and dict_sensitive and _is_content_key(str(key))
            )
            result[key] = _redact_for_export(
                item,
                report,
                path=item_path,
                cwd=cwd,
                key_only=key_only,
                sensitive_context=child_sensitive,
            )
        return result
    if isinstance(value, list):
        return [
            _redact_for_export(
                item,
                report,
                path=(*path, str(index)),
                cwd=cwd,
                key_only=key_only,
                sensitive_context=sensitive_context,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, str) and not key_only:
        if sensitive_context:
            report.record(SENSITIVE_CONTENT_RULE_ID, path)
            return REDACTED_VALUE
        if _SECRET_VALUE_RE.search(value):
            report.record(SECRET_VALUE_RULE_ID, path)
            return _SECRET_VALUE_RE.sub(REDACTED_VALUE, value)
    return value


def _is_secret_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized.endswith(_SAFE_REFERENCE_SUFFIXES):
        return False
    if normalized.startswith("tokens") or normalized.endswith("tokens"):
        return False
    if normalized in _SECRET_EXACT_KEYS:
        return True
    return bool(_SECRET_KEY_RE.search(key))


def _is_path_key(key: str) -> bool:
    return _normalize_key(key) in _PATH_KEYS


def _is_content_key(key: str) -> bool:
    return _normalize_key(key) in _CONTENT_KEYS


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _dict_has_sensitive_path(value: dict[str, Any], cwd: Path | None) -> bool:
    for key, item in value.items():
        if _is_path_key(str(key)) and isinstance(item, str):
            if _is_sensitive_path(item) or _is_outside_cwd(item, cwd):
                return True
        if isinstance(item, dict) and _dict_has_sensitive_path(item, cwd):
            return True
    return False


def _redact_path_if_needed(
    value: str,
    report: RedactionReport,
    path: tuple[str, ...],
    cwd: Path | None,
) -> str:
    normalized_key = _normalize_key(path[-1]) if path else ""
    if normalized_key == "fulloutputpath":
        report.record(OUTSIDE_CWD_PATH_RULE_ID, path)
        return REDACTED_PATH
    if _is_sensitive_path(value):
        report.record(SENSITIVE_PATH_RULE_ID, path)
        return REDACTED_PATH
    if _is_outside_cwd(value, cwd):
        report.record(OUTSIDE_CWD_PATH_RULE_ID, path)
        return REDACTED_PATH
    return value


def _is_sensitive_path(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SENSITIVE_PATH_MARKERS)


def _is_outside_cwd(value: str, cwd: Path | None) -> bool:
    if cwd is None:
        return False
    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            return False
        candidate.resolve(strict=False).relative_to(cwd)
    except (OSError, ValueError):
        return True
    return False


__all__ = [
    "OUTSIDE_CWD_PATH_RULE_ID",
    "REDACTED_PATH",
    "REDACTED_VALUE",
    "RedactionReport",
    "SECRET_KEY_RULE_ID",
    "SECRET_VALUE_RULE_ID",
    "SENSITIVE_CONTENT_RULE_ID",
    "SENSITIVE_PATH_RULE_ID",
    "redact_for_export",
    "redact_secret_keys",
    "redacted_command_preview",
]

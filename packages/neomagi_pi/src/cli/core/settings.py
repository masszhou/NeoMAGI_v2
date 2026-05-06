"""Product settings schema and manager for CLI/runtime control-plane state."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai_provider.types import CacheRetention, ThinkingLevel

SettingsScope = Literal["global", "project"]


class SettingsDiagnostic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    severity: Literal["error", "warning"]
    message: str
    path: str | None = None
    scope: SettingsScope | None = None
    field: str | None = None


class ModelSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    provider: str | None = None
    id: str | None = None
    thinking_level: ThinkingLevel | None = Field(default=None, alias="thinkingLevel")
    cache_retention: CacheRetention | None = Field(default=None, alias="cacheRetention")
    enabled_models: list[str] = Field(default_factory=list, alias="enabledModels")

    @field_validator("enabled_models", mode="before")
    @classmethod
    def _coerce_enabled_models(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


class ResourceProductSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    packages: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    enable_skill_commands: bool = Field(default=True, alias="enableSkillCommands")

    @field_validator("packages", "extensions", "skills", "prompts", "themes", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class ModelOverride(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    name: str | None = None
    api: str | None = None
    model: str | None = None
    base_url: str | None = Field(default=None, alias="baseUrl")
    reasoning: bool | None = None
    input: list[Literal["text", "image"]] | None = None
    cost: dict[str, float] | None = None
    context_window: int | None = Field(default=None, alias="contextWindow")
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    headers: dict[str, str] | None = None
    compat: dict[str, Any] | None = None


class ProviderOverride(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    api: str | None = None
    base_url: str | None = Field(default=None, alias="baseUrl")
    headers: dict[str, str] | None = None
    auth_header: bool | None = Field(default=None, alias="authHeader")
    compat: dict[str, Any] | None = None
    models: list[ModelOverride] = Field(default_factory=list)


class ProductSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    model: ModelSettings = Field(default_factory=ModelSettings)
    providers: dict[str, ProviderOverride] = Field(default_factory=dict)
    resources: ResourceProductSettings = Field(default_factory=ResourceProductSettings)
    terminal: dict[str, Any] = Field(default_factory=dict)
    images: dict[str, Any] = Field(default_factory=dict)
    retry: dict[str, Any] = Field(default_factory=dict)
    compaction: dict[str, Any] = Field(default_factory=dict)
    branch_summary: dict[str, Any] = Field(default_factory=dict, alias="branchSummary")


@dataclass(frozen=True, slots=True)
class LoadedSettings:
    settings: ProductSettings
    diagnostics: tuple[SettingsDiagnostic, ...] = ()
    global_path: Path | None = None
    project_path: Path | None = None
    global_raw: dict[str, Any] = field(default_factory=dict)
    project_raw: dict[str, Any] = field(default_factory=dict)


class SettingsManager:
    """Load, merge, and save Pi-compatible product settings.

    M9 keeps the Pi-compatible global path (`~/.pi/agent/settings.json`, or
    `NEOMAGI_AGENT_DIR/settings.json`) as the single global product/resource
    settings path. Project settings live at `<cwd>/.pi/settings.json`.
    """

    def __init__(
        self,
        *,
        cwd: str | Path,
        agent_dir: str | Path | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> None:
        self.cwd = Path(cwd).resolve()
        self.agent_dir = Path(agent_dir).expanduser().resolve() if agent_dir is not None else _default_agent_dir()
        self.overrides = dict(overrides or {})

    @property
    def global_path(self) -> Path:
        return self.agent_dir / "settings.json"

    @property
    def project_path(self) -> Path:
        return self.cwd / ".pi" / "settings.json"

    def load(self) -> LoadedSettings:
        diagnostics: list[SettingsDiagnostic] = []
        global_raw = _normalize_legacy_resource_keys(
            _read_json_object(self.global_path, diagnostics, scope="global")
        )
        project_raw = _normalize_legacy_resource_keys(
            _read_json_object(self.project_path, diagnostics, scope="project")
        )
        sanitized_project = _strip_project_secrets(project_raw, diagnostics, self.project_path)
        merged = _deep_merge(_deep_merge(_defaults(), global_raw), sanitized_project)
        if self.overrides:
            merged = _deep_merge(merged, dict(self.overrides))
        try:
            settings = ProductSettings.model_validate(merged)
        except ValidationError as exc:
            diagnostics.append(
                SettingsDiagnostic(
                    severity="error",
                    message=f"invalid product settings: {exc}",
                    path=str(self.project_path),
                )
            )
            settings = ProductSettings()
        return LoadedSettings(
            settings=settings,
            diagnostics=tuple(diagnostics),
            global_path=self.global_path,
            project_path=self.project_path,
            global_raw=global_raw,
            project_raw=project_raw,
        )

    def update_value(self, scope: SettingsScope, dotted_path: str, value: Any) -> LoadedSettings:
        path = self._path_for_scope(scope)
        raw = _read_json_object(path, [], scope=scope)
        _set_dotted(raw, dotted_path, value)
        _write_json_object(path, raw)
        return self.load()

    def update_settings(
        self,
        scope: SettingsScope,
        mutator: Callable[[dict[str, Any]], None],
    ) -> LoadedSettings:
        path = self._path_for_scope(scope)
        raw = _read_json_object(path, [], scope=scope)
        mutator(raw)
        _write_json_object(path, raw)
        return self.load()

    def _path_for_scope(self, scope: SettingsScope) -> Path:
        if scope == "global":
            return self.global_path
        if scope == "project":
            return self.project_path
        raise ValueError(f"unknown settings scope: {scope}")


def _defaults() -> dict[str, Any]:
    return ProductSettings().model_dump(by_alias=True, exclude_none=True)


def _default_agent_dir() -> Path:
    override = os.environ.get("NEOMAGI_AGENT_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".pi" / "agent").resolve()


def _read_json_object(
    path: Path,
    diagnostics: list[SettingsDiagnostic],
    *,
    scope: SettingsScope,
) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        diagnostics.append(
            SettingsDiagnostic(
                severity="error",
                message=f"failed to read settings: {exc}",
                path=str(path),
                scope=scope,
            )
        )
        return {}
    if not isinstance(data, dict):
        diagnostics.append(
            SettingsDiagnostic(
                severity="error",
                message="settings root must be a JSON object",
                path=str(path),
                scope=scope,
            )
        )
        return {}
    return dict(data)


def _write_json_object(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


_RESOURCE_KEYS = {
    "packages",
    "extensions",
    "skills",
    "prompts",
    "themes",
    "enableSkillCommands",
    "enable_skill_commands",
}


def _normalize_legacy_resource_keys(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(raw)
    resources = dict(normalized.get("resources") or {})
    changed = False
    for key in _RESOURCE_KEYS:
        if key in normalized:
            target_key = "enableSkillCommands" if key == "enable_skill_commands" else key
            resources.setdefault(target_key, normalized[key])
            changed = True
    if changed:
        normalized["resources"] = resources
    return normalized


def _deep_merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_SECRET_EXACT_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "id_token",
    "password",
    "refresh",
    "refresh_token",
    "token",
    "secret",
}

_SECRET_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_apikey",
    "_authorization",
    "_cookie",
    "_id_token",
    "_password",
    "_refresh_token",
    "_secret",
    "_token",
)

_NON_SECRET_REFERENCE_KEYS = {
    "api_key_header",
    "apikey_header",
    "auth_header",
}

_SECRET_POLICY_CONTAINER_KEYS = {"refresh"}


def _strip_project_secrets(
    value: Any,
    diagnostics: list[SettingsDiagnostic],
    path: Path,
    *,
    dotted: str = "",
) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            child_path = f"{dotted}.{key}" if dotted else str(key)
            if _should_strip_project_secret_field(str(key), child):
                diagnostics.append(
                    SettingsDiagnostic(
                        severity="warning",
                        message="project settings secret-like field ignored; use auth storage or environment variables",
                        path=str(path),
                        scope="project",
                        field=child_path,
                    )
                )
                continue
            cleaned[key] = _strip_project_secrets(
                child,
                diagnostics,
                path,
                dotted=child_path,
            )
        return cleaned
    if isinstance(value, list):
        return [
            _strip_project_secrets(item, diagnostics, path, dotted=dotted)
            for item in value
        ]
    return value


def _should_strip_project_secret_field(key: str, value: Any) -> bool:
    normalized = _normalize_secret_key(key)
    if _is_non_secret_reference_key(normalized):
        return False
    if normalized in _SECRET_POLICY_CONTAINER_KEYS and isinstance(value, dict | list):
        return False
    return _looks_secret_key(normalized)


def _normalize_secret_key(key: str) -> str:
    return _CAMEL_BOUNDARY_RE.sub("_", key.replace("-", "_")).lower()


def _is_non_secret_reference_key(normalized: str) -> bool:
    return normalized in _NON_SECRET_REFERENCE_KEYS or normalized.endswith("_env")


def _looks_secret_key(key: str) -> bool:
    return key in _SECRET_EXACT_KEYS or key.endswith(_SECRET_SUFFIXES)


def _set_dotted(raw: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = [part for part in dotted_path.split(".") if part]
    if not parts:
        raise ValueError("settings path cannot be empty")
    cursor = raw
    for part in parts[:-1]:
        current = cursor.get(part)
        if not isinstance(current, dict):
            current = {}
            cursor[part] = current
        cursor = current
    cursor[parts[-1]] = value


__all__ = [
    "LoadedSettings",
    "ModelOverride",
    "ModelSettings",
    "ProductSettings",
    "ProviderOverride",
    "ResourceProductSettings",
    "SettingsDiagnostic",
    "SettingsManager",
    "SettingsScope",
]

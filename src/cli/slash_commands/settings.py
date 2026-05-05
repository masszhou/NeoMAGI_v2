"""M9 settings slash command."""

from __future__ import annotations

from typing import Any

from cli.core.settings import SettingsScope

from .registry import SlashCommandContext

_SETTING_ALIASES = {
    "model.provider": "model.provider",
    "model.id": "model.id",
    "model.thinking": "model.thinkingLevel",
    "model.thinkinglevel": "model.thinkingLevel",
    "model.thinkingLevel": "model.thinkingLevel",
    "model.cache": "model.cacheRetention",
    "model.cacheRetention": "model.cacheRetention",
    "resources.enableSkillCommands": "resources.enableSkillCommands",
}

_THINKING_LEVELS: set[str] = {"off", "minimal", "low", "medium", "high", "xhigh"}
_CACHE_RETENTIONS: set[str] = {"default", "none", "short", "long"}


def handle_settings(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.status.push_notification("/settings requires an interactive runtime", level="warn")
        return
    if not ctx.args:
        ctx.controller.push_session_message(_settings_summary(runtime.settings_snapshot()))
        return
    if ctx.args[0] != "set":
        ctx.controller.push_session_message(_settings_usage(), level="warn")
        return
    try:
        path, value, scope = _parse_set_args(ctx.args[1:])
        loaded = runtime.settings_manager.update_value(scope, path, value)
        runtime.reload_resources()
        ctx.controller.editor.set_footer(runtime.footer_summary)
        ctx.controller.status.push_notification(
            f"updated {scope} settings: {path}",
            level="info",
        )
        if loaded.diagnostics:
            ctx.controller.push_session_message(_diagnostic_lines(loaded.diagnostics), level="warn")
    except Exception as exc:
        ctx.controller.status.push_notification(str(exc), level="error", ttl_seconds=8.0)


def _parse_set_args(args: list[str]) -> tuple[str, Any, SettingsScope]:
    if len(args) < 2:
        raise ValueError(_settings_usage())
    scope: SettingsScope = "project"
    filtered: list[str] = []
    for arg in args:
        if arg == "--global":
            scope = "global"
        elif arg == "--project":
            scope = "project"
        else:
            filtered.append(arg)
    if len(filtered) < 2:
        raise ValueError(_settings_usage())
    path = _SETTING_ALIASES.get(filtered[0], filtered[0])
    raw_value = " ".join(filtered[1:])
    return path, _coerce_value(path, raw_value), scope


def _coerce_value(path: str, value: str) -> Any:
    if path == "model.thinkingLevel":
        if value not in _THINKING_LEVELS:
            raise ValueError(f"invalid thinking level: {value}")
        return value  # type: ignore[return-value]
    if path == "model.cacheRetention":
        if value not in _CACHE_RETENTIONS:
            raise ValueError(f"invalid cache retention: {value}")
        return None if value == "default" else value  # type: ignore[return-value]
    if path == "model.enabledModels":
        return [item.strip() for item in value.split(",") if item.strip()]
    if path == "resources.enableSkillCommands":
        normalized = value.lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise ValueError("resources.enableSkillCommands expects true/false")
    return value


def _settings_summary(loaded) -> str:
    settings = loaded.settings
    model = settings.model
    cache = model.cache_retention or "default"
    current = "/".join(part for part in (model.provider, model.id) if part) or "(runtime)"
    lines = [
        "settings:",
        f"- global: {loaded.global_path}",
        f"- project: {loaded.project_path}",
        f"- default model: {current}",
        f"- thinking: {model.thinking_level or 'off'}",
        f"- cache retention: {cache}",
        f"- scoped models: {', '.join(model.enabled_models) if model.enabled_models else '(all)'}",
        f"- skill commands: {settings.resources.enable_skill_commands}",
        f"- custom providers: {', '.join(settings.providers) if settings.providers else '(none)'}",
    ]
    if loaded.diagnostics:
        lines.append(_diagnostic_lines(loaded.diagnostics))
    return "\n".join(lines)


def _diagnostic_lines(diagnostics) -> str:
    return "\n".join(
        f"- {diagnostic.severity}: {diagnostic.message}"
        + (f" ({diagnostic.field})" if diagnostic.field else "")
        for diagnostic in diagnostics
    )


def _settings_usage() -> str:
    return (
        "usage: /settings [set <path> <value> [--project|--global]]\n"
        "paths: model.provider, model.id, model.thinkingLevel, "
        "model.cacheRetention, resources.enableSkillCommands"
    )


__all__ = ["handle_settings"]

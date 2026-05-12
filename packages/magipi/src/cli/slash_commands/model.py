"""M9 model and scoped-model slash commands."""

from __future__ import annotations

from typing import cast

from ai_provider.auth_storage import credential_status
from ai_provider.credentials import get_env_api_key
from ai_provider.model_registry import (
    canonical_model_ref,
    list_model_entries,
    resolve_model,
)
from ai_provider.prompt_cache import resolve_cache_retention
from ai_provider.types import CacheRetention

from .registry import SlashCommandContext

_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "xhigh"}
_CACHE_RETENTIONS = {"default", "none", "short", "long"}


def handle_model(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.status.push_notification("/model requires an interactive runtime", level="warn")
        return
    if not ctx.args or ctx.args[0] in {"list", "--list"}:
        ctx.controller.push_session_message(_model_list(runtime.state.model_ref))
        return
    try:
        if ctx.args[0] in {"--thinking", "thinking"}:
            _set_thinking(ctx, ctx.args[1:])
            return
        if ctx.args[0] in {"--cache", "cache"}:
            _set_cache(ctx, ctx.args[1:])
            return
        model = resolve_model(ctx.args[0])
        canonical = canonical_model_ref(model)
        runtime.set_model_ref(canonical)
        ctx.controller.editor.set_footer(runtime.footer_summary)
        ctx.controller.status.push_notification(
            f"model set: {canonical}",
            level="info",
        )
    except Exception as exc:
        ctx.controller.status.push_notification(str(exc), level="error", ttl_seconds=8.0)


def handle_scoped_models(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.status.push_notification("/scoped-models requires an interactive runtime", level="warn")
        return
    if not ctx.args or ctx.args[0] in {"list", "--list"}:
        enabled = runtime.settings_snapshot().settings.model.enabled_models
        ctx.controller.push_session_message(
            "scoped models: " + (", ".join(enabled) if enabled else "(all models)")
        )
        return
    refs = [
        item.strip()
        for raw in ctx.args
        for item in raw.split(",")
        if item.strip()
    ]
    try:
        canonical_refs = [canonical_model_ref(resolve_model(ref)) for ref in refs]
        runtime.settings_manager.update_value(
            "project",
            "model.enabledModels",
            canonical_refs,
        )
        ctx.controller.status.push_notification(
            f"scoped models updated: {len(canonical_refs)}",
            level="info",
        )
    except Exception as exc:
        ctx.controller.status.push_notification(str(exc), level="error", ttl_seconds=8.0)


def _set_thinking(ctx: SlashCommandContext, args: list[str]) -> None:
    if not args:
        raise ValueError("usage: /model --thinking <off|minimal|low|medium|high|xhigh>")
    level = args[0]
    if level not in _THINKING_LEVELS:
        raise ValueError(f"invalid thinking level: {level}")
    runtime = ctx.controller.runtime
    assert runtime is not None
    runtime.set_thinking_level(level)  # type: ignore[arg-type]
    ctx.controller.editor.set_footer(runtime.footer_summary)
    ctx.controller.status.push_notification(f"thinking level set: {level}", level="info")


def _set_cache(ctx: SlashCommandContext, args: list[str]) -> None:
    if not args:
        raise ValueError("usage: /model --cache <default|none|short|long>")
    retention = args[0]
    if retention not in _CACHE_RETENTIONS:
        raise ValueError(f"invalid cache retention: {retention}")
    runtime = ctx.controller.runtime
    assert runtime is not None
    next_retention: CacheRetention | None = (
        None if retention == "default" else cast(CacheRetention, retention)
    )
    runtime.set_cache_retention(next_retention)
    effective_retention = resolve_cache_retention(next_retention)
    ctx.controller.editor.set_footer(runtime.footer_summary)
    ctx.controller.status.push_notification(
        f"cache retention set: {effective_retention}",
        level="info",
    )


def _model_list(current_ref: str) -> str:
    lines = ["models:"]
    for entry in list_model_entries():
        model = entry.model
        ref = canonical_model_ref(model)
        marker = "*" if ref == current_ref else "-"
        auth = _auth_status(model.provider)
        owner = f":{entry.owner}" if entry.owner else ""
        lines.append(
            f"{marker} {ref}  api={model.api} source={entry.source}{owner} auth={auth}"
        )
    return "\n".join(lines)


def _auth_status(provider: str) -> str:
    if provider == "faux":
        return "not-required"
    try:
        if credential_status(provider) is not None:
            return "stored"
    except Exception:
        return "auth-error"
    if get_env_api_key(provider):
        return "env"
    return "missing"


__all__ = ["handle_model", "handle_scoped_models"]

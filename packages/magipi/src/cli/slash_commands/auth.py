"""M9 auth slash commands."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ai_provider.auth_storage import (
    OPENAI_CODEX_PROVIDER,
    auth_storage_status,
    delete_credential,
    list_credentials,
    resolve_auth_path,
    save_api_key,
    save_oauth_credentials,
)
from ai_provider.oauth import (
    OAuthCallbackServer,
    exchange_openai_authorization_code,
    parse_and_validate_authorization_input,
    start_openai_oauth_callback_server,
    start_openai_oauth_login,
)

from .registry import SlashCommandContext


@dataclass(slots=True)
class _PendingOAuth:
    verifier: str
    state: str
    server: OAuthCallbackServer


_PENDING_OPENAI_CODEX: _PendingOAuth | None = None
_LAST_OPENAI_CODEX_CALLBACK_ERROR: str | None = None
_LAST_OPENAI_CODEX_SAVE_PATH: str | None = None
_OAuthNotify = Callable[[str, str], None]


def handle_login(ctx: SlashCommandContext) -> None:
    if not ctx.args:
        ctx.controller.push_session_message(_auth_status_lines())
        return
    try:
        if ctx.args[0] == "--api-key":
            _save_api_key_args(ctx.args[1:])
            ctx.controller.status.push_notification("API key saved", level="info")
            return
        provider = ctx.args[0]
        if "--api-key" in ctx.args:
            index = ctx.args.index("--api-key")
            key = " ".join(ctx.args[index + 1 :]).strip()
            if not key:
                raise ValueError("usage: /login <provider> --api-key <key>")
            save_api_key(provider, key)
            ctx.controller.status.push_notification(f"API key saved: {provider}", level="info")
            return
        if provider != OPENAI_CODEX_PROVIDER:
            raise ValueError("M9 OAuth supports only /login openai-codex")
        if len(ctx.args) > 1:
            _finish_openai_codex_login(" ".join(ctx.args[1:]))
            ctx.controller.status.push_notification("OpenAI Codex OAuth credential saved", level="info")
            return
        message = _start_openai_codex_login(_controller_notifier(ctx.controller))
        ctx.controller.push_session_message(message)
    except Exception as exc:
        ctx.controller.status.push_notification(str(exc), level="error", ttl_seconds=8.0)


def handle_logout(ctx: SlashCommandContext) -> None:
    provider = ctx.args[0] if ctx.args else OPENAI_CODEX_PROVIDER
    try:
        deleted = delete_credential(provider)
        level = "info" if deleted else "warn"
        suffix = "deleted" if deleted else "not found"
        ctx.controller.status.push_notification(f"credential {suffix}: {provider}", level=level)
    except Exception as exc:
        ctx.controller.status.push_notification(str(exc), level="error", ttl_seconds=8.0)


def _save_api_key_args(args: list[str]) -> None:
    if len(args) < 2:
        raise ValueError("usage: /login --api-key <provider> <key>")
    provider = args[0]
    key = " ".join(args[1:]).strip()
    if not key:
        raise ValueError("API key cannot be empty")
    save_api_key(provider, key)


def _start_openai_codex_login(notify: _OAuthNotify | None = None) -> str:
    global _LAST_OPENAI_CODEX_CALLBACK_ERROR, _PENDING_OPENAI_CODEX
    if _PENDING_OPENAI_CODEX is not None:
        _PENDING_OPENAI_CODEX.server.close()
    _LAST_OPENAI_CODEX_CALLBACK_ERROR = None
    start = start_openai_oauth_login()
    server = start_openai_oauth_callback_server(start.state)
    pending = _PendingOAuth(verifier=start.verifier, state=start.state, server=server)
    _PENDING_OPENAI_CODEX = pending
    thread = threading.Thread(
        target=_wait_for_callback,
        args=(pending, notify),
        name="neomagi-openai-codex-oauth",
        daemon=True,
    )
    thread.start()
    return (
        "OpenAI Codex login started.\n"
        f"Open this URL:\n{start.authorization_url}\n\n"
        "If browser callback succeeds, credentials are saved automatically. "
        "Fallback: paste the full redirect URL or code with "
        "`/login openai-codex <redirect-url-or-code>`."
    )


def _finish_openai_codex_login(value: str) -> None:
    global _LAST_OPENAI_CODEX_CALLBACK_ERROR, _LAST_OPENAI_CODEX_SAVE_PATH, _PENDING_OPENAI_CODEX
    pending = _PENDING_OPENAI_CODEX
    if pending is None:
        raise RuntimeError("no pending OpenAI Codex login; run /login openai-codex first")
    parsed = parse_and_validate_authorization_input(value, pending.state)
    if not parsed.code:
        raise RuntimeError("missing authorization code")
    credentials = asyncio.run(
        exchange_openai_authorization_code(parsed.code, pending.verifier)
    )
    save_oauth_credentials(OPENAI_CODEX_PROVIDER, credentials)
    _LAST_OPENAI_CODEX_CALLBACK_ERROR = None
    _LAST_OPENAI_CODEX_SAVE_PATH = _backend_summary()
    pending.server.close()
    _PENDING_OPENAI_CODEX = None


def _wait_for_callback(pending: _PendingOAuth, notify: _OAuthNotify | None = None) -> None:
    global _LAST_OPENAI_CODEX_CALLBACK_ERROR, _LAST_OPENAI_CODEX_SAVE_PATH, _PENDING_OPENAI_CODEX
    try:
        code = asyncio.run(pending.server.wait_for_code())
        if not code:
            _LAST_OPENAI_CODEX_CALLBACK_ERROR = (
                "OpenAI Codex OAuth callback did not return an authorization code; "
                "paste the redirect URL with `/login openai-codex <redirect-url-or-code>`"
            )
            _notify(notify, _LAST_OPENAI_CODEX_CALLBACK_ERROR, "warn")
            return
        credentials = asyncio.run(
            exchange_openai_authorization_code(code, pending.verifier)
        )
        save_oauth_credentials(OPENAI_CODEX_PROVIDER, credentials)
        _LAST_OPENAI_CODEX_CALLBACK_ERROR = None
        _LAST_OPENAI_CODEX_SAVE_PATH = _backend_summary()
        _notify(
            notify,
            f"OpenAI Codex OAuth credential saved: {_LAST_OPENAI_CODEX_SAVE_PATH}",
            "info",
        )
    except Exception as exc:
        _LAST_OPENAI_CODEX_CALLBACK_ERROR = (
            f"OpenAI Codex OAuth callback failed after authorization: {exc}"
        )
        _notify(notify, _LAST_OPENAI_CODEX_CALLBACK_ERROR, "error")
        return
    finally:
        pending.server.close()
        if _PENDING_OPENAI_CODEX is pending:
            _PENDING_OPENAI_CODEX = None


def _auth_status_lines() -> str:
    status = auth_storage_status()
    credentials = list_credentials()
    diagnostics = [f"backend: {_backend_summary(status)}"]
    if status.get("backend") == "file":
        diagnostics.append("file fallback: 0600 JSON; use OS keyring when available")
    if _LAST_OPENAI_CODEX_CALLBACK_ERROR:
        diagnostics.append(f"last openai-codex oauth error: {_LAST_OPENAI_CODEX_CALLBACK_ERROR}")
    elif _LAST_OPENAI_CODEX_SAVE_PATH:
        diagnostics.append(f"last openai-codex oauth save: {_LAST_OPENAI_CODEX_SAVE_PATH}")
    if not credentials:
        return "\n".join(["auth: no stored credentials", *diagnostics])
    lines = ["auth:"]
    for provider, entry in sorted(credentials.items()):
        lines.append(f"- {provider}: {_entry_summary(entry)}")
    lines.extend(diagnostics)
    return "\n".join(lines)


def _backend_summary(status: dict[str, Any] | None = None) -> str:
    status = status or auth_storage_status()
    if status.get("backend") == "keyring":
        return f"keyring:{status.get('service')}/{status.get('username')}"
    path = status.get("path") or str(resolve_auth_path())
    return f"file:{path}"


def _controller_notifier(controller: Any) -> _OAuthNotify:
    def notify(message: str, level: str) -> None:
        controller.status.push_notification(message, level=level, ttl_seconds=8.0)

    return notify


def _notify(notify: _OAuthNotify | None, message: str, level: str) -> None:
    if notify is None:
        return
    notify(message, level)


def _entry_summary(entry: dict[str, Any]) -> str:
    if entry.get("type") == "api_key":
        return f"api_key key={entry.get('key')}"
    if entry.get("type") == "oauth":
        account = f" account={entry.get('accountId')}" if entry.get("accountId") else ""
        return f"oauth access={entry.get('access')} expires={entry.get('expires')}{account}"
    return str(entry)


__all__ = ["handle_login", "handle_logout"]

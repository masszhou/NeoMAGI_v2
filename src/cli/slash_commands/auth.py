"""M9 auth slash commands."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from ai_provider.auth_storage import (
    OPENAI_CODEX_PROVIDER,
    delete_credential,
    list_credentials,
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
        message = _start_openai_codex_login()
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


def _start_openai_codex_login() -> str:
    global _PENDING_OPENAI_CODEX
    if _PENDING_OPENAI_CODEX is not None:
        _PENDING_OPENAI_CODEX.server.close()
    start = start_openai_oauth_login()
    server = start_openai_oauth_callback_server(start.state)
    pending = _PendingOAuth(verifier=start.verifier, state=start.state, server=server)
    _PENDING_OPENAI_CODEX = pending
    thread = threading.Thread(
        target=_wait_for_callback,
        args=(pending,),
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
    global _PENDING_OPENAI_CODEX
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
    pending.server.close()
    _PENDING_OPENAI_CODEX = None


def _wait_for_callback(pending: _PendingOAuth) -> None:
    global _PENDING_OPENAI_CODEX
    try:
        code = asyncio.run(pending.server.wait_for_code())
        if not code:
            return
        credentials = asyncio.run(
            exchange_openai_authorization_code(code, pending.verifier)
        )
        save_oauth_credentials(OPENAI_CODEX_PROVIDER, credentials)
    except Exception:
        return
    finally:
        pending.server.close()
        if _PENDING_OPENAI_CODEX is pending:
            _PENDING_OPENAI_CODEX = None


def _auth_status_lines() -> str:
    credentials = list_credentials()
    if not credentials:
        return "auth: no stored credentials"
    lines = ["auth:"]
    for provider, entry in sorted(credentials.items()):
        lines.append(f"- {provider}: {_entry_summary(entry)}")
    return "\n".join(lines)


def _entry_summary(entry: dict[str, Any]) -> str:
    if entry.get("type") == "api_key":
        return f"api_key key={entry.get('key')}"
    if entry.get("type") == "oauth":
        account = f" account={entry.get('accountId')}" if entry.get("accountId") else ""
        return f"oauth access={entry.get('access')} expires={entry.get('expires')}{account}"
    return str(entry)


__all__ = ["handle_login", "handle_logout"]

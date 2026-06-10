"""GitHub Copilot device-flow OAuth.

Copilot reuses the VS Code OAuth App client id (public, identical for every
user). The login is a GitHub device flow (no callback server, no PKCE): the
long-lived GitHub token is stored as ``refresh`` and exchanged for a
short-lived Copilot token (``access``, ~30 min, ``tid=...;proxy-ep=...``) at
``copilot_internal/v2/token``. The chat base url is derived from the token's
``proxy-ep`` (or the enterprise domain), not hard-coded.

This module imports base OAuth types from :mod:`ai_provider.oauth` but
``oauth`` never imports this module, so there is no import cycle. The Copilot
provider self-registers (and registers a builtin factory so
``reset_oauth_providers_for_tests`` re-adds it) at import time.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .oauth import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthError,
    OAuthLoginCallbacks,
    OAuthPrompt,
    _maybe_await,
    _now_ms,
    register_builtin_oauth_provider_factory,
    register_oauth_provider,
)

GITHUB_COPILOT_PROVIDER_ID = "github-copilot"
GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
GITHUB_COPILOT_DEFAULT_DOMAIN = "github.com"
GITHUB_COPILOT_SCOPE = "read:user"
GITHUB_COPILOT_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
GITHUB_COPILOT_INDIVIDUAL_BASE_URL = "https://api.individual.githubcopilot.com"
# pi-mono expires Copilot tokens 5 minutes early; keep the same safety margin.
GITHUB_COPILOT_TOKEN_EXPIRY_SKEW_MS = 5 * 60 * 1000
GITHUB_COPILOT_DEVICE_POLL_INITIAL_MULTIPLIER = 1.2
GITHUB_COPILOT_DEVICE_POLL_SLOW_DOWN_MULTIPLIER = 1.4
COPILOT_USER_AGENT = "GitHubCopilotChat/0.35.0"
COPILOT_EDITOR_VERSION = "vscode/1.107.0"
COPILOT_PLUGIN_VERSION = "copilot-chat/0.35.0"
COPILOT_INTEGRATION_ID = "vscode-chat"
COPILOT_HEADERS: dict[str, str] = {
    "User-Agent": COPILOT_USER_AGENT,
    "Editor-Version": COPILOT_EDITOR_VERSION,
    "Editor-Plugin-Version": COPILOT_PLUGIN_VERSION,
    "Copilot-Integration-Id": COPILOT_INTEGRATION_ID,
}

HttpJson = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class GitHubCopilotDeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


def normalize_domain(value: str) -> str | None:
    """Normalize a GitHub Enterprise URL or bare host to a hostname.

    Accepts ``company.ghe.com`` or ``https://company.ghe.com/path`` and returns
    the hostname; returns ``None`` for blank or unparseable input.
    """

    trimmed = value.strip()
    if not trimmed:
        return None
    candidate = trimmed if "://" in trimmed else f"https://{trimmed}"
    host = urlparse(candidate).hostname
    return host or None


def get_github_copilot_urls(domain: str) -> dict[str, str]:
    return {
        "device_code": f"https://{domain}/login/device/code",
        "access_token": f"https://{domain}/login/oauth/access_token",
        "copilot_token": f"https://api.{domain}/copilot_internal/v2/token",
    }


def github_copilot_base_url(
    token: str | None,
    enterprise_domain: str | None = None,
) -> str:
    """Resolve the Copilot chat base url from a token's ``proxy-ep``.

    Falls back to ``copilot-api.<enterprise-domain>`` then the individual host.
    """

    if token:
        match = re.search(r"proxy-ep=([^;]+)", token)
        if match:
            proxy_host = match.group(1)
            api_host = re.sub(r"^proxy\.", "api.", proxy_host)
            return f"https://{api_host}"
    if enterprise_domain:
        return f"https://copilot-api.{enterprise_domain}"
    return GITHUB_COPILOT_INDIVIDUAL_BASE_URL


def start_github_copilot_device_flow(
    domain: str,
    *,
    http_json: HttpJson | None = None,
) -> GitHubCopilotDeviceCode:
    http = http_json or _http_json_sync
    urls = get_github_copilot_urls(domain)
    raw = http(
        urls["device_code"],
        method="POST",
        headers={"User-Agent": COPILOT_USER_AGENT},
        form={"client_id": GITHUB_COPILOT_CLIENT_ID, "scope": GITHUB_COPILOT_SCOPE},
        error_label="GitHub device code endpoint",
    )
    device_code = raw.get("device_code")
    user_code = raw.get("user_code")
    verification_uri = raw.get("verification_uri")
    interval = raw.get("interval")
    expires_in = raw.get("expires_in")
    if not isinstance(device_code, str) or not isinstance(user_code, str):
        raise OAuthError("GitHub device code response missing device_code/user_code")
    if not isinstance(verification_uri, str):
        raise OAuthError("GitHub device code response missing verification_uri")
    if not isinstance(interval, int | float) or not isinstance(expires_in, int | float):
        raise OAuthError("GitHub device code response missing interval/expires_in")
    return GitHubCopilotDeviceCode(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        interval=int(interval),
        expires_in=int(expires_in),
    )


def poll_github_copilot_access_token(
    domain: str,
    device_code: str,
    interval: int,
    expires_in: int,
    *,
    http_json: HttpJson | None = None,
    sleep: Callable[[float], None] | None = None,
    now_ms: Callable[[], int] | None = None,
) -> str:
    """Poll the device token endpoint until a GitHub access token is granted."""

    http = http_json or _http_json_sync
    do_sleep = sleep or time.sleep
    clock = now_ms or _now_ms
    urls = get_github_copilot_urls(domain)
    deadline = clock() + expires_in * 1000
    interval_ms = max(1000, int(interval * 1000))
    multiplier = GITHUB_COPILOT_DEVICE_POLL_INITIAL_MULTIPLIER
    while clock() < deadline:
        wait_ms = min(int(interval_ms * multiplier), deadline - clock())
        do_sleep(max(0.0, wait_ms / 1000))
        raw = http(
            urls["access_token"],
            method="POST",
            headers={"User-Agent": COPILOT_USER_AGENT},
            form={
                "client_id": GITHUB_COPILOT_CLIENT_ID,
                "device_code": device_code,
                "grant_type": GITHUB_COPILOT_DEVICE_GRANT,
            },
            error_label="GitHub access token endpoint",
        )
        access = raw.get("access_token")
        if isinstance(access, str) and access:
            return access
        error = raw.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            new_interval = raw.get("interval")
            if isinstance(new_interval, int | float) and new_interval > 0:
                interval_ms = int(new_interval) * 1000
            else:
                interval_ms = max(1000, interval_ms + 5000)
            multiplier = GITHUB_COPILOT_DEVICE_POLL_SLOW_DOWN_MULTIPLIER
            continue
        if isinstance(error, str) and error:
            description = raw.get("error_description")
            suffix = f": {description}" if isinstance(description, str) and description else ""
            raise OAuthError(f"GitHub device flow failed: {error}{suffix}")
    raise OAuthError("GitHub device flow timed out")


def exchange_github_copilot_token(
    github_token: str,
    enterprise_domain: str | None = None,
    *,
    http_json: HttpJson | None = None,
    now_ms: Callable[[], int] | None = None,
) -> OAuthCredentials:
    """Exchange a GitHub token for a short-lived Copilot token.

    ``now_ms`` is accepted for refresher-dispatch symmetry; the Copilot token
    expiry is absolute (from ``expires_at``), so it is not consulted here.
    """

    http = http_json or _http_json_sync
    domain = enterprise_domain or GITHUB_COPILOT_DEFAULT_DOMAIN
    urls = get_github_copilot_urls(domain)
    raw = http(
        urls["copilot_token"],
        method="GET",
        headers={"Authorization": f"Bearer {github_token}", **COPILOT_HEADERS},
        error_label="GitHub Copilot token endpoint",
    )
    token = raw.get("token")
    expires_at = raw.get("expires_at")
    if not isinstance(token, str) or not token:
        raise OAuthError("GitHub Copilot token response missing token")
    if not isinstance(expires_at, int | float):
        raise OAuthError("GitHub Copilot token response missing expires_at")
    extra: dict[str, Any] = {}
    if enterprise_domain:
        extra["enterpriseUrl"] = enterprise_domain
    return OAuthCredentials(
        access=token,
        refresh=github_token,
        expires=int(expires_at * 1000) - GITHUB_COPILOT_TOKEN_EXPIRY_SKEW_MS,
        extra=extra,
    )


def refresh_github_copilot_credentials_sync(
    credentials: OAuthCredentials,
    *,
    http_json: HttpJson | None = None,
    now_ms: Callable[[], int] | None = None,
) -> OAuthCredentials:
    """Re-exchange the stored GitHub token, preserving the enterprise domain."""

    enterprise = credentials.extra.get("enterpriseUrl")
    enterprise_domain = (
        normalize_domain(enterprise) if isinstance(enterprise, str) and enterprise else None
    )
    return exchange_github_copilot_token(
        credentials.refresh,
        enterprise_domain,
        http_json=http_json,
        now_ms=now_ms,
    )


class GitHubCopilotOAuthProvider:
    id = GITHUB_COPILOT_PROVIDER_ID
    name = "GitHub Copilot"
    uses_callback_server = False

    def __init__(
        self,
        *,
        http_json: HttpJson | None = None,
        now_ms: Callable[[], int] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._http_json = http_json or _http_json_sync
        self._now_ms = now_ms or _now_ms
        self._sleep = sleep or time.sleep

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        enterprise_domain = await self._prompt_enterprise_domain(callbacks)
        domain = enterprise_domain or GITHUB_COPILOT_DEFAULT_DOMAIN
        device = await asyncio.to_thread(
            start_github_copilot_device_flow, domain, http_json=self._http_json
        )
        await _maybe_await(
            callbacks.on_auth(
                OAuthAuthInfo(
                    url=device.verification_uri,
                    instructions=f"Enter code: {device.user_code}",
                )
            )
        )
        github_token = await asyncio.to_thread(
            poll_github_copilot_access_token,
            domain,
            device.device_code,
            device.interval,
            device.expires_in,
            http_json=self._http_json,
            sleep=self._sleep,
            now_ms=self._now_ms,
        )
        if callbacks.on_progress is not None:
            await _maybe_await(callbacks.on_progress("Exchanging Copilot token..."))
        return await asyncio.to_thread(
            exchange_github_copilot_token,
            github_token,
            enterprise_domain,
            http_json=self._http_json,
            now_ms=self._now_ms,
        )

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        return await asyncio.to_thread(
            refresh_github_copilot_credentials_sync,
            credentials,
            http_json=self._http_json,
            now_ms=self._now_ms,
        )

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    async def _prompt_enterprise_domain(
        self, callbacks: OAuthLoginCallbacks
    ) -> str | None:
        raw = await _maybe_await(
            callbacks.on_prompt(
                OAuthPrompt(
                    message="GitHub Enterprise URL/domain (blank for github.com):"
                )
            )
        )
        trimmed = (raw or "").strip()
        if not trimmed:
            return None
        enterprise_domain = normalize_domain(trimmed)
        if enterprise_domain is None:
            raise OAuthError("invalid GitHub Enterprise URL/domain")
        return enterprise_domain


def _http_json_sync(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    form: Mapping[str, str] | None = None,
    error_label: str = "request",
) -> Mapping[str, Any]:
    """General JSON HTTP helper (GET or form POST) with custom headers."""

    data = urlencode(form).encode("utf-8") if form is not None else None
    request_headers = {"Accept": "application/json"}
    if data is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    if headers:
        request_headers.update(headers)
    request = Request(url, data=data, headers=request_headers, method=method)  # noqa: S310 - URL fixed by caller.
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OAuthError(f"{error_label} returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise OAuthError(f"{error_label} request failed: {exc}") from exc
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping):
        raise OAuthError(f"{error_label} returned non-object JSON")
    return parsed


register_builtin_oauth_provider_factory(GitHubCopilotOAuthProvider)
register_oauth_provider(GitHubCopilotOAuthProvider())


__all__ = [
    "COPILOT_HEADERS",
    "GITHUB_COPILOT_CLIENT_ID",
    "GITHUB_COPILOT_DEFAULT_DOMAIN",
    "GITHUB_COPILOT_INDIVIDUAL_BASE_URL",
    "GITHUB_COPILOT_PROVIDER_ID",
    "GitHubCopilotDeviceCode",
    "GitHubCopilotOAuthProvider",
    "exchange_github_copilot_token",
    "get_github_copilot_urls",
    "github_copilot_base_url",
    "normalize_domain",
    "poll_github_copilot_access_token",
    "refresh_github_copilot_credentials_sync",
    "start_github_copilot_device_flow",
]

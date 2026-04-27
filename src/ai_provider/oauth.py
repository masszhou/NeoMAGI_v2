"""OAuth provider registry and OpenAI Codex OAuth flow.

The built-in registry is intentionally OpenAI-only for P1 core. Anthropic
OAuth is not registered here.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import inspect
import json
import queue
import secrets
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

OPENAI_OAUTH_PROVIDER_ID = "openai"
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_JWT_CLAIM_PATH = "https://api.openai.com/auth"
OPENAI_CODEX_ORIGINATOR = "pi"
DEFAULT_CALLBACK_HOST = "127.0.0.1"
DEFAULT_CALLBACK_PORT = 1455
TOKEN_REFRESH_SKEW_MS = 60_000


class OAuthError(RuntimeError):
    """Raised when an OAuth flow cannot produce usable credentials."""


@dataclass(frozen=True, slots=True)
class OAuthCredentials:
    access: str
    refresh: str
    expires: int
    account_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "OAuthCredentials":
        access = data.get("access")
        refresh = data.get("refresh")
        expires = data.get("expires")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise OAuthError("OAuth credentials require access and refresh tokens")
        if not isinstance(expires, int):
            raise OAuthError("OAuth credentials require integer expires timestamp")
        known = {"access", "refresh", "expires", "accountId", "account_id", "type"}
        return cls(
            access=access,
            refresh=refresh,
            expires=expires,
            account_id=_optional_str(data.get("accountId") or data.get("account_id")),
            extra={key: value for key, value in data.items() if key not in known},
        )

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "access": self.access,
            "refresh": self.refresh,
            "expires": self.expires,
        }
        if self.account_id:
            data["accountId"] = self.account_id
        data.update(self.extra)
        return data


@dataclass(frozen=True, slots=True)
class OAuthAuthInfo:
    url: str
    instructions: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthPrompt:
    message: str


@dataclass(frozen=True, slots=True)
class AuthorizationInput:
    code: str | None = None
    state: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthApiKeyResult:
    api_key: str
    new_credentials: OAuthCredentials


OnAuthCallback = Callable[[OAuthAuthInfo], Awaitable[None] | None]
OnPromptCallback = Callable[[OAuthPrompt], Awaitable[str] | str]
OnProgressCallback = Callable[[str], Awaitable[None] | None]
OnManualCodeInputCallback = Callable[[], Awaitable[str] | str]
TokenPost = Callable[[str, Mapping[str, str]], Awaitable[Mapping[str, Any]] | Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class OAuthLoginCallbacks:
    on_auth: OnAuthCallback
    on_prompt: OnPromptCallback
    on_progress: OnProgressCallback | None = None
    on_manual_code_input: OnManualCodeInputCallback | None = None


class OAuthCallbackServer(Protocol):
    def cancel_wait(self) -> None: ...

    async def wait_for_code(self) -> str | None: ...

    def close(self) -> None: ...


class OAuthProvider(Protocol):
    id: str
    name: str
    uses_callback_server: bool

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials: ...

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials: ...

    def get_api_key(self, credentials: OAuthCredentials) -> str: ...


class OpenAIOAuthProvider:
    id = OPENAI_OAUTH_PROVIDER_ID
    name = "OpenAI (Codex OAuth)"
    uses_callback_server = True

    def __init__(
        self,
        *,
        callback_host: str = DEFAULT_CALLBACK_HOST,
        callback_port: int = DEFAULT_CALLBACK_PORT,
        originator: str = OPENAI_CODEX_ORIGINATOR,
        token_post: TokenPost | None = None,
        callback_server_factory: Callable[[str], OAuthCallbackServer] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._callback_host = callback_host
        self._callback_port = callback_port
        self._originator = originator
        self._token_post = token_post or _default_post_form_json
        self._callback_server_factory = callback_server_factory
        self._now_ms = now_ms or _now_ms

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        verifier, challenge = generate_pkce()
        state = secrets.token_hex(16)
        authorize_url = build_openai_authorization_url(
            challenge=challenge,
            state=state,
            originator=self._originator,
        )
        server = self._start_callback_server(state)

        try:
            await _maybe_await(
                callbacks.on_auth(
                    OAuthAuthInfo(
                        url=authorize_url,
                        instructions=(
                            "Open the URL in a browser, complete OpenAI login, "
                            "then return to NeoMAGI."
                        ),
                    )
                )
            )
            code = await self._collect_authorization_code(callbacks, server, state)
            token_data = await self._post_token(
                {
                    "grant_type": "authorization_code",
                    "client_id": OPENAI_CODEX_CLIENT_ID,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": OPENAI_CODEX_REDIRECT_URI,
                }
            )
            return _openai_credentials_from_token_response(token_data, self._now_ms)
        finally:
            server.close()

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        token_data = await self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": credentials.refresh,
                "client_id": OPENAI_CODEX_CLIENT_ID,
            }
        )
        return _openai_credentials_from_token_response(token_data, self._now_ms)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    async def _collect_authorization_code(
        self,
        callbacks: OAuthLoginCallbacks,
        server: OAuthCallbackServer,
        state: str,
    ) -> str:
        code = await _race_callback_and_manual_input(callbacks, server, state)
        if not code:
            prompt_value = await _maybe_await(
                callbacks.on_prompt(
                    OAuthPrompt(
                        message="Paste the authorization code or full redirect URL:"
                    )
                )
            )
            code = parse_and_validate_authorization_input(prompt_value, state).code
        if not code:
            raise OAuthError("Missing OpenAI authorization code")
        return code

    async def _post_token(self, data: Mapping[str, str]) -> Mapping[str, Any]:
        response = self._token_post(OPENAI_CODEX_TOKEN_URL, data)
        resolved = await response if inspect.isawaitable(response) else response
        return resolved

    def _start_callback_server(self, state: str) -> OAuthCallbackServer:
        if self._callback_server_factory is not None:
            return self._callback_server_factory(state)
        return _LocalOAuthCallbackServer.start(
            host=self._callback_host,
            port=self._callback_port,
            expected_state=state,
        )


def generate_pkce() -> tuple[str, str]:
    verifier = _base64url_encode(secrets.token_bytes(32))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, _base64url_encode(digest)


def build_openai_authorization_url(*, challenge: str, state: str, originator: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": OPENAI_CODEX_CLIENT_ID,
            "redirect_uri": OPENAI_CODEX_REDIRECT_URI,
            "scope": OPENAI_CODEX_SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": originator,
        }
    )
    return f"{OPENAI_CODEX_AUTHORIZE_URL}?{query}"


def refresh_openai_oauth_credentials_sync(
    credentials: OAuthCredentials,
    *,
    now_ms: Callable[[], int] | None = None,
) -> OAuthCredentials:
    token_data = _post_form_json_sync(
        OPENAI_CODEX_TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh,
            "client_id": OPENAI_CODEX_CLIENT_ID,
        },
    )
    return _openai_credentials_from_token_response(token_data, now_ms or _now_ms)


def parse_authorization_input(value: str) -> AuthorizationInput:
    trimmed = value.strip()
    if not trimmed:
        return AuthorizationInput()

    parsed = urlparse(trimmed)
    if parsed.scheme and parsed.netloc:
        params = parse_qs(parsed.query)
        return AuthorizationInput(
            code=_first_param(params, "code"),
            state=_first_param(params, "state"),
        )

    if "#" in trimmed:
        code, state = trimmed.split("#", 1)
        return AuthorizationInput(code=code or None, state=state or None)

    if "code=" in trimmed:
        params = parse_qs(trimmed)
        return AuthorizationInput(
            code=_first_param(params, "code"),
            state=_first_param(params, "state"),
        )

    return AuthorizationInput(code=trimmed)


def parse_and_validate_authorization_input(
    value: str, expected_state: str
) -> AuthorizationInput:
    parsed = parse_authorization_input(value)
    if parsed.state and parsed.state != expected_state:
        raise OAuthError("OpenAI OAuth state mismatch")
    return parsed


def extract_openai_account_id(access_token: str) -> str | None:
    payload = _decode_jwt_payload(access_token)
    auth_claim = payload.get(OPENAI_CODEX_JWT_CLAIM_PATH)
    if not isinstance(auth_claim, Mapping):
        return None
    return _optional_str(auth_claim.get("chatgpt_account_id"))


def register_oauth_provider(provider: OAuthProvider) -> None:
    _oauth_providers[provider.id] = provider


def unregister_oauth_provider(provider_id: str) -> None:
    _oauth_providers.pop(provider_id, None)


def get_oauth_provider(provider_id: str) -> OAuthProvider:
    try:
        return _oauth_providers[provider_id]
    except KeyError as exc:
        raise KeyError(f"unknown OAuth provider {provider_id!r}") from exc


def list_oauth_providers() -> list[OAuthProvider]:
    return list(_oauth_providers.values())


def reset_oauth_providers_for_tests() -> None:
    _oauth_providers.clear()
    _register_builtin_oauth_providers()


async def get_oauth_api_key(
    provider_id: str,
    credentials_by_provider: Mapping[str, OAuthCredentials | Mapping[str, Any]],
    *,
    now_ms: Callable[[], int] | None = None,
) -> OAuthApiKeyResult | None:
    raw_credentials = credentials_by_provider.get(provider_id)
    if raw_credentials is None:
        return None
    credentials = _coerce_credentials(raw_credentials)
    provider = get_oauth_provider(provider_id)
    current_time = (now_ms or _now_ms)()
    if credentials.expires <= current_time + TOKEN_REFRESH_SKEW_MS:
        credentials = await provider.refresh_token(credentials)
    return OAuthApiKeyResult(
        api_key=provider.get_api_key(credentials),
        new_credentials=credentials,
    )


async def _race_callback_and_manual_input(
    callbacks: OAuthLoginCallbacks,
    server: OAuthCallbackServer,
    state: str,
) -> str | None:
    manual_input = callbacks.on_manual_code_input
    if manual_input is None:
        return await server.wait_for_code()
    return await _race_callback_task_with_manual_input(manual_input, server, state)


async def _race_callback_task_with_manual_input(
    manual_input: OnManualCodeInputCallback,
    server: OAuthCallbackServer,
    state: str,
) -> str | None:
    manual_task = asyncio.create_task(_maybe_await(manual_input()))
    callback_task = asyncio.create_task(server.wait_for_code())
    done, pending = await asyncio.wait(
        {manual_task, callback_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    try:
        if callback_task in done:
            callback_code = callback_task.result()
            if callback_code:
                manual_task.cancel()
                return callback_code
        if manual_task in done:
            server.cancel_wait()
            return parse_and_validate_authorization_input(manual_task.result(), state).code
        manual_value = await manual_task
        return parse_and_validate_authorization_input(manual_value, state).code
    finally:
        for task in pending:
            task.cancel()


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _coerce_credentials(
    credentials: OAuthCredentials | Mapping[str, Any],
) -> OAuthCredentials:
    if isinstance(credentials, OAuthCredentials):
        return credentials
    return OAuthCredentials.from_mapping(credentials)


def _openai_credentials_from_token_response(
    token_data: Mapping[str, Any],
    now_ms: Callable[[], int],
) -> OAuthCredentials:
    access = token_data.get("access_token")
    refresh = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise OAuthError("OpenAI token response missing access or refresh token")
    if not isinstance(expires_in, int | float):
        raise OAuthError("OpenAI token response missing expires_in")
    account_id = extract_openai_account_id(access)
    if not account_id:
        raise OAuthError("OpenAI token response missing ChatGPT account id claim")
    return OAuthCredentials(
        access=access,
        refresh=refresh,
        expires=now_ms() + int(expires_in * 1000),
        account_id=account_id,
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_param(params: Mapping[str, list[str]], key: str) -> str | None:
    values = params.get(key) or []
    return values[0] if values else None


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode_jwt_payload(token: str) -> Mapping[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        parsed = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


async def _default_post_form_json(
    url: str,
    data: Mapping[str, str],
) -> Mapping[str, Any]:
    return await asyncio.to_thread(_post_form_json_sync, url, data)


def _post_form_json_sync(url: str, data: Mapping[str, str]) -> Mapping[str, Any]:
    body = urlencode(data).encode("utf-8")
    request = Request(  # noqa: S310 - OpenAI OAuth token URL is fixed by caller.
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OAuthError(f"OpenAI token endpoint returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise OAuthError(f"OpenAI token endpoint request failed: {exc}") from exc
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping):
        raise OAuthError("OpenAI token endpoint returned non-object JSON")
    return parsed


def _now_ms() -> int:
    return int(time.time() * 1000)


class _NoopOAuthCallbackServer:
    def cancel_wait(self) -> None:
        return None

    async def wait_for_code(self) -> str | None:
        return None

    def close(self) -> None:
        return None


class _LocalOAuthCallbackServer:
    def __init__(
        self,
        server: ThreadingHTTPServer,
        result_queue: "queue.Queue[str | None]",
    ) -> None:
        self._server = server
        self._result_queue = result_queue
        self._closed = False

    @classmethod
    def start(
        cls,
        *,
        host: str,
        port: int,
        expected_state: str,
    ) -> OAuthCallbackServer:
        result_queue: queue.Queue[str | None] = queue.Queue(maxsize=1)
        handler = _make_oauth_handler(expected_state, result_queue)
        try:
            server = ThreadingHTTPServer((host, port), handler)
        except OSError:
            return _NoopOAuthCallbackServer()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return cls(server, result_queue)

    def cancel_wait(self) -> None:
        _queue_put_once(self._result_queue, None)

    async def wait_for_code(self) -> str | None:
        return await asyncio.to_thread(self._result_queue.get)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel_wait()
        self._server.shutdown()
        self._server.server_close()


def _make_oauth_handler(
    expected_state: str,
    result_queue: "queue.Queue[str | None]",
) -> type[BaseHTTPRequestHandler]:
    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path != "/auth/callback":
                self._send_html(HTTPStatus.NOT_FOUND, _oauth_error_html("Callback route not found."))
                return
            params = parse_qs(parsed.query)
            if _first_param(params, "state") != expected_state:
                self._send_html(HTTPStatus.BAD_REQUEST, _oauth_error_html("State mismatch."))
                return
            code = _first_param(params, "code")
            if not code:
                self._send_html(
                    HTTPStatus.BAD_REQUEST,
                    _oauth_error_html("Missing authorization code."),
                )
                return
            _queue_put_once(result_queue, code)
            self._send_html(
                HTTPStatus.OK,
                _oauth_success_html(
                    "OpenAI authentication completed. You can close this window."
                ),
            )

        def log_message(self, format: str, *args: object) -> None:
            return None

        def _send_html(self, status: HTTPStatus, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return OAuthCallbackHandler


def _queue_put_once(result_queue: "queue.Queue[str | None]", value: str | None) -> None:
    try:
        result_queue.put_nowait(value)
    except queue.Full:
        return


def _oauth_success_html(message: str) -> str:
    return _oauth_page("Authentication complete", message)


def _oauth_error_html(message: str) -> str:
    return _oauth_page("Authentication failed", message)


def _oauth_page(title: str, message: str) -> str:
    escaped_title = html.escape(title)
    escaped_message = html.escape(message)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{escaped_title}</title></head><body>"
        f"<h1>{escaped_title}</h1><p>{escaped_message}</p>"
        "</body></html>"
    )


_oauth_providers: dict[str, OAuthProvider] = {}


def _register_builtin_oauth_providers() -> None:
    register_oauth_provider(OpenAIOAuthProvider())


_register_builtin_oauth_providers()


__all__ = [
    "AuthorizationInput",
    "OPENAI_OAUTH_PROVIDER_ID",
    "OAuthApiKeyResult",
    "OAuthAuthInfo",
    "OAuthCredentials",
    "OAuthError",
    "OAuthLoginCallbacks",
    "OAuthPrompt",
    "OAuthProvider",
    "OpenAIOAuthProvider",
    "build_openai_authorization_url",
    "extract_openai_account_id",
    "generate_pkce",
    "get_oauth_api_key",
    "get_oauth_provider",
    "list_oauth_providers",
    "parse_and_validate_authorization_input",
    "parse_authorization_input",
    "register_oauth_provider",
    "refresh_openai_oauth_credentials_sync",
    "reset_oauth_providers_for_tests",
    "unregister_oauth_provider",
]

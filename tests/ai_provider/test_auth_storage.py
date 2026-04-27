from __future__ import annotations

import base64
import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_provider.auth_storage import (
    load_auth_storage,
    resolve_stored_api_key,
    save_api_key,
    save_oauth_credentials,
)
from ai_provider.oauth import OPENAI_CODEX_JWT_CLAIM_PATH, OAuthCredentials


def _jwt_with_account(account_id: str) -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = {OPENAI_CODEX_JWT_CLAIM_PATH: {"chatgpt_account_id": account_id}}
    return f"{_b64_json(header)}.{_b64_json(payload)}.signature"


def _b64_json(data: Mapping[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_save_oauth_credentials_uses_pi_compatible_shape(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"

    save_oauth_credentials(
        "openai-codex",
        OAuthCredentials(
            access=_jwt_with_account("acct-123"),
            refresh="refresh-123",
            expires=123_456,
            account_id="acct-123",
        ),
        auth_path,
    )

    storage = load_auth_storage(auth_path)
    assert storage == {
        "openai-codex": {
            "type": "oauth",
            "access": _jwt_with_account("acct-123"),
            "refresh": "refresh-123",
            "expires": 123_456,
            "accountId": "acct-123",
        }
    }
    assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(auth_path.parent.stat().st_mode) == 0o700


def test_resolve_stored_api_key_returns_fresh_oauth_access(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    token = _jwt_with_account("acct-123")
    save_oauth_credentials(
        "openai-codex",
        OAuthCredentials(access=token, refresh="refresh-123", expires=200_000),
        auth_path,
    )

    assert resolve_stored_api_key("openai-codex", auth_path, now_ms=lambda: 1_000) == token


def test_resolve_stored_api_key_refreshes_expired_oauth(
    monkeypatch,
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    save_oauth_credentials(
        "openai-codex",
        OAuthCredentials(access="old", refresh="refresh-old", expires=1_000),
        auth_path,
    )

    def fake_refresh(credentials: OAuthCredentials, *, now_ms):
        assert credentials.refresh == "refresh-old"
        return OAuthCredentials(
            access=_jwt_with_account("acct-new"),
            refresh="refresh-new",
            expires=300_000,
            account_id="acct-new",
        )

    monkeypatch.setattr(
        "ai_provider.auth_storage.refresh_openai_oauth_credentials_sync",
        fake_refresh,
    )

    token = resolve_stored_api_key("openai-codex", auth_path, now_ms=lambda: 200_000)

    assert token == _jwt_with_account("acct-new")
    assert load_auth_storage(auth_path)["openai-codex"]["refresh"] == "refresh-new"


def test_resolve_stored_api_key_supports_api_key_entries(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"

    save_api_key("openai", "sk-test", auth_path)

    assert resolve_stored_api_key("openai", auth_path) == "sk-test"

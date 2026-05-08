from __future__ import annotations

import base64
import json
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import ai_provider.auth_storage as auth_storage_module
from ai_provider.auth_storage import (
    default_auth_path,
    load_auth_storage,
    resolve_auth_path,
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


def test_default_auth_path_uses_user_config_dir(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(auth_storage_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(auth_storage_module, "LEGACY_AUTH_PATH", home / ".neomagi" / "auth.json")
    monkeypatch.setattr(auth_storage_module.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert default_auth_path({}) == home / ".config" / "neomagi" / "auth.json"


def test_default_auth_path_honors_xdg_config_home(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(auth_storage_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(auth_storage_module, "LEGACY_AUTH_PATH", home / ".neomagi" / "auth.json")
    monkeypatch.setattr(auth_storage_module.sys, "platform", "linux")
    custom = tmp_path / "xdg"

    assert (
        default_auth_path({"XDG_CONFIG_HOME": str(custom)})
        == custom / "neomagi" / "auth.json"
    )


def test_default_auth_path_uses_appdata_on_windows(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(auth_storage_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(auth_storage_module, "LEGACY_AUTH_PATH", home / ".neomagi" / "auth.json")
    monkeypatch.setattr(auth_storage_module.sys, "platform", "win32")
    appdata = tmp_path / "AppData"

    assert (
        default_auth_path({"APPDATA": str(appdata)})
        == appdata / "neomagi" / "auth.json"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only permission expectations")
def test_default_auth_path_migrates_legacy_file(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy_dir = home / ".neomagi"
    legacy_dir.mkdir(mode=0o700)
    legacy = legacy_dir / "auth.json"
    legacy.write_text('{"openai": {"type": "api_key", "key": "sk-legacy"}}\n', encoding="utf-8")
    legacy.chmod(0o600)
    monkeypatch.setattr(auth_storage_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(auth_storage_module, "LEGACY_AUTH_PATH", legacy)
    monkeypatch.setattr(auth_storage_module.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    new_path = default_auth_path({})

    assert new_path == home / ".config" / "neomagi" / "auth.json"
    assert new_path.is_file()
    assert not legacy.exists()
    assert json.loads(new_path.read_text(encoding="utf-8"))["openai"]["key"] == "sk-legacy"
    assert stat.S_IMODE(new_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(new_path.parent.stat().st_mode) == 0o700


def test_default_auth_path_skips_migration_when_new_already_exists(
    monkeypatch,
    tmp_path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy_dir = home / ".neomagi"
    legacy_dir.mkdir()
    legacy = legacy_dir / "auth.json"
    legacy.write_text('{"openai": {"type": "api_key", "key": "sk-legacy"}}\n', encoding="utf-8")
    new = home / ".config" / "neomagi" / "auth.json"
    new.parent.mkdir(parents=True)
    new.write_text('{"openai": {"type": "api_key", "key": "sk-new"}}\n', encoding="utf-8")
    monkeypatch.setattr(auth_storage_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(auth_storage_module, "LEGACY_AUTH_PATH", legacy)
    monkeypatch.setattr(auth_storage_module.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    resolved = default_auth_path({})

    assert resolved == new
    assert legacy.exists()  # untouched
    assert json.loads(new.read_text(encoding="utf-8"))["openai"]["key"] == "sk-new"


def test_default_auth_path_falls_back_to_legacy_on_migration_failure(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy_dir = home / ".neomagi"
    legacy_dir.mkdir()
    legacy = legacy_dir / "auth.json"
    legacy.write_text('{}\n', encoding="utf-8")
    monkeypatch.setattr(auth_storage_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(auth_storage_module, "LEGACY_AUTH_PATH", legacy)
    monkeypatch.setattr(auth_storage_module.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    def boom(*args, **kwargs):
        raise OSError("destination unwritable")

    monkeypatch.setattr(auth_storage_module.Path, "replace", boom)

    resolved = default_auth_path({})

    assert resolved == legacy
    assert "could not migrate" in capsys.readouterr().err


def test_resolve_auth_path_explicit_arg_skips_migration(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy = home / ".neomagi" / "auth.json"
    legacy.parent.mkdir()
    legacy.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(auth_storage_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(auth_storage_module, "LEGACY_AUTH_PATH", legacy)
    monkeypatch.delenv("NEOMAGI_AUTH_PATH", raising=False)
    explicit = tmp_path / "explicit.json"

    assert resolve_auth_path(explicit) == explicit
    assert legacy.exists()  # not migrated


def test_resolve_auth_path_honors_neomagi_auth_path_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NEOMAGI_AUTH_PATH", str(tmp_path / "custom.json"))

    assert resolve_auth_path() == tmp_path / "custom.json"

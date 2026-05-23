from __future__ import annotations

import re

from fastapi.testclient import TestClient
from storage.config import ConfigSource, DatabaseConfig

from webui.app import create_app
from webui.auth import hash_password
from webui.config import WebUIConfig, WebUIConfigError, load_webui_config


def test_api_requires_login() -> None:
    client = TestClient(create_app(config=_config(), dashboard_reader=_Reader()))

    response = client.get("/api/dashboard/database")

    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"


def test_login_sets_http_only_session_and_allows_dashboard_api() -> None:
    client = TestClient(create_app(config=_config(), dashboard_reader=_Reader()))

    login = client.get("/login")
    token = _csrf(login.text)
    response = client.post(
        "/login",
        data={"password": "secret", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session_cookie = response.headers["set-cookie"]
    assert "webui_session=" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert response.headers["Cache-Control"] == "no-store"
    payload = client.get("/api/dashboard/database").json()
    assert payload["panel6_audit_recent"]["items"][0]["subject"] == "git status"
    assert "raw_tool_args" not in payload["panel6_audit_recent"]["items"][0]


def test_dashboard_api_and_raw_detail_are_not_cacheable_after_login() -> None:
    client = TestClient(create_app(config=_config(), dashboard_reader=_Reader()))
    login = client.get("/login")
    client.post(
        "/login",
        data={"password": "secret", "csrf_token": _csrf(login.text)},
        follow_redirects=False,
    )

    dashboard = client.get("/api/dashboard/database")
    detail = client.get("/api/dashboard/audit/audit-1")

    assert dashboard.headers["Cache-Control"] == "no-store"
    assert detail.headers["Cache-Control"] == "no-store"
    assert detail.json()["raw_tool_args"] == {"command": "git status --short"}


def test_wrong_password_does_not_create_session_cookie() -> None:
    client = TestClient(create_app(config=_config(), dashboard_reader=_Reader()))

    login = client.get("/login")
    response = client.post(
        "/login",
        data={"password": "bad", "csrf_token": _csrf(login.text)},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "webui_session=" not in response.headers.get("set-cookie", "")


def test_secure_cookie_flag_is_configurable() -> None:
    client = TestClient(
        create_app(config=_config(cookie_secure=True), dashboard_reader=_Reader())
    )

    login = client.get("/login")
    response = client.post(
        "/login",
        data={"password": "secret", "csrf_token": _csrf(login.text)},
        follow_redirects=False,
    )

    assert "Secure" in response.headers["set-cookie"]


def test_missing_auth_config_fails_closed_before_app_start() -> None:
    env = {
        "WEBUI_PASSWORD_HASH": hash_password("secret", salt=b"0" * 16),
        "DATABASE_HOST": "localhost",
        "DATABASE_PORT": "5432",
        "DATABASE_USER": "user",
        "DATABASE_PASSWORD": "pw",
        "DATABASE_NAME": "db",
    }

    try:
        load_webui_config(env=env)
    except WebUIConfigError as exc:
        assert "WEBUI_SESSION_SECRET" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing session secret should fail closed")


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def _config(*, cookie_secure: bool = False) -> WebUIConfig:
    return WebUIConfig(
        password_hash=hash_password("secret", salt=b"1" * 16),
        session_secret="s" * 64,
        cookie_secure=cookie_secure,
        host="127.0.0.1",
        port=8787,
        database=DatabaseConfig(
            host="localhost",
            port=5432,
            user="user",
            password="pw",
            database="db",
            schema="neomagi",
        ),
        database_source=ConfigSource(kind="env", label="environment"),
    )


class _Reader:
    def read_dashboard(
        self,
        *,
        range_value: str = "7d",
        show_internal: bool = False,
        hide_tmp: bool = False,
    ):
        return {
            "_meta": {"range": range_value},
            "health": {"_panel": {"status": "ok", "skipped_count": 0, "warnings": []}},
            "panel1_taskrun_progress": {
                "_panel": {"status": "empty", "skipped_count": 0, "warnings": []},
                "status_distribution": [],
                "recent_taskruns": [],
            },
            "panel2_session_coherence": {
                "_panel": {"status": "empty", "skipped_count": 0, "warnings": []},
                "workspaces": [],
                "sessions_in_top_workspace": [],
            },
            "panel3_tool_health": {
                "_panel": {"status": "empty", "skipped_count": 0, "warnings": []},
                "tool_stats": [],
                "recent_perm_blocks": [],
            },
            "panel4_event_stream": {
                "_panel": {"status": "empty", "skipped_count": 0, "warnings": []},
                "taskrun_id": None,
                "taskrun_summary": None,
                "events": [],
            },
            "panel5_usage_trend": {
                "_panel": {"status": "empty", "skipped_count": 0, "warnings": []},
                "items": [],
                "totals": {},
            },
            "panel6_audit_recent": {
                "_panel": {"status": "ok", "skipped_count": 0, "warnings": []},
                "items": [{"id": "audit-1", "subject": "git status"}],
            },
        }

    def read_audit_detail(self, audit_event_id: str):
        return {
            "id": audit_event_id,
            "metadata": {"args": {"commandPreview": "git status"}},
            "raw_tool_args": {"command": "git status --short"},
        }

"""FastAPI app for the NeoMAGI operator dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE,
    new_csrf_pair,
    sign_session,
    verify_csrf,
    verify_password,
    verify_session,
)
from .config import WebUIConfig, load_webui_config
from .dashboard_queries import DashboardQueryError, DashboardQueryService
from .dashboard_schema import DashboardRangeError


class DashboardReader(Protocol):
    def read_dashboard(
        self,
        *,
        range_value: str = "7d",
        show_internal: bool = False,
        hide_tmp: bool = False,
    ) -> dict[str, Any]:
        ...

    def read_audit_detail(self, audit_event_id: str) -> dict[str, Any]:
        ...


PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(
    *,
    config: WebUIConfig | None = None,
    dashboard_reader: DashboardReader | None = None,
) -> FastAPI:
    resolved_config = config or load_webui_config()
    reader = dashboard_reader or DashboardQueryService(
        resolved_config.database,
        database_source_label=resolved_config.safe_database_source_label,
    )

    app = FastAPI(title="NeoMAGI WebUI", docs_url=None, redoc_url=None)
    app.state.webui_config = resolved_config
    app.state.dashboard_reader = reader

    @app.middleware("http")
    async def no_store_sensitive_routes(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if _is_sensitive_path(request.url.path):
            _no_store(response)
        return response

    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_DIR / "static")),
        name="static",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        nonce, token = new_csrf_pair(resolved_config.session_secret)
        response = templates.TemplateResponse(
            request,
            "login.html",
            {"csrf_token": token},
        )
        _attach_csrf(response, resolved_config, nonce)
        return _no_store(response)

    @app.post("/login")
    async def login(request: Request) -> Response:
        form = await _form_body(request)
        if not verify_csrf(
            resolved_config.session_secret,
            request.cookies.get(CSRF_COOKIE_NAME),
            form.get("csrf_token", ""),
        ):
            return _login_error(request, resolved_config)
        password = form.get("password", "")
        if not verify_password(password, resolved_config.password_hash):
            return _login_error(request, resolved_config)
        response = RedirectResponse("/dashboard/database", status_code=303)
        response.set_cookie(
            SESSION_COOKIE.name,
            sign_session(resolved_config.session_secret),
            max_age=SESSION_COOKIE.max_age_seconds,
            httponly=True,
            samesite="lax",
            secure=resolved_config.cookie_secure,
        )
        return _no_store(response)

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        _require_authenticated(request, resolved_config)
        form = await _form_body(request)
        if not verify_csrf(
            resolved_config.session_secret,
            request.cookies.get(CSRF_COOKIE_NAME),
            form.get("csrf_token", ""),
        ):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE.name)
        return _no_store(response)

    @app.get("/")
    def index(request: Request) -> Response:
        if not _is_authenticated(request, resolved_config):
            return _no_store(RedirectResponse("/login", status_code=303))
        return _no_store(RedirectResponse("/dashboard/database", status_code=303))

    @app.get("/dashboard/database", response_class=HTMLResponse)
    def database_dashboard(request: Request) -> Response:
        if not _is_authenticated(request, resolved_config):
            return _no_store(RedirectResponse("/login", status_code=303))
        nonce, token = new_csrf_pair(resolved_config.session_secret)
        response = templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "csrf_token": token,
                "database_schema": resolved_config.database.schema,
                "database_source": resolved_config.safe_database_source_label,
            },
        )
        _attach_csrf(response, resolved_config, nonce)
        return _no_store(response)

    @app.get("/api/dashboard/database")
    def dashboard_api(
        request: Request,
        range: str = "7d",  # noqa: A002 - query name is part of the API contract.
        show_internal: bool = False,
        hide_tmp: bool = False,
    ) -> JSONResponse:
        _require_authenticated(request, resolved_config)
        try:
            payload = reader.read_dashboard(
                range_value=range,
                show_internal=show_internal,
                hide_tmp=hide_tmp,
            )
        except DashboardRangeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DashboardQueryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _no_store(JSONResponse(payload))

    @app.get("/api/dashboard/audit/{audit_event_id}")
    def audit_detail(request: Request, audit_event_id: str) -> JSONResponse:
        _require_authenticated(request, resolved_config)
        try:
            payload = reader.read_audit_detail(audit_event_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="audit event not found") from exc
        except DashboardQueryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _no_store(JSONResponse(payload))

    return app


def _is_authenticated(request: Request, config: WebUIConfig) -> bool:
    return verify_session(
        config.session_secret,
        request.cookies.get(SESSION_COOKIE.name),
    )


def _require_authenticated(request: Request, config: WebUIConfig) -> None:
    if not _is_authenticated(request, config):
        raise HTTPException(status_code=401, detail="authentication required")


def _attach_csrf(response: Response, config: WebUIConfig, nonce: str) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        nonce,
        httponly=True,
        samesite="lax",
        secure=config.cookie_secure,
    )


def _login_error(request: Request, config: WebUIConfig) -> Response:
    nonce, token = new_csrf_pair(config.session_secret)
    response = templates.TemplateResponse(
        request,
        "login.html",
        {
            "csrf_token": token,
            "error": "Invalid password.",
        },
        status_code=401,
    )
    _attach_csrf(response, config, nonce)
    return _no_store(response)


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _is_sensitive_path(path: str) -> bool:
    return path in {"/", "/login"} or path.startswith(("/dashboard/", "/api/"))


async def _form_body(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode()
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


__all__ = ["create_app"]

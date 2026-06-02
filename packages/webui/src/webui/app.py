"""FastAPI app for the NeoMAGI operator WebUI (Projects / TaskRun surface)."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

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
from .taskrun_queries import TaskRunQueryError, TaskRunQueryService


class TaskRunReader(Protocol):
    def read_meta(self) -> dict[str, Any]:
        ...

    def list_runs(self) -> list[dict[str, Any]]:
        ...

    def get_run(self, task_run_id: str) -> dict[str, Any] | None:
        ...


PACKAGE_DIR = Path(__file__).resolve().parent


def _default_static_dir() -> Path:
    override = os.environ.get("WEBUI_STATIC_DIR")
    if override:
        return Path(override).expanduser().resolve()
    # packages/webui/src/webui -> packages/webui/frontend/dist
    return PACKAGE_DIR.parents[1] / "frontend" / "dist"


def create_app(
    *,
    config: WebUIConfig | None = None,
    taskrun_reader: TaskRunReader | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    resolved_config = config or load_webui_config()
    reader: TaskRunReader = taskrun_reader or TaskRunQueryService(
        resolved_config.database,
        database_source_label=resolved_config.safe_database_source_label,
    )
    dist_dir = (static_dir or _default_static_dir()).resolve()
    index_file = dist_dir / "index.html"
    assets_dir = dist_dir / "assets"

    app = FastAPI(title="NeoMAGI WebUI", docs_url=None, redoc_url=None)
    app.state.webui_config = resolved_config
    app.state.taskrun_reader = reader

    @app.middleware("http")
    async def no_store_sensitive_routes(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if _is_sensitive_path(request.url.path):
            _no_store(response)
        return response

    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="assets",
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
        response = HTMLResponse(_login_html(token))
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
            return _login_error(resolved_config)
        password = form.get("password", "")
        if not verify_password(password, resolved_config.password_hash):
            return _login_error(resolved_config)
        response = RedirectResponse("/", status_code=303)
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

    @app.get("/api/meta")
    def meta(request: Request) -> JSONResponse:
        _require_authenticated(request, resolved_config)
        return _no_store(JSONResponse(reader.read_meta()))

    @app.get("/api/taskruns")
    def list_taskruns(request: Request) -> JSONResponse:
        _require_authenticated(request, resolved_config)
        try:
            runs = reader.list_runs()
        except TaskRunQueryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _no_store(JSONResponse({"runs": runs}))

    @app.get("/api/taskruns/{task_run_id}")
    def get_taskrun(request: Request, task_run_id: str) -> JSONResponse:
        _require_authenticated(request, resolved_config)
        try:
            run = reader.get_run(task_run_id)
        except TaskRunQueryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if run is None:
            raise HTTPException(status_code=404, detail="task run not found")
        return _no_store(JSONResponse(run))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        if not _is_authenticated(request, resolved_config):
            return _no_store(RedirectResponse("/login", status_code=303))
        if not index_file.is_file():
            return _no_store(HTMLResponse(_missing_build_html(dist_dir), status_code=503))
        return _no_store(HTMLResponse(index_file.read_text(encoding="utf-8")))

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


def _login_error(config: WebUIConfig) -> Response:
    nonce, token = new_csrf_pair(config.session_secret)
    response = HTMLResponse(_login_html(token, error="Invalid password."), status_code=401)
    _attach_csrf(response, config, nonce)
    return _no_store(response)


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _is_sensitive_path(path: str) -> bool:
    return path in {"/", "/login"} or path.startswith("/api/")


async def _form_body(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode()
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _login_html(csrf_token: str, *, error: str | None = None) -> str:
    error_block = (
        f'<div class="err" role="alert">{html.escape(error)}</div>' if error else ""
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>NeoMAGI WebUI — Login</title>
<style>
  :root {{ --wave-deep:#1B3A6B; --foam:#F5F1E8; --cream:#EFE5D0; --sand:#E2D4B5;
           --coral:#B45E3F; --ink:#1A1A1A; --mute:#6B6258; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
          background:var(--cream); color:var(--ink);
          font:400 14px/1.5 "Geist", system-ui, -apple-system, sans-serif; }}
  .panel {{ width:320px; max-width:90vw; background:var(--sand);
            border:1px solid rgba(26,26,26,0.70); padding:28px 26px 26px;
            box-shadow: inset 0 1px 0 0 var(--foam); }}
  .mark {{ width:40px; height:40px; display:grid; place-items:center;
           background:var(--wave-deep); color:var(--foam); font:700 24px/1 serif;
           border:1.5px solid var(--ink); margin-bottom:16px; }}
  h1 {{ font:700 22px/1 "Shippori Mincho", serif; margin:0 0 2px; }}
  p.sub {{ margin:0 0 18px; color:var(--mute); font-size:12px; }}
  label {{ display:block; font-size:11px; letter-spacing:0.08em; text-transform:uppercase;
           color:var(--ink); margin-bottom:6px; }}
  input[type=password] {{ width:100%; padding:9px 11px; border:1px solid rgba(26,26,26,0.70);
           background:var(--foam); font:inherit; outline:none; }}
  input[type=password]:focus {{ outline:2px solid var(--ink); outline-offset:-2px; }}
  button {{ width:100%; margin-top:16px; padding:9px 14px; cursor:pointer;
            border:1px solid var(--ink); background:#F285B5; color:var(--ink);
            font:600 13px/1 "Geist", sans-serif; }}
  button:hover {{ filter:brightness(0.97); }}
  .err {{ margin-bottom:14px; padding:8px 10px; font-size:12px;
          background:#D88A6C; border:1px solid var(--coral); }}
  .sr-only {{ position:absolute; width:1px; height:1px; padding:0; margin:-1px;
              overflow:hidden; clip:rect(0,0,0,0); border:0; }}
</style>
</head><body>
  <main class="panel">
    <div class="mark">N</div>
    <h1>NeoMAGI</h1>
    <p class="sub">Operator dashboard · Projects</p>
    {error_block}
    <form method="post" action="/login">
      <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}"/>
      <input class="sr-only" type="text" name="username" value="operator"
             autocomplete="username" tabindex="-1" aria-hidden="true"/>
      <label for="password">Password</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" autofocus required/>
      <button type="submit">Log in</button>
    </form>
  </main>
</body></html>"""


def _missing_build_html(dist_dir: Path) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"/>
<title>NeoMAGI WebUI</title>
<style>body{{font:14px/1.6 ui-monospace,monospace;background:#EFE5D0;color:#1A1A1A;
margin:0;display:grid;place-items:center;min-height:100vh}}
.box{{max-width:560px;padding:24px;border:1px solid rgba(26,26,26,.7);background:#E2D4B5}}
code{{background:#F5F1E8;padding:1px 5px}}</style></head>
<body><div class="box">
<h2>Frontend build not found</h2>
<p>Expected a built single-page app at:</p>
<p><code>{html.escape(str(dist_dir))}</code></p>
<p>Build it first:</p>
<pre><code>cd packages/webui/frontend
npm install
npm run build</code></pre>
<p>Or point <code>WEBUI_STATIC_DIR</code> at a built <code>dist/</code>.</p>
</div></body></html>"""


__all__ = ["create_app"]

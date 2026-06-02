# neomagi-webui

Operator WebUI for NeoMAGI — a read-only **Projects (TaskRun) observability**
dashboard. A FastAPI backend serves a Vite + React + TypeScript single-page app
and a small read-only JSON API over the existing Postgres TaskRun tables
(`task_runs`, `task_steps`, `task_experiments`).

This pass implements the **Projects** surface from the redesign (run list,
run detail, P3 trajectory git-graph, attempt detail). Other surfaces from the
design (Chat, Members, Workspace/Artifacts browsers) are intentionally deferred
— see [`DESIGN_DB_GAP.md`](./DESIGN_DB_GAP.md).

## Layout

```
packages/webui/
  src/webui/            FastAPI backend (Python)
    app.py              auth + /api/* + serves the built SPA
    taskrun_queries.py  read-only TaskRun read model (reuses magipi's P3 projection)
    auth.py config.py db.py __main__.py
  frontend/             Vite + React + TS single-page app
    src/...             App shell + Projects surface components
    dist/               build output (served by the backend)
```

## Configure

The backend reuses the single-operator auth model. Required environment:

| Variable | Purpose |
| --- | --- |
| `WEBUI_PASSWORD_HASH` | `pbkdf2_sha256` hash — generate with `magipi-webui hash-password` |
| `WEBUI_SESSION_SECRET` | ≥32-char secret for signing the session cookie |
| `WEBUI_HOST` / `WEBUI_PORT` | bind address (default `127.0.0.1:8787`) |
| `WEBUI_COOKIE_SECURE` | `true` when served over HTTPS |
| `DATABASE_*` | Postgres connection (shared with magipi; read-only access) |

```sh
export WEBUI_PASSWORD_HASH="$(uv run magipi-webui hash-password)"
export WEBUI_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

## Build the frontend

```sh
cd packages/webui/frontend
npm install
npm run build      # emits frontend/dist/
```

## Run

```sh
uv run magipi-webui serve            # http://127.0.0.1:8787
```

The backend serves `frontend/dist/` (override with `--static-dir` or
`WEBUI_STATIC_DIR`). All data access is read-only (`BEGIN READ ONLY`).

## Develop

Run the Vite dev server with hot reload; it proxies `/api` to the backend:

```sh
# terminal 1
uv run magipi-webui serve
# terminal 2
cd packages/webui/frontend && npm run dev
```

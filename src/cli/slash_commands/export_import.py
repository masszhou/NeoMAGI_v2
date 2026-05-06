"""Session import/export slash commands."""

from __future__ import annotations

from pathlib import Path

from storage.ids import short_session_id

from .registry import SlashCommandContext

_SUPPORTED_EXPORT_FORMATS = ".jsonl, .pi.jsonl, .session.json, .html"


def handle_export(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if len(ctx.args) != 1:
        ctx.controller.push_session_message(
            f"/export requires a path ({_SUPPORTED_EXPORT_FORMATS})",
            level="warn",
        )
        return
    try:
        path = _export_relative_path(ctx.args[0])
    except ValueError as exc:
        ctx.controller.push_session_message(
            str(exc),
            level="warn",
        )
        return
    try:
        exported = _dispatch_export(runtime, path)
    except (RuntimeError, ValueError, OSError) as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.push_session_message(f"exported session: {exported}")


def handle_import(ctx: SlashCommandContext) -> None:
    runtime = ctx.controller.runtime
    if runtime is None:
        ctx.controller.push_session_message("no interactive runtime", level="warn")
        return
    if len(ctx.args) != 1:
        ctx.controller.push_session_message("/import requires a .jsonl path", level="warn")
        return
    try:
        path = _jsonl_relative_path(ctx.args[0], verb="/import")
    except ValueError as exc:
        ctx.controller.push_session_message(
            str(exc),
            level="warn",
        )
        return
    try:
        session = runtime.import_jsonl(path, allowed_root=runtime.cwd)
    except (RuntimeError, ValueError, OSError) as exc:
        ctx.controller.push_session_message(str(exc), level="error")
        return
    ctx.controller.refresh_after_session_switch(
        f"imported session {short_session_id(session.id)}"
    )


def _jsonl_relative_path(value: str, *, verb: str) -> Path:
    path = Path(value)
    if path.suffix != ".jsonl":
        raise ValueError(
            f"{verb} only supports .jsonl; structured envelope import is out of scope"
        )
    if value.startswith("~"):
        raise ValueError(f"{verb} path must be relative to the current workspace")
    if path.is_absolute():
        raise ValueError(f"{verb} path must be relative to the current workspace")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"{verb} path must stay inside the current workspace")
    return path


def _export_relative_path(value: str) -> Path:
    path = _workspace_relative_path(value, verb="/export")
    if _export_format(path) is None:
        raise ValueError(
            "unsupported export format; supported extensions: "
            f"{_SUPPORTED_EXPORT_FORMATS}"
        )
    return path


def _workspace_relative_path(value: str, *, verb: str) -> Path:
    path = Path(value)
    if value.startswith("~"):
        raise ValueError(f"{verb} path must be relative to the current workspace")
    if path.is_absolute():
        raise ValueError(f"{verb} path must be relative to the current workspace")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"{verb} path must stay inside the current workspace")
    return path


def _export_format(path: Path) -> str | None:
    name = path.name
    if name.endswith(".pi.jsonl"):
        return "pi_jsonl"
    if name.endswith(".session.json"):
        return "session_json"
    if path.suffix == ".jsonl":
        return "jsonl"
    if path.suffix == ".html":
        return "html"
    return None


def _dispatch_export(runtime, path: Path) -> Path:
    export_format = _export_format(path)
    if export_format == "jsonl":
        return runtime.export_jsonl(path, allowed_root=runtime.cwd)
    if export_format == "pi_jsonl":
        return runtime.export_pi_jsonl(path, allowed_root=runtime.cwd)
    if export_format == "session_json":
        return runtime.export_structured_json(path, allowed_root=runtime.cwd)
    if export_format == "html":
        return runtime.export_html(path, allowed_root=runtime.cwd)
    raise ValueError(
        "unsupported export format; supported extensions: "
        f"{_SUPPORTED_EXPORT_FORMATS}"
    )


__all__ = ["handle_export", "handle_import"]

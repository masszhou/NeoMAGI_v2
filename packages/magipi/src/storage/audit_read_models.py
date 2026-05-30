"""Safe read models for dashboard audit surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class AuditDashboardRow:
    id: str
    session_id: str
    short_id: str
    event_type: str
    actor_type: str
    tool: str
    effect: str
    subject: str
    metric: str
    cwd: str | None
    is_error: bool
    redaction_status: str
    occurred_at: str
    age_seconds: int | None
    duration_ms: int | None
    tool_execution_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "short_id": self.short_id,
            "event_type": self.event_type,
            "actor_type": self.actor_type,
            "tool": self.tool,
            "effect": self.effect,
            "subject": self.subject,
            "metric": self.metric,
            "cwd": self.cwd,
            "is_error": self.is_error,
            "redaction_status": self.redaction_status,
            "occurred_at": self.occurred_at,
            "age_seconds": self.age_seconds,
            "duration_ms": self.duration_ms,
            "tool_execution_id": self.tool_execution_id,
        }


def shape_audit_dashboard_row(
    *,
    event_id: str,
    session_id: str,
    event_type: str,
    actor_type: str,
    action: str,
    decision: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
    occurred_at: str,
    age_seconds: int | None = None,
    cwd: str | None = None,
    tool_execution_id: str | None = None,
) -> AuditDashboardRow:
    """Map durable audit metadata into the safe dashboard list row."""

    meta = dict(metadata or {})
    args = _mapping(meta.get("args"))
    tool = str(action or _string(_mapping(meta.get("target")).get("toolName")) or "unknown")
    is_error = bool(meta.get("isError") is True)
    redaction_status = str(meta.get("redactionStatus") or "not_required")
    duration_ms = _int(meta.get("durationMs"))
    effect = _effect(decision)
    return AuditDashboardRow(
        id=event_id,
        session_id=session_id,
        short_id=event_id[:8],
        event_type=event_type,
        actor_type=actor_type,
        tool=tool,
        effect=effect,
        subject=_subject(tool, args),
        metric=_metric(tool, args, meta, duration_ms, is_error),
        cwd=_string(args.get("cwd")) or cwd,
        is_error=is_error,
        redaction_status=redaction_status,
        occurred_at=occurred_at,
        age_seconds=age_seconds,
        duration_ms=duration_ms,
        tool_execution_id=tool_execution_id,
    )


def _subject(tool: str, args: Mapping[str, Any]) -> str:
    if tool == "bash":
        command = _string(args.get("commandPreview")) or _string(args.get("command"))
        if not command:
            return "-"
        length = _int(args.get("commandLength"))
        if length is not None and length > len(command):
            return f"{command} ... (full {length} chars)"
        return command
    if tool in {"read", "write", "edit"}:
        return _string(args.get("path")) or "-"
    if tool == "log_experiment":
        return _truncate(_string(args.get("hypothesis")) or "-", 96)
    if tool == "run_experiment":
        return _string(args.get("command")) or _string(args.get("commandPreview")) or "-"
    if tool == "init_experiment":
        return _string(args.get("workspace")) or "-"
    if args:
        return "args hidden; unsupported tool mapping"
    return "-"


def _metric(
    tool: str,
    args: Mapping[str, Any],
    metadata: Mapping[str, Any],
    duration_ms: int | None,
    is_error: bool,
) -> str:
    builder = _METRIC_BUILDERS.get(tool)
    parts = builder(args, metadata, duration_ms) if builder else []
    if is_error:
        parts.append("ERR")
    return " · ".join(parts) if parts else "-"


def _metric_bash(
    args: Mapping[str, Any], metadata: Mapping[str, Any], duration_ms: int | None
) -> list[str]:
    parts: list[str] = []
    if duration_ms is not None:
        parts.append(f"{duration_ms}ms")
    output_lines = _output_lines(metadata)
    if output_lines is not None:
        parts.append(f"{output_lines} lines")
    return parts


def _metric_read(
    args: Mapping[str, Any], metadata: Mapping[str, Any], duration_ms: int | None
) -> list[str]:
    return _limit_offset(args)


def _metric_write(
    args: Mapping[str, Any], metadata: Mapping[str, Any], duration_ms: int | None
) -> list[str]:
    bytes_value = _int(args.get("contentBytes"))
    return [f"{bytes_value} bytes"] if bytes_value is not None else []


def _metric_edit(
    args: Mapping[str, Any], metadata: Mapping[str, Any], duration_ms: int | None
) -> list[str]:
    parts: list[str] = []
    edits = args.get("edits")
    edits_count = len(edits) if isinstance(edits, list) else _int(args.get("editsCount"))
    if edits_count is not None:
        parts.append(f"{edits_count} edit(s)")
    new_bytes = _int(args.get("newTextBytes"))
    old_bytes = _int(args.get("oldTextBytes"))
    if new_bytes is not None or old_bytes is not None:
        parts.append(f"+{new_bytes or 0}/-{old_bytes or 0} bytes")
    return parts


def _metric_log_experiment(
    args: Mapping[str, Any], metadata: Mapping[str, Any], duration_ms: int | None
) -> list[str]:
    decision = _string(args.get("decision")) or _string(args.get("status"))
    return [f"decision={decision}"] if decision else []


def _metric_run_experiment(
    args: Mapping[str, Any], metadata: Mapping[str, Any], duration_ms: int | None
) -> list[str]:
    return [f"{duration_ms}ms"] if duration_ms is not None else []


_METRIC_BUILDERS: Mapping[
    str, Callable[[Mapping[str, Any], Mapping[str, Any], int | None], list[str]]
] = {
    "bash": _metric_bash,
    "read": _metric_read,
    "write": _metric_write,
    "edit": _metric_edit,
    "log_experiment": _metric_log_experiment,
    "run_experiment": _metric_run_experiment,
}


def _effect(decision: Mapping[str, Any] | None) -> str:
    if not decision:
        return "unknown"
    for key in ("effect", "decision", "status"):
        value = decision.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _output_lines(metadata: Mapping[str, Any]) -> int | None:
    truncation = _mapping(metadata.get("truncation"))
    for key in ("outputLines", "lines"):
        value = _int(truncation.get(key))
        if value is not None:
            return value
    return None


def _limit_offset(args: Mapping[str, Any]) -> list[str]:
    parts = []
    limit = _int(args.get("limit"))
    offset = _int(args.get("offset"))
    if limit is not None:
        parts.append(f"limit {limit}")
    if offset is not None:
        parts.append(f"offset {offset}")
    return parts


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


__all__ = ["AuditDashboardRow", "shape_audit_dashboard_row"]

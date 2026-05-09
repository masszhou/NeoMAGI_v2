"""Structured local projections for durable sessions."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ai_provider.types import AssistantMessage
from cli.core.session_types import (
    MessageEntry,
    SessionEntryAdapter,
    SessionHeader,
    SessionHeaderAdapter,
)
from policy.redaction import RedactionReport, redact_for_export
from storage.audit_queries import SessionAuditEventRecord
from storage.session_repository import (
    EntryRecord,
    SessionRecord,
    SessionRepository,
    ToolExecutionRecord,
)

SESSION_EXPORT_TYPE = "neomagi.session_export"
SESSION_EXPORT_SCHEMA_VERSION = 1


class SessionExportError(ValueError):
    """Raised when a structured export cannot be built or written."""


class _ExportModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ExportDiagnostic(_ExportModel):
    rule_id: str = Field(alias="ruleId")
    message: str
    severity: Literal["info", "warn", "error"] = "info"
    path: str | None = None


class ExportSource(_ExportModel):
    app: str = "NeoMAGI_v2"
    milestone: str = "P1-M10"
    pi_session_version: int = Field(alias="piSessionVersion")
    notes: list[str] = Field(default_factory=list)


class ExportPiData(_ExportModel):
    header: dict[str, Any]
    entries: list[dict[str, Any]]
    leaf_id: str | None = Field(default=None, alias="leafId")
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    tools: list[dict[str, Any]] | None = None
    rendered_tools: dict[str, Any] | None = Field(default=None, alias="renderedTools")


class ExportSessionMetadata(_ExportModel):
    id: str
    cwd: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    name: str | None = None
    parent_session: str | None = Field(default=None, alias="parentSession")
    current_leaf: str | None = Field(default=None, alias="currentLeaf")
    provider_cache_affinity_id: str = Field(alias="providerCacheAffinityId")
    provider: str | None = None
    model_id: str | None = Field(default=None, alias="modelId")
    thinking_level: str | None = Field(default=None, alias="thinkingLevel")
    source: dict[str, Any] = Field(default_factory=dict)


class ExportUsageCost(_ExportModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = Field(default=0.0, alias="cacheRead")
    cache_write: float = Field(default=0.0, alias="cacheWrite")
    total: float = 0.0


class ExportUsageSummary(_ExportModel):
    input: int = 0
    output: int = 0
    cache_read: int = Field(default=0, alias="cacheRead")
    cache_write: int = Field(default=0, alias="cacheWrite")
    total_tokens: int = Field(default=0, alias="totalTokens")
    cost: ExportUsageCost | None = None


class ExportToolExecution(_ExportModel):
    id: str
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: Any = None
    result_content: Any = Field(default=None, alias="resultContent")
    result_details: Any = Field(default=None, alias="resultDetails")
    is_error: bool | None = Field(default=None, alias="isError")
    started_at: str | None = Field(default=None, alias="startedAt")
    ended_at: str | None = Field(default=None, alias="endedAt")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    truncation: Any = None
    policy_decision: Any = Field(default=None, alias="policyDecision")
    sandbox: Any = None
    runtime_session_id: str | None = Field(default=None, alias="runtimeSessionId")
    run_id: str | None = Field(default=None, alias="runId")
    affected_paths: list[str] = Field(default_factory=list, alias="affectedPaths")
    full_output_path: str | None = Field(default=None, alias="fullOutputPath")
    redaction_status: str | None = Field(default=None, alias="redactionStatus")
    redaction_tags: list[str] = Field(default_factory=list, alias="redactionTags")
    exception_class: str | None = Field(default=None, alias="exceptionClass")
    exception_message: str | None = Field(default=None, alias="exceptionMessage")
    metadata_source: Literal["typed", "typed+audit", "legacy_result_details"] = Field(
        default="typed",
        alias="metadataSource",
    )


class ExportAnalytics(_ExportModel):
    tool_executions: list[ExportToolExecution] = Field(
        default_factory=list,
        alias="toolExecutions",
    )
    usage: ExportUsageSummary = Field(default_factory=ExportUsageSummary)


class ExportRedactionRule(_ExportModel):
    id: str
    count: int
    paths: list[str] = Field(default_factory=list)


class ExportRedactionSummary(_ExportModel):
    status: Literal["not_required", "applied", "partial", "failed"]
    rules: list[ExportRedactionRule] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ExportNeoMAGIData(_ExportModel):
    session: ExportSessionMetadata
    active_path: list[str] = Field(default_factory=list, alias="activePath")
    analytics: ExportAnalytics = Field(default_factory=ExportAnalytics)
    redaction: ExportRedactionSummary
    diagnostics: list[ExportDiagnostic] = Field(default_factory=list)


class SessionExportEnvelope(_ExportModel):
    type: Literal["neomagi.session_export"] = SESSION_EXPORT_TYPE
    schema_version: Literal[1] = Field(default=SESSION_EXPORT_SCHEMA_VERSION, alias="schemaVersion")
    generated_at: str = Field(alias="generatedAt")
    source: ExportSource
    pi: ExportPiData
    neomagi: ExportNeoMAGIData


SessionExportEnvelopeAdapter = TypeAdapter(SessionExportEnvelope)


def validate_session_export_envelope(payload: dict[str, Any]) -> SessionExportEnvelope:
    version = payload.get("schemaVersion")
    if version != SESSION_EXPORT_SCHEMA_VERSION:
        raise SessionExportError(f"unsupported session export schemaVersion: {version}")
    return SessionExportEnvelopeAdapter.validate_python(payload)


def build_session_export_envelope(
    repository: SessionRepository,
    session_id: str,
    *,
    clock: Callable[[], datetime] | None = None,
) -> SessionExportEnvelope:
    session = _require_session(repository, session_id)
    generated_at = _generated_at(clock)
    entries = repository.list_entries(session.id)
    active_path = _entry_path(entries, session.current_leaf_entry_id)
    report = RedactionReport()
    diagnostics: list[ExportDiagnostic] = []

    raw_header = session.header().model_dump(by_alias=True, exclude_none=True)
    header, _ = redact_for_export(raw_header, cwd=session.cwd, report=report)
    SessionHeaderAdapter.validate_python(header)

    pi_entries = [_redacted_entry(entry, session.cwd, report) for entry in entries]
    for entry in pi_entries:
        SessionEntryAdapter.validate_python(entry)

    context_state = _derive_context_state(active_path)
    tool_executions = _export_tool_executions(repository, session, report, diagnostics)
    usage = _usage_summary(entries, diagnostics)
    redacted_source = _redacted_value(session.source, session.cwd, report)

    if report.counts:
        diagnostics.append(
            ExportDiagnostic(
                ruleId="redaction_applied",
                message="one or more export fields were redacted",
            )
        )

    envelope = SessionExportEnvelope(
        generatedAt=generated_at,
        source=ExportSource(
            piSessionVersion=SessionHeaderAdapter.validate_python(raw_header).version,
            notes=[
                ".jsonl exports are NeoMAGI full-tree projections",
                ".pi.jsonl exports are active-branch Pi projections",
            ],
        ),
        pi=ExportPiData(
            header=header,
            entries=pi_entries,
            leafId=active_path[-1].pi_export_id if active_path else None,
        ),
        neomagi=ExportNeoMAGIData(
            session=ExportSessionMetadata(
                id=session.id,
                cwd=session.cwd,
                createdAt=session.created_at,
                updatedAt=session.updated_at,
                name=session.display_name,
                parentSession=raw_header.get("parentSession"),
                currentLeaf=active_path[-1].pi_export_id if active_path else None,
                providerCacheAffinityId=session.provider_cache_affinity_id,
                provider=context_state["provider"],
                modelId=context_state["modelId"],
                thinkingLevel=context_state["thinkingLevel"],
                source=redacted_source,
            ),
            activePath=[entry.pi_export_id for entry in active_path],
            analytics=ExportAnalytics(toolExecutions=tool_executions, usage=usage),
            redaction=_redaction_summary(report),
            diagnostics=diagnostics,
        ),
    )
    return envelope


def export_session_structured_json(
    repository: SessionRepository,
    session_id: str,
    path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Path:
    target = _resolve_export_path(path, allowed_root=allowed_root)
    if not target.name.endswith(".session.json"):
        raise SessionExportError("structured session export path must end with .session.json")
    envelope = build_session_export_envelope(repository, session_id, clock=clock)
    _write_json(target, envelope.model_dump(by_alias=True, exclude_none=False))
    return target


def export_session_pi_jsonl(
    repository: SessionRepository,
    session_id: str,
    path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Path:
    target = _resolve_export_path(path, allowed_root=allowed_root)
    if not target.name.endswith(".pi.jsonl"):
        raise SessionExportError("Pi branch session export path must end with .pi.jsonl")
    session = _require_session(repository, session_id)
    entries = repository.list_entries(session.id)
    active_path = _entry_path(entries, session.current_leaf_entry_id)
    report = RedactionReport()
    header = SessionHeader(
        id=session.id,
        timestamp=_generated_at(clock),
        cwd=session.cwd,
    ).model_dump(by_alias=True, exclude_none=True)
    SessionHeaderAdapter.validate_python(header)
    lines = [json.dumps(header, ensure_ascii=False, separators=(",", ":"))]
    previous_id: str | None = None
    for entry in active_path:
        payload = _redacted_entry(entry, session.cwd, report)
        payload["parentId"] = previous_id
        SessionEntryAdapter.validate_python(payload)
        lines.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        previous_id = payload["id"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def export_session_html(
    repository: SessionRepository,
    session_id: str,
    path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Path:
    target = _resolve_export_path(path, allowed_root=allowed_root)
    if target.suffix != ".html":
        raise SessionExportError("HTML session export path must end with .html")
    envelope = build_session_export_envelope(repository, session_id, clock=clock)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_html(envelope), encoding="utf-8")
    return target


def _require_session(repository: SessionRepository, session_id: str) -> SessionRecord:
    session = repository.get_session(session_id)
    if session is None:
        raise SessionExportError(f"unknown session: {session_id}")
    return session


def _resolve_export_path(path: str | Path, *, allowed_root: str | Path | None) -> Path:
    raw = Path(path).expanduser()
    if allowed_root is None:
        return raw
    try:
        root = Path(allowed_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise SessionExportError(f"session export allowed root is unavailable: {allowed_root}") from exc
    if not root.is_dir():
        raise SessionExportError(f"session export allowed root is not a directory: {allowed_root}")
    target = raw.resolve(strict=False) if raw.is_absolute() else (root / raw).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SessionExportError(f"session export path escapes allowed root: {path}") from exc
    return target


def _write_json(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _generated_at(clock: Callable[[], datetime] | None) -> str:
    now = clock() if clock is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC).replace(microsecond=0).isoformat()


def _redacted_entry(
    entry: EntryRecord,
    cwd: str,
    report: RedactionReport,
) -> dict[str, Any]:
    payload = _entry_payload_for_export(entry)
    return _redacted_value(payload, cwd, report)


def _entry_payload_for_export(entry: EntryRecord) -> dict[str, Any]:
    payload = entry.payload.model_dump(by_alias=True, exclude_none=True)
    if isinstance(entry.payload, MessageEntry) and isinstance(entry.payload.message, AssistantMessage):
        if not _usage_cost_was_explicit(entry.payload.message):
            payload.get("message", {}).get("usage", {}).pop("cost", None)
    return payload


def _redacted_value(value: Any, cwd: str, report: RedactionReport) -> Any:
    redacted, _ = redact_for_export(value, cwd=cwd, report=report)
    return redacted


def _entry_path(entries: list[EntryRecord], leaf_entry_id: str | None) -> list[EntryRecord]:
    if leaf_entry_id is None:
        return []
    by_pi = {entry.pi_export_id: entry for entry in entries}
    by_db = {entry.id: entry for entry in entries}
    leaf = by_pi.get(leaf_entry_id) or by_db.get(leaf_entry_id)
    if leaf is None:
        raise SessionExportError(f"unknown current leaf entry: {leaf_entry_id}")
    path: list[EntryRecord] = []
    cursor: EntryRecord | None = leaf
    seen: set[str] = set()
    while cursor is not None:
        if cursor.pi_export_id in seen:
            raise SessionExportError("session entry tree contains a cycle")
        seen.add(cursor.pi_export_id)
        path.append(cursor)
        cursor = by_pi.get(cursor.payload.parent_id) if cursor.payload.parent_id else None
    path.reverse()
    return path


def _derive_context_state(path: list[EntryRecord]) -> dict[str, str | None]:
    state: dict[str, str | None] = {
        "provider": None,
        "modelId": None,
        "thinkingLevel": None,
    }
    for entry in path:
        payload = entry.payload
        if payload.type == "model_change":
            state["provider"] = payload.provider
            state["modelId"] = payload.model_id
        elif payload.type == "thinking_level_change":
            state["thinkingLevel"] = payload.thinking_level
    return state


def _export_tool_executions(
    repository: SessionRepository,
    session: SessionRecord,
    report: RedactionReport,
    diagnostics: list[ExportDiagnostic],
) -> list[ExportToolExecution]:
    records = repository.list_tool_executions(session.id)
    audit_events = _audit_events_by_tool(repository, session.id)
    exported: list[ExportToolExecution] = []
    for record in records:
        audit = _matching_audit(record, audit_events)
        metadata = audit.metadata if audit is not None else {}
        partial = audit is None or (
            record.truncation is None
            and record.policy_decision is None
            and record.sandbox is None
            and not metadata
        )
        if partial:
            diagnostics.append(
                ExportDiagnostic(
                    ruleId="tool_execution_metadata_partial",
                    message=f"tool execution metadata is partial for {record.tool_call_id}",
                    severity="warn",
                    path=f"neomagi.analytics.toolExecutions.{record.tool_call_id}",
                )
            )
        redacted_metadata = _redacted_value(metadata, session.cwd, report)
        exported.append(
            ExportToolExecution(
                id=record.id,
                toolCallId=record.tool_call_id,
                toolName=record.tool_name,
                args=_redacted_value(record.args, session.cwd, report),
                resultContent=_redacted_value(record.result_content, session.cwd, report),
                resultDetails=_redacted_value(record.result_details, session.cwd, report),
                isError=record.is_error,
                startedAt=record.started_at,
                endedAt=record.ended_at,
                durationMs=record.duration_ms,
                truncation=_redacted_value(record.truncation, session.cwd, report),
                policyDecision=_redacted_value(record.policy_decision, session.cwd, report),
                sandbox=_redacted_value(record.sandbox, session.cwd, report),
                runtimeSessionId=record.runtime_session_id,
                runId=record.run_id,
                affectedPaths=_string_list(redacted_metadata.get("affectedPaths")),
                fullOutputPath=_string_or_none(redacted_metadata.get("fullOutputPath")),
                redactionStatus=_string_or_none(redacted_metadata.get("redactionStatus")),
                redactionTags=_string_list(redacted_metadata.get("redactionTags")),
                exceptionClass=_string_or_none(redacted_metadata.get("exceptionClass")),
                exceptionMessage=_string_or_none(redacted_metadata.get("exceptionMessage")),
                metadataSource="typed+audit" if audit is not None else "legacy_result_details",
            )
        )
    if audit_events:
        matched_ids = {item.id for item in exported}
        for event in audit_events:
            tool_id = event.tool_execution_id
            if tool_id is not None and tool_id not in matched_ids:
                diagnostics.append(
                    ExportDiagnostic(
                        ruleId="tool_execution_unmatched",
                        message=f"audit event has no matching tool execution: {tool_id}",
                        severity="warn",
                    )
                )
    return exported


def _audit_events_by_tool(
    repository: SessionRepository,
    session_id: str,
) -> list[SessionAuditEventRecord]:
    try:
        return repository.list_audit_events(session_id)
    except AttributeError:
        return []


def _matching_audit(
    record: ToolExecutionRecord,
    events: list[SessionAuditEventRecord],
) -> SessionAuditEventRecord | None:
    for event in events:
        if event.tool_execution_id == record.id:
            return event
    for event in events:
        target = event.target or {}
        metadata = event.metadata or {}
        if target.get("toolCallId") != record.tool_call_id:
            continue
        if record.runtime_session_id and metadata.get("runtimeSessionId") not in {
            None,
            record.runtime_session_id,
        }:
            continue
        if record.run_id and metadata.get("runId") not in {None, record.run_id}:
            continue
        return event
    return None


def _usage_summary(
    entries: list[EntryRecord],
    diagnostics: list[ExportDiagnostic],
) -> ExportUsageSummary:
    usage = ExportUsageSummary(cost=ExportUsageCost())
    saw_cost = False
    saw_assistant = False
    missing_cost = False
    for entry in entries:
        payload = entry.payload
        if not isinstance(payload, MessageEntry):
            continue
        message = payload.message
        if not isinstance(message, AssistantMessage):
            continue
        saw_assistant = True
        usage.input += message.usage.input
        usage.output += message.usage.output
        usage.cache_read += message.usage.cache_read
        usage.cache_write += message.usage.cache_write
        usage.total_tokens += message.usage.total_tokens
        cost = message.usage.cost
        if cost is not None and _usage_cost_was_explicit(message):
            saw_cost = True
            if usage.cost is not None:
                usage.cost.input += cost.input
                usage.cost.output += cost.output
                usage.cost.cache_read += cost.cache_read
                usage.cost.cache_write += cost.cache_write
                usage.cost.total += cost.total
        else:
            missing_cost = True
    if missing_cost or not saw_cost:
        usage.cost = None
        if saw_assistant:
            diagnostics.append(
                ExportDiagnostic(
                    ruleId="usage_cost_unavailable",
                    message="assistant usage has no cost metadata",
                    severity="warn",
                )
            )
    return usage


def _usage_cost_was_explicit(message: AssistantMessage) -> bool:
    return "cost" in getattr(message.usage, "model_fields_set", set())


def _redaction_summary(report: RedactionReport) -> ExportRedactionSummary:
    rules = [
        ExportRedactionRule(
            id=rule_id,
            count=count,
            paths=report.paths.get(rule_id, []),
        )
        for rule_id, count in sorted(report.counts.items())
    ]
    return ExportRedactionSummary(
        status=report.status,
        rules=rules,
        counts=dict(sorted(report.counts.items())),
    )


def _render_html(envelope: SessionExportEnvelope) -> str:
    payload = envelope.model_dump(by_alias=True, exclude_none=False)
    session_data = payload["pi"]
    session_data_b64 = base64.b64encode(
        json.dumps(session_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    envelope_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace(
        "<",
        "\\u003c",
    )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>NeoMAGI Session Export</title>",
            "<style>",
            _HTML_CSS,
            "</style>",
            "</head>",
            "<body>",
            "<main>",
            f"<h1>{escape(envelope.neomagi.session.name or 'NeoMAGI Session Export')}</h1>",
            _render_summary(envelope),
            _render_messages(envelope.pi.entries),
            _render_tools(envelope.neomagi.analytics.tool_executions),
            _render_diagnostics(envelope.neomagi.diagnostics),
            "</main>",
            (
                '<script id="pi-session-data" type="application/json" '
                f'data-base64="{session_data_b64}"></script>'
            ),
            f'<script id="neomagi-export" type="application/json">{envelope_json}</script>',
            "</body>",
            "</html>",
            "",
        ]
    )


def _render_summary(envelope: SessionExportEnvelope) -> str:
    session = envelope.neomagi.session
    usage = envelope.neomagi.analytics.usage
    cost = "unavailable" if usage.cost is None else f"{usage.cost.total:.6f}"
    rows = (
        ("Session", session.id),
        ("Generated", envelope.generated_at),
        ("CWD", session.cwd),
        ("Current leaf", session.current_leaf or "none"),
        ("Model", _format_model_ref(session.provider, session.model_id)),
        ("Thinking", session.thinking_level or "none"),
        ("Tokens", str(usage.total_tokens)),
        ("Cost", cost),
        ("Redaction", envelope.neomagi.redaction.status),
    )
    body = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in rows
    )
    return f"<section><h2>Summary</h2><table>{body}</table></section>"


def _render_messages(entries: list[dict[str, Any]]) -> str:
    parts = ["<section><h2>Timeline</h2>"]
    for entry in entries:
        if entry.get("type") != "message":
            parts.append(
                '<article class="entry">'
                f'<div class="meta">{escape(str(entry.get("type")))} · '
                f'{escape(str(entry.get("id")))}</div>'
                f'<pre>{escape(json.dumps(entry, ensure_ascii=False, indent=2))}</pre>'
                "</article>"
            )
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        role = str(message.get("role", "message"))
        parts.append(
            f'<article class="entry {escape(role)}">'
            f'<div class="meta">{escape(role)} · {escape(str(entry.get("id")))}</div>'
            f"<pre>{escape(_message_text(message))}</pre>"
            "</article>"
        )
    parts.append("</section>")
    return "".join(parts)


def _render_tools(tools: list[ExportToolExecution]) -> str:
    if not tools:
        return "<section><h2>Tool Executions</h2><p>No tool executions.</p></section>"
    rows = []
    for tool in tools:
        rows.append(
            "<tr>"
            f"<td>{escape(tool.tool_call_id)}</td>"
            f"<td>{escape(tool.tool_name)}</td>"
            f"<td>{escape(str(tool.is_error))}</td>"
            f"<td>{escape(str(tool.duration_ms if tool.duration_ms is not None else ''))}</td>"
            f"<td><pre>{escape(json.dumps(tool.args, ensure_ascii=False, indent=2))}</pre></td>"
            f"<td><pre>{escape(json.dumps(tool.result_details, ensure_ascii=False, indent=2))}</pre></td>"
            "</tr>"
        )
    return (
        "<section><h2>Tool Executions</h2><table>"
        "<thead><tr><th>Call</th><th>Name</th><th>Error</th><th>Duration</th>"
        "<th>Args</th><th>Result Details</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _render_diagnostics(diagnostics: list[ExportDiagnostic]) -> str:
    if not diagnostics:
        return "<section><h2>Diagnostics</h2><p>No diagnostics.</p></section>"
    items = "".join(
        "<li>"
        f"<strong>{escape(item.rule_id)}</strong>: {escape(item.message)}"
        f"{' (' + escape(item.path) + ')' if item.path else ''}"
        "</li>"
        for item in diagnostics
    )
    return f"<section><h2>Diagnostics</h2><ul>{items}</ul></section>"


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
            elif isinstance(item, dict) and item.get("type") == "toolCall":
                texts.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(texts)
    return json.dumps(message, ensure_ascii=False, indent=2)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _join_non_empty(*values: str | None) -> str:
    return "/".join(value for value in values if value)


def _format_model_ref(provider: str | None, model_id: str | None) -> str:
    """Render the canonical ``vendor/auth-channel/model-id`` model ref.

    Sessions persist ``(provider, model_id)``; reverse-look up the user-visible
    ``(vendor, auth-channel)`` for display so the summary matches the runtime
    footer and ``/model list``.
    """

    if not provider or not model_id:
        return "none"
    from ai_provider.model_registry import provider_auth_info

    vendor, auth_channel = provider_auth_info(provider)
    return f"{vendor}/{auth_channel}/{model_id}"


_HTML_CSS = """
:root { color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
body { margin: 0; background: #f6f7f9; color: #1f2933; }
main { max-width: 1080px; margin: 0 auto; padding: 32px 20px 48px; }
h1 { margin: 0 0 24px; font-size: 28px; font-weight: 650; }
h2 { margin: 28px 0 12px; font-size: 18px; }
section { margin: 0 0 20px; }
table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dde6; }
th, td { text-align: left; vertical-align: top; padding: 8px 10px; border-bottom: 1px solid #e4e8ef; }
th { width: 180px; color: #52606d; font-weight: 600; }
.entry { background: #fff; border: 1px solid #d8dde6; margin: 10px 0; padding: 10px 12px; }
.meta { color: #687788; font-size: 12px; margin-bottom: 6px; }
pre { white-space: pre-wrap; word-break: break-word; margin: 0; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
@media (prefers-color-scheme: dark) {
  body { background: #111827; color: #e5e7eb; }
  table, .entry { background: #18212f; border-color: #2f3b4c; }
  th, td { border-bottom-color: #2f3b4c; }
  th, .meta { color: #a8b3c1; }
}
"""


__all__ = [
    "ExportDiagnostic",
    "SessionExportEnvelope",
    "SessionExportEnvelopeAdapter",
    "SessionExportError",
    "build_session_export_envelope",
    "export_session_html",
    "export_session_pi_jsonl",
    "export_session_structured_json",
    "validate_session_export_envelope",
]

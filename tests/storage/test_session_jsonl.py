from __future__ import annotations

import json
from pathlib import Path

import pytest
from ai_provider.types import TextContent, ToolResultMessage, UserMessage
from cli.core.session_manager import SessionManager
from storage.in_memory_session_repository import InMemorySessionRepository
import storage.session_jsonl as session_jsonl
from storage.session_jsonl import SessionJsonlError


def test_session_jsonl_exports_and_imports_round_trip(tmp_path: Path) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="persist me")], timestamp=1),
    )
    path = tmp_path / "session.jsonl"

    manager.export_jsonl(session.id, path)
    imported = manager.import_jsonl(path)

    imported_context = manager.build_session_context(imported.id)
    assert imported.id != session.id
    assert imported_context.messages[0].role == "user"
    assert imported_context.messages[0].content[0].text == "persist me"


def test_session_jsonl_round_trip_preserves_resolvable_parent_session(
    tmp_path: Path,
) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    source = manager.new_session(tmp_path)
    manager.append_message(
        source.id,
        UserMessage(content=[TextContent(text="parent context")], timestamp=1),
    )
    cloned = manager.clone_session(source.id)
    path = tmp_path / "child.jsonl"

    manager.export_jsonl(cloned.session.id, path)
    imported = manager.import_jsonl(path)

    assert imported.parent_session_id == source.id
    assert imported.source["sourceHeaderId"] == cloned.session.id


def test_session_jsonl_round_trip_preserves_fork_parent_session(
    tmp_path: Path,
) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    source = manager.new_session(tmp_path)
    user = manager.append_message(
        source.id,
        UserMessage(content=[TextContent(text="fork me")], timestamp=1),
    )
    forked = manager.fork_session(source.id, user.pi_export_id)
    path = tmp_path / "forked.jsonl"

    manager.export_jsonl(forked.session.id, path)
    imported = manager.import_jsonl(path)

    assert imported.parent_session_id == source.id
    assert imported.source["sourceHeaderId"] == forked.session.id


def test_session_jsonl_round_trip_preserves_read_tool_line_details(
    tmp_path: Path,
) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        ToolResultMessage(
            toolCallId="call-read",
            toolName="read",
            content=[TextContent(text="one\ntwo")],
            details={
                "path": "README.md",
                "lineStart": 1,
                "lineEnd": 2,
                "totalLines": 4,
                "outputLines": 2,
            },
            isError=False,
            timestamp=1,
        ),
    )
    path = tmp_path / "session.jsonl"

    manager.export_jsonl(session.id, path)
    imported = manager.import_jsonl(path)

    imported_context = manager.build_session_context(imported.id)
    message = imported_context.messages[0]
    assert isinstance(message, ToolResultMessage)
    assert message.details["lineStart"] == 1
    assert message.details["lineEnd"] == 2
    assert message.details["totalLines"] == 4
    assert message.details["outputLines"] == 2


def test_session_jsonl_allowed_root_blocks_path_escape(tmp_path: Path) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="persist me")], timestamp=1),
    )

    exported = manager.export_jsonl(session.id, "session.jsonl", allowed_root=tmp_path)

    assert exported == tmp_path / "session.jsonl"
    with pytest.raises(SessionJsonlError, match="escapes allowed root"):
        manager.export_jsonl(session.id, "../escape.jsonl", allowed_root=tmp_path)
    with pytest.raises(SessionJsonlError, match="escapes allowed root"):
        manager.import_jsonl(tmp_path.parent / "session.jsonl", allowed_root=tmp_path)


def test_session_jsonl_export_preserves_existing_target_on_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="new content")], timestamp=1),
    )
    target = tmp_path / "session.jsonl"
    target.write_text("old content\n", encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(session_jsonl.os, "replace", fail_replace)

    with pytest.raises(SessionJsonlError, match="failed to write session JSONL"):
        manager.export_jsonl(session.id, target)

    assert target.read_text(encoding="utf-8") == "old content\n"
    assert not list(tmp_path.glob(".session.jsonl.*.tmp"))


def test_session_jsonl_legacy_compaction_summary_from_id_reads_but_does_not_export(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session",
                        "version": 3,
                        "id": "legacy",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "cwd": str(tmp_path),
                    },
                    separators=(",", ":"),
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "entry-1",
                        "timestamp": "2026-01-01T00:00:01+00:00",
                        "message": {
                            "role": "compactionSummary",
                            "summary": "old",
                            "tokensBefore": 10,
                            "fromId": "legacy-extra",
                            "timestamp": 1,
                        },
                    },
                    separators=(",", ":"),
                ),
            ]
        ),
        encoding="utf-8",
    )
    manager = SessionManager(InMemorySessionRepository())

    imported = manager.import_jsonl(source)
    exported = manager.export_jsonl(imported.id, tmp_path / "exported.jsonl")

    assert '"role":"compactionSummary"' in exported.read_text(encoding="utf-8")
    assert '"fromId"' not in exported.read_text(encoding="utf-8")

from __future__ import annotations

import asyncio
from pathlib import Path

from ai_provider.model_registry import get_model
from ai_provider.providers.faux import faux_assistant_message
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.streaming import AssistantMessageEventStream
from ai_provider.types import StreamDone, TextContent, UserMessage
from cli.core.compaction.service import CompactionService, ProviderSummaryGenerator
from cli.core.compaction.settings import BranchSummarySettings
from cli.core.session_manager import SessionManager
from cli.tools.context import convert_coding_messages_to_llm
from storage.in_memory_session_repository import InMemorySessionRepository


SUMMARY = """## Goal
Continue the task.
## Constraints & Preferences
Keep repo boundaries.
## Progress
### Done
Older context summarized.
### In Progress
Current work.
### Blocked
None.
## Key Decisions
Use durable session summaries.
## Next Steps
Continue.
## Critical Context
Important details.
<read-files>
</read-files>
<modified-files>
</modified-files>"""


class FakeSummaryGenerator:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, *, model) -> str:
        self.prompts.append(prompt)
        return SUMMARY


def _summary_stream(text: str):
    def stream_fn(_model, _context, options: SimpleStreamOptions | None = None):
        stream_fn.options.append(options)
        stream = AssistantMessageEventStream()
        message = faux_assistant_message(text, get_model("faux", "faux-1"))
        stream.push(StreamDone(reason="stop", message=message))
        return stream

    stream_fn.options = []
    return stream_fn


def _service(manager: SessionManager, generator: FakeSummaryGenerator) -> CompactionService:
    return CompactionService(
        manager=manager,
        model=get_model("faux", "faux-1"),
        generator=generator,
    )


def _service_with_branch_skip(
    manager: SessionManager,
    generator: FakeSummaryGenerator,
) -> CompactionService:
    return CompactionService(
        manager=manager,
        model=get_model("faux", "faux-1"),
        branch_settings=BranchSummarySettings(skip_prompt=True),
        generator=generator,
    )


def test_compaction_service_appends_compaction_entry_without_intermediate_messages(
    tmp_path: Path,
) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="old context")], timestamp=1),
    )
    recent = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="recent context")], timestamp=2),
    )
    generator = FakeSummaryGenerator()

    result = asyncio.run(
        _service(manager, generator).compact_session(
            session.id,
            reason="manual",
            custom_instructions=" keep this detail   exactly",
            force=True,
        )
    )

    entries = manager.repository.list_entries(session.id)
    assert [entry.entry_type for entry in entries] == ["message", "message", "compaction"]
    assert result.entry.payload.first_kept_entry_id == recent.pi_export_id
    assert result.entry.payload.from_hook is False
    assert " keep this detail   exactly" in generator.prompts[0]


def test_provider_summary_generator_does_not_send_nonportable_metadata() -> None:
    stream_fn = _summary_stream(SUMMARY)
    generator = ProviderSummaryGenerator(stream_fn=stream_fn)

    result = asyncio.run(generator.generate("summarize", model=get_model("faux", "faux-1")))

    assert result == SUMMARY
    assert stream_fn.options
    assert stream_fn.options[0].metadata == {}
    assert stream_fn.options[0].cache_retention == "none"
    assert stream_fn.options[0].session_id is None


def test_hydrated_summaries_cross_provider_boundary(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="old context")], timestamp=1),
    )
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="recent context")], timestamp=2),
    )

    asyncio.run(
        _service(manager, FakeSummaryGenerator()).compact_session(
            session.id,
            reason="manual",
            force=True,
        )
    )

    context = manager.build_session_context(session.id)
    converted = convert_coding_messages_to_llm(list(context.messages))

    assert any(
        message.role == "user"
        and "<session-context type=\"compactionSummary\"" in message.content[0].text
        for message in converted
    )


def test_repeated_compaction_prompt_includes_previous_summary_and_survivors(
    tmp_path: Path,
) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="old context")], timestamp=1),
    )
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="recent context")], timestamp=2),
    )
    generator = FakeSummaryGenerator()
    service = _service(manager, generator)

    asyncio.run(service.compact_session(session.id, reason="manual", force=True))
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="post summary survivor")], timestamp=3),
    )
    asyncio.run(service.compact_session(session.id, reason="manual", force=True))

    assert len(generator.prompts) == 2
    assert SUMMARY in generator.prompts[1]
    assert "post summary survivor" in generator.prompts[1]


def test_branch_summary_switches_leaf_to_summary_entry(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    root = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="root")], timestamp=1),
    )
    target = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="target")], timestamp=2),
    )
    manager.select_leaf(session.id, root.pi_export_id)
    old_leaf = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="old branch")], timestamp=3),
    )

    result = asyncio.run(
        _service(manager, FakeSummaryGenerator()).summarize_branch_for_tree_switch(
            session.id,
            target_entry_id=target.pi_export_id,
        )
    )

    assert result.entry is not None
    assert result.entry.payload.type == "branch_summary"
    assert result.entry.payload.parent_id == target.pi_export_id
    assert result.entry.payload.from_id == old_leaf.pi_export_id
    assert manager.session_stats(session.id).current_leaf == result.entry.pi_export_id
    assert [message.role for message in manager.build_session_context(session.id).messages] == [
        "user",
        "user",
        "branchSummary",
    ]


def test_branch_summary_skip_prompt_switches_without_summary_entry(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    root = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="root")], timestamp=1),
    )
    target = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="target")], timestamp=2),
    )
    manager.select_leaf(session.id, root.pi_export_id)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="old branch")], timestamp=3),
    )
    generator = FakeSummaryGenerator()

    result = asyncio.run(
        _service_with_branch_skip(manager, generator).summarize_branch_for_tree_switch(
            session.id,
            target_entry_id=target.pi_export_id,
        )
    )

    assert result.skipped is True
    assert result.entry is None
    assert generator.prompts == []
    assert manager.session_stats(session.id).current_leaf == target.pi_export_id
    assert [entry.entry_type for entry in manager.repository.list_entries(session.id)] == [
        "message",
        "message",
        "message",
    ]

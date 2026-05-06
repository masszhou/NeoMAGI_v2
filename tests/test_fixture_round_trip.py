"""Pi-compatibility fixture round-trip tests (W4 acceptance gate).

For each of the 8 core M0 fixtures we:

1. Load the source JSON / JSONL.
2. Validate via the appropriate ``TypeAdapter`` from `ai_provider`,
   `agent_core`, or `cli.core.session_types`.
3. Dump back with ``model_dump(by_alias=True, exclude_none=True)`` and assert
   it round-trips byte-stably (after a normalised key sort).
4. Verify timestamp types stayed put: messages keep ``int`` (Unix ms);
   session entries keep ``str`` (ISO8601).
5. Verify opaque fields (``textSignature`` / ``thinkingSignature`` /
   ``thoughtSignature`` / ``responseId``) survive the round-trip.

M2 extends the fixture tree with provider/cache scenes. Those machine-readable
fixtures are exercised by the provider behavior tests rather than by
file-existence assertions here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_core.types import AgentEventAdapter
from ai_provider.overflow import is_context_overflow
from ai_provider.types import (
    AssistantMessageAdapter,
    AssistantMessageEventAdapter,
    ContextAdapter,
    ToolResultMessageAdapter,
)
from cli.core.session_types import SessionEntryAdapter

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "pi_compat"

# The 8 fixtures that M0 acceptance (#6) requires to ship with full input + expected.
CORE_SCENES = (
    "assistant_text_delta",
    "tool_execution_success",
    "parallel_tools",
    "compaction",
    "cache_retention_none",
    "overflow_error_patterns",
    "silent_overflow",
    "rpc_prompt_flow",
)

ALL_SCENES = (
    "abort_during_stream",
    "abort_during_tool",
    "anthropic_cache_long",
    "anthropic_cache_none",
    "anthropic_cache_short",
    "assistant_text_delta",
    "assistant_thinking_delta",
    "assistant_tool_call",
    "before_agent_start_chained_systemprompt",
    "branch_summary",
    "cache_retention_none",
    "compaction",
    "cross_provider_handoff_opaque",
    "extension_api_surface",
    "extension_custom_message",
    "extension_tool_event_mutation",
    "extensions",
    "model_change",
    "openai_completions_prompt_cache",
    "openai_responses_prompt_cache",
    "openai_responses_stream_tool_call",
    "overflow_error_patterns",
    "parallel_tools",
    "prepare_arguments_repair",
    "provider_abort",
    "provider_stream_text",
    "provider_stream_tool_call",
    "rpc_prompt_flow",
    "rpc_sync_response",
    "session_affinity_headers",
    "session_export_full_demo",
    "session_before_compact_extension_replace",
    "session_tree_branch",
    "skills",
    "silent_overflow",
    "thinking_level_change",
    "tool_argument_validation",
    "tool_execution_error",
    "tool_execution_success",
    "prompt_templates",
    "usage_cache_normalization",
)

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _canonical(obj: Any) -> Any:
    """Re-parsed canonical form of a JSON object, ignoring key-insertion order
    and float representation differences (``3e-05`` vs ``0.00003``)."""

    return json.loads(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _round_trip_obj(obj: Any, adapter: Any) -> dict[str, Any]:
    """Validate then dump with Pi-compatible alias serialization."""

    instance = adapter.validate_python(obj)
    return adapter.dump_python(instance, by_alias=True, exclude_none=True)


# --------------------------------------------------------------------------- #
# Fixture directory invariant                                                 #
# --------------------------------------------------------------------------- #


def test_expected_scene_directories_exist() -> None:
    actual = {p.name for p in FIXTURE_ROOT.iterdir() if p.is_dir()}
    assert actual == set(ALL_SCENES), (
        f"Mismatched fixture directories: extra={actual - set(ALL_SCENES)}, "
        f"missing={set(ALL_SCENES) - actual}"
    )


@pytest.mark.parametrize("scene", ALL_SCENES)
def test_scene_has_readme(scene: str) -> None:
    assert (FIXTURE_ROOT / scene / "README.md").is_file()


@pytest.mark.parametrize("scene", CORE_SCENES)
def test_core_scene_has_input_and_expected(scene: str) -> None:
    """Acceptance #6 of plan §完成标准: 8 core fixtures must ship with full
    ``input.json`` AND ``expected.json``. Stream-only fixtures may also carry
    ``events.jsonl`` but those are supplementary, not substitutes for the
    canonical input/expected pair."""

    scene_dir = FIXTURE_ROOT / scene
    assert (scene_dir / "input.json").is_file(), f"{scene}: missing input.json"
    assert (scene_dir / "expected.json").is_file(), f"{scene}: missing expected.json"


# --------------------------------------------------------------------------- #
# Core fixture round-trip suite (8 fixtures)                                  #
# --------------------------------------------------------------------------- #


def test_assistant_text_delta_round_trip() -> None:
    scene = FIXTURE_ROOT / "assistant_text_delta"
    inputs = _load_json(scene / "input.json")
    events = _load_jsonl(scene / "events.jsonl")
    expected = _load_json(scene / "expected.json")

    # input.json describes the upstream Context handed to the provider;
    # validate via the canonical adapter to keep the contract tight.
    ctx = ContextAdapter.validate_python(inputs)
    assert ctx.system_prompt == "You are a helpful coding assistant."
    assert isinstance(ctx.messages[0].timestamp, int)

    # Each event round-trips through the AssistantMessageEvent adapter.
    for event in events:
        dumped = _round_trip_obj(event, AssistantMessageEventAdapter)
        assert _canonical(dumped) == _canonical(event), event

    # The final ``done`` frame's message equals expected.json.
    done = events[-1]
    assert done["type"] == "done"
    assert _canonical(done["message"]) == _canonical(expected)

    # Opaque ``textSignature`` and ``responseId`` survive.
    final_msg = AssistantMessageAdapter.validate_python(expected)
    assert final_msg.content[0].text_signature == "sig_v1_text_001"
    assert final_msg.response_id == "resp_anth_001"
    # Timestamps remain int.
    assert isinstance(final_msg.timestamp, int)


def test_tool_execution_success_round_trip() -> None:
    scene = FIXTURE_ROOT / "tool_execution_success"
    inputs = _load_json(scene / "input.json")
    events = _load_jsonl(scene / "events.jsonl")
    expected = _load_json(scene / "expected.json")

    # input.json carries the assistant message with the toolCall block + the
    # ToolDefinition that was registered. Verify shape consistency.
    assistant = AssistantMessageAdapter.validate_python(inputs["assistantMessage"])
    tool_calls = [c for c in assistant.content if c.type == "toolCall"]
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_read_1"
    assert inputs["toolDefinition"]["name"] == tool_calls[0].name

    for event in events:
        dumped = _round_trip_obj(event, AgentEventAdapter)
        assert _canonical(dumped) == _canonical(event)

    result_msg = ToolResultMessageAdapter.validate_python(expected)
    assert result_msg.is_error is False
    assert result_msg.tool_call_id == "call_read_1"
    assert isinstance(result_msg.timestamp, int)


def test_parallel_tools_round_trip() -> None:
    scene = FIXTURE_ROOT / "parallel_tools"
    inputs = _load_json(scene / "input.json")
    events = _load_jsonl(scene / "events.jsonl")
    expected = _load_json(scene / "expected.json")

    # input.json: assistant message with two parallel toolCall blocks.
    assistant = AssistantMessageAdapter.validate_python(inputs["assistantMessage"])
    tool_calls = [c for c in assistant.content if c.type == "toolCall"]
    assert [c.id for c in tool_calls] == ["call_read_a", "call_grep_foo"]
    assert {td["name"] for td in inputs["toolDefinitions"]} == {"read", "grep"}

    for event in events:
        dumped = _round_trip_obj(event, AgentEventAdapter)
        assert _canonical(dumped) == _canonical(event)

    # Source-order preservation: assistant order is read then grep, even though
    # the second tool finishes first.
    assert [e["toolCallId"] for e in events if e["type"] == "tool_execution_start"] == [
        "call_read_a",
        "call_grep_foo",
    ]
    assert [e["toolCallId"] for e in events if e["type"] == "tool_execution_end"] == [
        "call_grep_foo",
        "call_read_a",
    ]
    assert [m["toolCallId"] for m in expected] == ["call_read_a", "call_grep_foo"]

    for raw in expected:
        msg = ToolResultMessageAdapter.validate_python(raw)
        assert isinstance(msg.timestamp, int)


def test_compaction_round_trip() -> None:
    scene = FIXTURE_ROOT / "compaction"
    inputs = _load_json(scene / "input.json")
    expected = _load_json(scene / "expected.json")

    # Pre-compaction entries are message-typed; round-trip via SessionEntryAdapter.
    for entry in inputs:
        dumped = _round_trip_obj(entry, SessionEntryAdapter)
        assert _canonical(dumped) == _canonical(entry)
        # SessionEntry timestamps are ISO8601 strings; never coerced.
        assert isinstance(entry["timestamp"], str)

    compaction_entry = SessionEntryAdapter.validate_python(expected)
    assert compaction_entry.type == "compaction"
    assert compaction_entry.first_kept_entry_id == "01abc003"
    assert compaction_entry.tokens_before == 80120
    # ISO8601 stays a str.
    assert isinstance(compaction_entry.timestamp, str)
    # ``fromHook=false`` keeps its boolean shape on the wire.
    dumped = SessionEntryAdapter.dump_python(
        compaction_entry, by_alias=True, exclude_none=True
    )
    assert dumped["fromHook"] is False
    assert _canonical(dumped) == _canonical(expected)


def test_cache_retention_none_round_trip() -> None:
    scene = FIXTURE_ROOT / "cache_retention_none"
    inputs = _load_json(scene / "input.json")
    expected = _load_json(scene / "expected.json")

    for forbidden_key in inputs["forbidden_payload_keys"]:
        assert forbidden_key not in inputs["outgoing_payload"], forbidden_key
    for forbidden_header in inputs["forbidden_headers"]:
        assert forbidden_header not in inputs["outgoing_headers"], forbidden_header

    msg = AssistantMessageAdapter.validate_python(expected)
    assert msg.usage.cache_read == 0
    assert msg.usage.cache_write == 0
    assert msg.usage.cost.cache_read == 0
    assert msg.usage.cost.cache_write == 0
    # Opaque ``responseId`` preserved.
    assert msg.response_id == "resp_no_cache_001"
    dumped = AssistantMessageAdapter.dump_python(msg, by_alias=True, exclude_none=True)
    assert _canonical(dumped) == _canonical(expected)


def test_overflow_error_patterns_fixture() -> None:
    scene = FIXTURE_ROOT / "overflow_error_patterns"
    inputs = _load_json(scene / "input.json")
    expected = _load_json(scene / "expected.json")

    from ai_provider.types import AssistantMessage, Usage, UsageCost

    zero_usage = Usage(
        input=0,
        output=0,
        cacheRead=0,
        cacheWrite=0,
        totalTokens=0,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )

    def _msg(text: str) -> AssistantMessage:
        return AssistantMessage(
            role="assistant",
            content=[],
            api="anthropic-messages",
            provider="anthropic",
            model="m",
            usage=zero_usage,
            stopReason="error",
            errorMessage=text,
            timestamp=0,
        )

    for name, sample in inputs["overflow_samples"].items():
        assert is_context_overflow(_msg(sample)) is expected["overflow_samples"][name], name
    for name, sample in inputs["non_overflow_samples"].items():
        assert (
            is_context_overflow(_msg(sample)) is expected["non_overflow_samples"][name]
        ), name


def test_silent_overflow_round_trip() -> None:
    scene = FIXTURE_ROOT / "silent_overflow"
    inputs = _load_json(scene / "input.json")
    expected = _load_json(scene / "expected.json")

    msg = AssistantMessageAdapter.validate_python(inputs["message"])
    assert is_context_overflow(msg, context_window=inputs["contextWindow"]) is expected["overflow"]
    assert is_context_overflow(msg) is expected["fallbackOverflowWithoutContextWindow"]


def test_rpc_prompt_flow_shape() -> None:
    scene = FIXTURE_ROOT / "rpc_prompt_flow"
    inputs = _load_json(scene / "input.json")
    expected = _load_json(scene / "expected.json")

    # Sanity: client side shape.
    assert inputs[0]["type"] == "prompt"
    # First server output is the sync acceptance response.
    assert expected[0]["type"] == "response"
    assert expected[0]["command"] == "prompt"
    assert expected[0]["success"] is True
    # No second ``response`` for the same async command.
    response_count = sum(
        1 for o in expected if o.get("type") == "response" and o.get("command") == "prompt"
    )
    assert response_count == 1
    # Last server output is the agent_end frame.
    assert expected[-1]["type"] == "agent_end"
    # Each AgentSessionEvent in the body validates via the AgentEvent adapter
    # (the 5 session-level frames are absent in this minimal fixture so the
    # core 10-frame adapter is sufficient).
    for entry in expected[1:]:
        dumped = _round_trip_obj(entry, AgentEventAdapter)
        assert _canonical(dumped) == _canonical(entry)

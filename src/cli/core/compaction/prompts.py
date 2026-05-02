"""Prompt builders for Pi-compatible compaction summaries."""

from __future__ import annotations

from typing import Any

from .files import FileContext

SUMMARY_OUTLINE = """## Goal
## Constraints & Preferences
## Progress
### Done
### In Progress
### Blocked
## Key Decisions
## Next Steps
## Critical Context
<read-files>
</read-files>
<modified-files>
</modified-files>"""


def build_compaction_prompt(
    *,
    messages: list[Any],
    file_context: FileContext,
    custom_instructions: str | None = None,
    surviving_messages: list[Any] | None = None,
) -> str:
    lines = [
        "Summarize the older durable session context for a coding agent.",
        "Return markdown in exactly this outline:",
        "",
        SUMMARY_OUTLINE,
    ]
    if custom_instructions is not None:
        lines.extend(["", "Custom instructions:", custom_instructions])
    lines.extend(["", "Read files:", *_prefix(file_context.read_files)])
    lines.extend(["", "Modified files:", *_prefix(file_context.modified_files)])
    lines.extend(["", "Context to summarize:", _messages_text(messages)])
    if surviving_messages:
        lines.extend(
            [
                "",
                "Surviving context to preserve:",
                _messages_text(surviving_messages),
            ]
        )
    return "\n".join(lines)


def build_branch_summary_prompt(
    *,
    entries: list[Any],
    from_id: str,
    target_id: str,
    file_context: FileContext,
) -> str:
    lines = [
        "Summarize the branch that is being left during session tree navigation.",
        f"Old leaf: {from_id}",
        f"Target leaf: {target_id}",
        "Return markdown in exactly this outline:",
        "",
        SUMMARY_OUTLINE,
        "",
        "Read files:",
        *_prefix(file_context.read_files),
        "",
        "Modified files:",
        *_prefix(file_context.modified_files),
        "",
        "Branch entries to summarize:",
        _entries_text(entries),
    ]
    return "\n".join(lines)


def ensure_summary_outline(summary: str, *, fallback_context: str = "") -> str:
    if all(heading in summary for heading in REQUIRED_HEADINGS):
        return summary
    critical = summary.strip() or fallback_context.strip() or "(no additional context)"
    return f"""## Goal
Preserve the durable session context needed to continue the task.
## Constraints & Preferences
Respect the existing repository and session boundaries.
## Progress
### Done
{critical}
### In Progress
(not specified)
### Blocked
(not specified)
## Key Decisions
(not specified)
## Next Steps
(not specified)
## Critical Context
{critical}
<read-files>
</read-files>
<modified-files>
</modified-files>"""


REQUIRED_HEADINGS = (
    "## Goal",
    "## Constraints & Preferences",
    "## Progress",
    "### Done",
    "### In Progress",
    "### Blocked",
    "## Key Decisions",
    "## Next Steps",
    "## Critical Context",
    "<read-files>",
    "<modified-files>",
)


def _prefix(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values] or ["- (none)"]


def _messages_text(messages: list[Any]) -> str:
    return "\n\n".join(_dump_message(message) for message in messages)


def _entries_text(entries: list[Any]) -> str:
    return "\n\n".join(_dump_message(getattr(entry, "payload", entry)) for entry in entries)


def _dump_message(value: Any) -> str:
    if getattr(value, "type", None) == "compaction" and hasattr(value, "summary"):
        return (
            f"compactionSummary firstKeptEntryId={value.first_kept_entry_id} "
            f"tokensBefore={value.tokens_before}\n{value.summary}"
        )
    if getattr(value, "type", None) == "branch_summary" and hasattr(value, "summary"):
        return f"branchSummary fromId={value.from_id}\n{value.summary}"
    if getattr(value, "role", None) == "compactionSummary":
        return f"compactionSummary tokensBefore={value.tokens_before}\n{value.summary}"
    if getattr(value, "role", None) == "branchSummary":
        return f"branchSummary fromId={value.from_id}\n{value.summary}"
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json(by_alias=True, exclude_none=True)
    if hasattr(value, "model_dump"):
        return str(value.model_dump(by_alias=True, exclude_none=True))
    return str(value)


__all__ = [
    "REQUIRED_HEADINGS",
    "SUMMARY_OUTLINE",
    "build_branch_summary_prompt",
    "build_compaction_prompt",
    "ensure_summary_outline",
]

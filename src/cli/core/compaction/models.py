"""Runtime result models for compaction operations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _CompactionModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class RetainedFragment(_CompactionModel):
    source_entry_id: str = Field(alias="sourceEntryId")
    role: str
    content_index: int = Field(alias="contentIndex")
    block_type: Literal["text"] = Field(default="text", alias="blockType")
    text: str


class CompactionResult(_CompactionModel):
    summary: str
    first_kept_entry_id: str = Field(alias="firstKeptEntryId")
    tokens_before: int = Field(alias="tokensBefore")
    tokens_after: int = Field(alias="tokensAfter")
    read_files: list[str] = Field(default_factory=list, alias="readFiles")
    modified_files: list[str] = Field(default_factory=list, alias="modifiedFiles")
    reason: Literal["manual", "threshold", "overflow"]
    from_hook: bool = Field(default=False, alias="fromHook")
    retained_fragments: list[RetainedFragment] = Field(
        default_factory=list,
        alias="retainedFragments",
    )

    def details_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "readFiles": list(self.read_files),
            "modifiedFiles": list(self.modified_files),
        }
        if self.retained_fragments:
            payload["retainedFragments"] = [
                fragment.model_dump(by_alias=True, exclude_none=True)
                for fragment in self.retained_fragments
            ]
        return payload


class BranchSummaryResult(_CompactionModel):
    summary: str
    from_id: str = Field(alias="fromId")
    read_files: list[str] = Field(default_factory=list, alias="readFiles")
    modified_files: list[str] = Field(default_factory=list, alias="modifiedFiles")
    reason: Literal["tree"] = "tree"
    from_hook: bool = Field(default=False, alias="fromHook")

    def details_payload(self) -> dict[str, list[str]]:
        return {
            "readFiles": list(self.read_files),
            "modifiedFiles": list(self.modified_files),
        }


class CompactionFailure(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def retained_fragments_from_details(details: object) -> list[RetainedFragment]:
    if not isinstance(details, dict):
        return []
    raw = details.get("retainedFragments")
    if not isinstance(raw, list):
        return []
    fragments: list[RetainedFragment] = []
    for item in raw:
        try:
            fragments.append(RetainedFragment.model_validate(item))
        except Exception:
            continue
    return fragments


__all__ = [
    "BranchSummaryResult",
    "CompactionFailure",
    "CompactionResult",
    "RetainedFragment",
    "retained_fragments_from_details",
]

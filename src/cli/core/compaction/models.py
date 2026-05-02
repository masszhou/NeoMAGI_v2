"""Runtime result models for compaction operations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _CompactionModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CompactionResult(_CompactionModel):
    summary: str
    first_kept_entry_id: str = Field(alias="firstKeptEntryId")
    tokens_before: int = Field(alias="tokensBefore")
    tokens_after: int = Field(alias="tokensAfter")
    read_files: list[str] = Field(default_factory=list, alias="readFiles")
    modified_files: list[str] = Field(default_factory=list, alias="modifiedFiles")
    reason: Literal["manual", "threshold", "overflow"]
    from_hook: bool = Field(default=False, alias="fromHook")

    def details_payload(self) -> dict[str, list[str]]:
        return {
            "readFiles": list(self.read_files),
            "modifiedFiles": list(self.modified_files),
        }


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


__all__ = ["BranchSummaryResult", "CompactionFailure", "CompactionResult"]

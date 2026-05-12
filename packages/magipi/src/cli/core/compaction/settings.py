"""Compaction settings shared by manual, auto, and overflow paths."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 16_384
    keep_recent_tokens: int = 20_000

    def target_budget(self, context_window: int) -> int:
        return max(0, context_window - self.reserve_tokens)


@dataclass(frozen=True, slots=True)
class BranchSummarySettings:
    reserve_tokens: int = 16_384
    skip_prompt: bool = False


__all__ = ["BranchSummarySettings", "CompactionSettings"]

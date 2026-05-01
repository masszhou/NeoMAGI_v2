"""Policy protocol types for governed local tool execution."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PolicyEffect = Literal["allow", "block", "confirm"]
PolicyActor = Literal["model", "user", "extension"]


class _PolicyModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class PolicyRequest(_PolicyModel):
    session_id: str | None = None
    runtime_session_id: str | None = Field(default=None, alias="runtimeSessionId")
    run_id: str | None = Field(default=None, alias="runId")
    tool_name: str = Field(alias="toolName")
    args: dict[str, Any]
    cwd: str
    actor: PolicyActor = "model"
    source: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(_PolicyModel):
    effect: PolicyEffect
    reason: str | None = None
    normalized_args: dict[str, Any] = Field(default_factory=dict, alias="normalizedArgs")
    resolved_paths: dict[str, str] = Field(default_factory=dict, alias="resolvedPaths")
    audit_tags: list[str] = Field(default_factory=list, alias="auditTags")
    redaction_tags: list[str] = Field(default_factory=list, alias="redactionTags")

    @classmethod
    def allow(
        cls,
        *,
        normalized_args: dict[str, Any] | None = None,
        resolved_paths: dict[str, str] | None = None,
        audit_tags: list[str] | None = None,
    ) -> PolicyDecision:
        return cls(
            effect="allow",
            normalizedArgs=normalized_args or {},
            resolvedPaths=resolved_paths or {},
            auditTags=audit_tags or [],
        )

    @classmethod
    def block(cls, reason: str, *, audit_tags: list[str] | None = None) -> PolicyDecision:
        return cls(effect="block", reason=reason, auditTags=audit_tags or ["policy:block"])

    @classmethod
    def confirm(cls, reason: str, *, audit_tags: list[str] | None = None) -> PolicyDecision:
        return cls(effect="confirm", reason=reason, auditTags=audit_tags or ["policy:confirm"])


__all__ = [
    "PolicyActor",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyRequest",
]

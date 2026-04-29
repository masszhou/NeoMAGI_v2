"""Provider cache affinity helpers for agent_core."""

from __future__ import annotations

import hashlib
import uuid

from ai_provider.prompt_cache import sanitize_cache_affinity_id


def derive_provider_cache_affinity_id(durable_session_id: str | None) -> str | None:
    if durable_session_id is None:
        return None
    affinity_id = sanitize_cache_affinity_id(durable_session_id)
    if affinity_id is not None:
        return affinity_id
    digest = hashlib.sha256(durable_session_id.encode("utf-8")).hexdigest()[:32]
    return f"neomagi-{digest}"


def mint_provider_cache_affinity_id() -> str:
    return str(uuid.uuid4())


__all__ = [
    "derive_provider_cache_affinity_id",
    "mint_provider_cache_affinity_id",
]

"""D11 PolicyResolutionStore: put / consume / step-scope TTL."""

from __future__ import annotations

from cli.tools.policy_resolution_store import PolicyResolutionStore
from policy.types import PolicyDecision


def _decision(effect: str = "allow") -> PolicyDecision:
    if effect == "allow":
        return PolicyDecision.allow()
    return PolicyDecision.block("denied")


def test_put_then_consume_returns_entry_once() -> None:
    store = PolicyResolutionStore()
    raw = _decision("allow")
    resolved = _decision("allow")
    store.put("call_1", raw=raw, resolved=resolved, permission_profile={"name": "guarded"})

    first = store.consume("call_1")
    assert first is not None
    assert first.raw is raw
    assert first.resolved is resolved
    assert first.permission_profile == {"name": "guarded"}

    # Read-and-remove: second consume returns None.
    assert store.consume("call_1") is None


def test_consume_unknown_returns_none() -> None:
    store = PolicyResolutionStore()
    assert store.consume("never_seen") is None


def test_consume_none_id_returns_none() -> None:
    store = PolicyResolutionStore()
    assert store.consume(None) is None


def test_peek_does_not_remove() -> None:
    store = PolicyResolutionStore()
    store.put("call_x", raw=_decision(), resolved=_decision())

    assert store.peek("call_x") is not None
    # Still consumable after peek.
    assert store.consume("call_x") is not None
    assert store.peek("call_x") is None


def test_has_pending_reflects_state() -> None:
    store = PolicyResolutionStore()
    assert store.has_pending() is False
    store.put("a", raw=_decision(), resolved=_decision())
    assert store.has_pending() is True
    store.consume("a")
    assert store.has_pending() is False


def test_record_block_does_not_populate_entries() -> None:
    """P2 invariant: ``record_block`` writes to the block-reason side
    channel only — it must not seed ``_entries`` because the wrapper is
    short-circuited on the block path and would never consume the entry,
    leaving a stale read-and-remove violation."""

    store = PolicyResolutionStore()
    store.record_block("call_blocked", "denied by profile")
    assert store.has_pending() is False
    assert store.peek("call_blocked") is None
    assert store.consume("call_blocked") is None
    # Block-reason side channel is still readable until consumed.
    assert store.consume_block_reason("call_blocked") == "denied by profile"
    # And it is read-and-remove on the block channel too.
    assert store.consume_block_reason("call_blocked") is None


def test_consume_block_reason_with_none_key_returns_none() -> None:
    store = PolicyResolutionStore()
    assert store.consume_block_reason(None) is None

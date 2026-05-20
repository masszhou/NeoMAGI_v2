from __future__ import annotations

import json
from pathlib import Path


_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "pi_compat"
_FORBIDDEN_MESSAGE_DELTA_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


def test_anthropic_message_delta_fixtures_do_not_smuggle_final_usage() -> None:
    offenders: list[str] = []
    for path in sorted(_FIXTURE_ROOT.glob("*/events.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for index, event in enumerate(data.get("events", [])):
            if event.get("type") != "message_delta" or not isinstance(event.get("usage"), dict):
                continue
            forbidden = sorted(
                _FORBIDDEN_MESSAGE_DELTA_USAGE_KEYS.intersection(event["usage"])
            )
            if forbidden:
                offenders.append(f"{path.relative_to(_FIXTURE_ROOT)}[{index}]: {forbidden}")

    assert offenders == []

"""Dashboard payload DTO helpers and parsing boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


class DashboardRangeError(ValueError):
    """Raised when a dashboard range is not in the allowlist."""


@dataclass(frozen=True, slots=True)
class DashboardRange:
    label: str
    since: datetime | None


@dataclass(frozen=True, slots=True)
class PanelMeta:
    status: str
    skipped_count: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "skipped_count": self.skipped_count,
            "warnings": list(self.warnings),
        }


RANGE_PRESETS = frozenset({"24h", "7d", "30d", "all"})


def parse_dashboard_range(value: str | None, *, now: datetime | None = None) -> DashboardRange:
    label = (value or "7d").strip().lower()
    if label not in RANGE_PRESETS:
        raise DashboardRangeError(f"unsupported dashboard range: {value!r}")
    current = now or utc_now()
    if label == "24h":
        return DashboardRange(label=label, since=current - timedelta(hours=24))
    if label == "7d":
        return DashboardRange(label=label, since=current - timedelta(days=7))
    if label == "30d":
        return DashboardRange(label=label, since=current - timedelta(days=30))
    return DashboardRange(label=label, since=None)


def panel_payload(
    data: dict[str, Any] | list[Any],
    *,
    status: str,
    skipped_count: int = 0,
    warnings: list[str] | tuple[str, ...] = (),
) -> dict[str, Any] | list[Any]:
    meta = PanelMeta(
        status=status,
        skipped_count=skipped_count,
        warnings=tuple(warnings),
    ).to_dict()
    if isinstance(data, dict):
        return {"_panel": meta, **data}
    return {"_panel": meta, "items": data}


def degraded_panel(reason: str) -> dict[str, Any]:
    return panel_payload({}, status="degraded", warnings=[reason])


def iso(value: Any) -> str | None:
    parsed = parse_timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def age_seconds(value: Any, *, now: datetime | None = None) -> int | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    current = now or utc_now()
    return max(0, int((current - parsed).total_seconds()))


def short_id(value: Any) -> str:
    return str(value)[:8]


def int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


__all__ = [
    "DashboardRange",
    "DashboardRangeError",
    "PanelMeta",
    "age_seconds",
    "degraded_panel",
    "int_or_zero",
    "iso",
    "panel_payload",
    "parse_dashboard_range",
    "parse_timestamp",
    "short_id",
    "utc_now",
]

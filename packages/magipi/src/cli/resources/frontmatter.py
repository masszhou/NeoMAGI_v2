"""Small frontmatter parser for resource markdown files."""

from __future__ import annotations

from typing import Any


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            metadata = _parse_metadata(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            if text.endswith("\n"):
                body += "\n"
            return metadata, body
    return {}, text


def _parse_metadata(lines: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = _parse_scalar(value.strip())
    return metadata


def _parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


__all__ = ["split_frontmatter"]

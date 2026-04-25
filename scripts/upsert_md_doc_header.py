#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

DOC_KEYS = ("doc_id", "doc_id_format", "doc_id_assigned_at")
KEY_VALUE_PATTERN = re.compile(r"^(?P<key>[A-Za-z0-9_]+):[ \t]*(?P<value>.*)$")
FRONT_MATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.DOTALL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure a markdown file starts with the expected doc_id front matter.",
    )
    parser.add_argument("path", help="Path to the target .md file.")
    return parser.parse_args()


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def format_timestamp(timestamp: float) -> str:
    local_tz = datetime.now().astimezone().tzinfo
    return datetime.fromtimestamp(timestamp, tz=local_tz).isoformat(timespec="seconds")


def initial_assigned_at(path: Path) -> str:
    stats = path.stat()
    created_at = getattr(stats, "st_birthtime", None)
    timestamp = created_at if created_at not in (None, 0) else stats.st_mtime
    return format_timestamp(timestamp)


def extract_front_matter(text: str) -> tuple[str, str] | None:
    match = FRONT_MATTER_PATTERN.match(text)
    if not match:
        return None

    block = match.group(1)
    if not any(KEY_VALUE_PATTERN.match(line) for line in block.splitlines() if line.strip()):
        return None

    return block, text[match.end() :]


def split_doc_metadata(block: str) -> tuple[dict[str, str], list[str]]:
    doc_metadata: dict[str, str] = {}
    passthrough_lines: list[str] = []

    for line in block.splitlines():
        match = KEY_VALUE_PATTERN.match(line)
        if match and match.group("key") in DOC_KEYS:
            doc_metadata[match.group("key")] = match.group("value").strip()
            continue

        passthrough_lines.append(line)

    return doc_metadata, passthrough_lines


def render_front_matter(lines: list[str], newline: str) -> str:
    return f"---{newline}{newline.join(lines)}{newline}---{newline}"


def update_content(path: Path, text: str) -> tuple[str, str]:
    newline = detect_newline(text)
    front_matter = extract_front_matter(text)

    if front_matter is None:
        body = text.lstrip("\r\n")
        metadata_lines = [
            f"doc_id: {uuid.uuid7()}",
            "doc_id_format: uuidv7",
            f"doc_id_assigned_at: {initial_assigned_at(path)}",
        ]
        header = render_front_matter(metadata_lines, newline)
        return (header + body) if body else header, "updated"

    block, body = front_matter
    doc_metadata, passthrough_lines = split_doc_metadata(block)

    if all(key in doc_metadata for key in DOC_KEYS):
        return text, "skipped"

    metadata_lines = [
        f"doc_id: {doc_metadata.get('doc_id', uuid.uuid7())}",
        f"doc_id_format: {doc_metadata.get('doc_id_format', 'uuidv7')}",
        f"doc_id_assigned_at: {doc_metadata.get('doc_id_assigned_at', initial_assigned_at(path))}",
    ]
    merged_lines = metadata_lines + passthrough_lines
    normalized_body = body.lstrip("\r\n")
    header = render_front_matter(merged_lines, newline)
    return (header + normalized_body) if normalized_body else header, "updated"


def main() -> int:
    args = parse_args()
    path = Path(args.path).expanduser().resolve()

    if not path.exists():
        print(f"error: file does not exist: {path}", file=sys.stderr)
        return 1

    if not path.is_file():
        print(f"error: path is not a file: {path}", file=sys.stderr)
        return 1

    if path.suffix.lower() != ".md":
        print(f"error: file must end with .md: {path}", file=sys.stderr)
        return 1

    original_text = path.read_text(encoding="utf-8")
    updated_text, status = update_content(path, original_text)

    if status == "skipped":
        print(f"skipped: {path}")
        return 0

    path.write_text(updated_text, encoding="utf-8")
    print(f"updated: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

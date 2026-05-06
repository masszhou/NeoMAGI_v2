"""Build the NeoMAGI-compatible pi-skills showcase pack."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

UPSTREAM_REPO = "https://github.com/badlogic/pi-skills"
UPSTREAM_REF = "75d32a382b0c8aafce356d68e17d2dc94c0c953b"

EXPECTED_SKILLS = (
    "brave-search",
    "browser-tools",
    "gccli",
    "gdcli",
    "gmcli",
    "transcribe",
    "vscode",
    "youtube-transcript",
)

ALLOWED_FILENAMES = {"SKILL.md", "package.json", "package-lock.json"}
ALLOWED_SUFFIXES = {".js", ".sh"}
BLOCKED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    "downloads",
    "screenshots",
    "profiles",
}
BLOCKED_FILENAMES = {
    "accounts.json",
    "config",
    "credentials.json",
    "token.json",
    "tokens.json",
}


@dataclass(frozen=True, slots=True)
class BuildSummary:
    upstream_ref: str
    skills: tuple[str, ...]
    copied_files: tuple[Path, ...]
    skipped_files: tuple[Path, ...]


def should_copy_file(relative_path: Path) -> bool:
    parts = {part.lower() for part in relative_path.parts}
    if parts & BLOCKED_PARTS:
        return False
    name = relative_path.name
    lowered_name = name.lower()
    if lowered_name in BLOCKED_FILENAMES:
        return False
    if lowered_name.startswith("client_secret") or "oauth" in lowered_name:
        return False
    return name in ALLOWED_FILENAMES or relative_path.suffix in ALLOWED_SUFFIXES


def rewrite_skill_markdown(skill_name: str, text: str) -> str:
    frontmatter, body = _split_frontmatter(text)
    body = _patch_upstream_body(skill_name, body)
    return (
        f"{frontmatter}\n"
        f"Derived from badlogic/pi-skills@{UPSTREAM_REF} (MIT). NeoMAGI additions document "
        "execution and safety boundaries.\n\n"
        f"{_neomagi_sections(skill_name)}\n\n"
        f"{body.lstrip()}"
    )


def build_compat_pack(
    source: Path,
    output: Path,
    *,
    expected_skills: tuple[str, ...] = EXPECTED_SKILLS,
) -> BuildSummary:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    skill_dirs = _discover_skill_dirs(source)
    skill_names = tuple(path.name for path in skill_dirs)
    if skill_names != expected_skills:
        raise ValueError(f"expected skills {expected_skills!r}, found {skill_names!r}")

    if output.exists():
        shutil.rmtree(output)
    pack_root = output / "skills" / "pi-skills"
    pack_root.mkdir(parents=True)

    copied: list[Path] = []
    skipped: list[Path] = []
    for skill_dir in skill_dirs:
        target_dir = pack_root / skill_dir.name
        target_dir.mkdir(parents=True)
        for file_path in sorted(path for path in skill_dir.rglob("*") if path.is_file()):
            relative = file_path.relative_to(skill_dir)
            if not should_copy_file(relative):
                skipped.append(Path(skill_dir.name) / relative)
                continue
            target = target_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative.name == "SKILL.md":
                target.write_text(
                    rewrite_skill_markdown(skill_dir.name, file_path.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
            else:
                shutil.copy2(file_path, target)
            copied.append(Path(skill_dir.name) / relative)

    license_path = source / "LICENSE"
    if license_path.is_file():
        shutil.copy2(license_path, output / "LICENSE.pi-skills")
        copied.append(Path("LICENSE.pi-skills"))

    (output / "README.md").write_text(_readme(), encoding="utf-8")
    copied.append(Path("README.md"))
    return BuildSummary(UPSTREAM_REF, skill_names, tuple(copied), tuple(skipped))


def _discover_skill_dirs(source: Path) -> tuple[Path, ...]:
    return tuple(sorted(path.parent for path in source.glob("*/SKILL.md")))


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md is missing YAML frontmatter")
    marker = text.find("\n---\n", 4)
    if marker == -1:
        raise ValueError("SKILL.md frontmatter is not closed")
    end = marker + len("\n---\n")
    return text[:end].rstrip(), text[end:]


def _patch_upstream_body(skill_name: str, body: str) -> str:
    if skill_name == "browser-tools":
        body = body.replace("cd {baseDir}/browser-tools", "cd {baseDir}")
    if skill_name == "transcribe":
        body = body.replace(
            "echo $GROQ_API_KEY",
            'test -n "${GROQ_API_KEY:-}" && echo "GROQ_API_KEY is set" || echo "GROQ_API_KEY is not set"',
        )
    if skill_name == "vscode":
        body = body.replace("/tmp/old", ".tmp/vscode-diff/old")
    return body


def _neomagi_sections(skill_name: str) -> str:
    return (
        "## NeoMAGI execution boundary\n\n"
        "Run every command from this skill through NeoMAGI's governed `bash` tool so shell policy, "
        "timeout, audit logging, truncation, and artifact handling remain active. Commands using "
        "`{baseDir}` are resolved by NeoMAGI during `/skill:<name>` expansion to this skill's "
        "`location` path.\n\n"
        "## Setup check\n\n"
        f"{_setup_check(skill_name)}\n\n"
        "## Credentials\n\n"
        f"{_credentials(skill_name)}\n\n"
        "## Sensitive operations\n\n"
        f"{_sensitive_operations(skill_name)}\n\n"
        "## Output hygiene\n\n"
        "Do not record API keys, OAuth tokens, cookies, private email bodies, private Drive file "
        "contents, browser profile data, or non-public audio/transcript content in durable logs, "
        "findings, or summaries. Redact identifiers unless the user explicitly asks to preserve them.\n\n"
        "## Failure mode\n\n"
        "If a dependency, credential, OAuth account, browser, or transcript is unavailable, stop and "
        "report the missing prerequisite plus the next setup command. Do not continue by guessing, "
        "printing secret values, or switching to another external service without user approval."
    )


def _setup_check(skill_name: str) -> str:
    checks = {
        "brave-search": """```bash
command -v node >/dev/null && echo "node found" || echo "node missing"
test -f "{baseDir}/package.json" && echo "package.json found" || echo "package.json missing"
test -d "{baseDir}/node_modules" && echo "dependencies installed" || echo "run npm install in {baseDir}"
test -n "${BRAVE_API_KEY:-}" && echo "BRAVE_API_KEY is set" || echo "BRAVE_API_KEY is not set"
```""",
        "browser-tools": """```bash
command -v node >/dev/null && echo "node found" || echo "node missing"
test -d "{baseDir}/node_modules" && echo "dependencies installed" || echo "run npm install in {baseDir}"
command -v google-chrome >/dev/null || command -v chromium >/dev/null || test -d "/Applications/Google Chrome.app"
```""",
        "gccli": """```bash
command -v gccli >/dev/null && echo "gccli found" || echo "gccli missing"
gccli accounts list
```""",
        "gdcli": """```bash
command -v gdcli >/dev/null && echo "gdcli found" || echo "gdcli missing"
gdcli accounts list
```""",
        "gmcli": """```bash
command -v gmcli >/dev/null && echo "gmcli found" || echo "gmcli missing"
gmcli accounts list
```""",
        "transcribe": """```bash
command -v curl >/dev/null && echo "curl found" || echo "curl missing"
test -x "{baseDir}/transcribe.sh" && echo "transcribe.sh executable" || echo "transcribe.sh missing"
test -n "${GROQ_API_KEY:-}" && echo "GROQ_API_KEY is set" || echo "GROQ_API_KEY is not set"
```""",
        "vscode": """```bash
command -v code >/dev/null && echo "code CLI found" || echo "code CLI missing"
mkdir -p .tmp/vscode-diff
```""",
        "youtube-transcript": """```bash
command -v node >/dev/null && echo "node found" || echo "node missing"
test -f "{baseDir}/package.json" && echo "package.json found" || echo "package.json missing"
test -d "{baseDir}/node_modules" && echo "dependencies installed" || echo "run npm install in {baseDir}"
```""",
    }
    return checks[skill_name]


def _credentials(skill_name: str) -> str:
    credentials = {
        "brave-search": "Requires `BRAVE_API_KEY`. Check only whether it is set; never echo the value.",
        "browser-tools": "Does not require API credentials. Browser profile data and cookies are credentials for safety purposes.",
        "gccli": "Uses OAuth files under `~/.gccli/`. Do not print or copy those files.",
        "gdcli": "Uses OAuth files under `~/.gdcli/`. Do not print or copy those files.",
        "gmcli": "Uses OAuth files under `~/.gmcli/`. Do not print or copy those files.",
        "transcribe": "Requires `GROQ_API_KEY`. Check only whether it is set; never echo the value.",
        "vscode": "No external credential is required.",
        "youtube-transcript": "No credential is required for public videos with available transcripts.",
    }
    return credentials[skill_name]


def _sensitive_operations(skill_name: str) -> str:
    operations = {
        "brave-search": (
            "Search and page extraction send the query or URL to Brave and target sites. Confirm before using "
            "`--content` on private, internal, or user-sensitive URLs."
        ),
        "browser-tools": (
            "Require same-turn user confirmation before `browser-start.js --profile` or `browser-cookies.js`. "
            "Treat screenshots, DOM dumps, and JavaScript evaluation on authenticated pages as sensitive."
        ),
        "gccli": (
            "Read-only `calendars`, `events`, `event`, and `freebusy` are allowed after account selection. "
            "Require same-turn user confirmation before Calendar `create`, `update`, or `delete` operations."
        ),
        "gdcli": (
            "Read-only `ls`, `search`, and explicit downloads to a user-approved path are allowed after account "
            "selection. Require same-turn user confirmation before Drive `upload`, `mkdir`, `share --anyone`, "
            "or delete operations."
        ),
        "gmcli": (
            "Search and thread reads may expose private email; summarize instead of copying full bodies by default. "
            "Require same-turn user confirmation before Gmail `send`, `drafts` mutation, label mutation, or any "
            "attachment download risk to a non-user-specified path."
        ),
        "transcribe": (
            "Running `transcribe.sh <audio-file>` uploads local audio to Groq. Require same-turn user confirmation "
            "that the file path is correct and the audio is non-sensitive or approved for upload to Groq."
        ),
        "vscode": (
            "Opening `code -d` is local UI only. If temporary files are needed, prefer repo-local `.tmp/vscode-diff` "
            "and clean it up after review."
        ),
        "youtube-transcript": (
            "Only use public video IDs or URLs. Do not pass private, tokenized, or access-controlled URLs without "
            "same-turn user confirmation."
        ),
    }
    return operations[skill_name]


def _readme() -> str:
    skill_lines = "\n".join(f"- `{skill}`" for skill in EXPECTED_SKILLS)
    return f"""# Pi Skills Compatibility Pack

NeoMAGI-compatible prompt-resource pack derived from `badlogic/pi-skills` at `{UPSTREAM_REF}` (MIT).

This pack is a showcase fixture for Pi-style Agent Skills compatibility. It does not add native
Google, Gmail, Drive, Calendar, Brave, Groq, browser, or VS Code providers to NeoMAGI.

## Contents

{skill_lines}

## Expose The Pack

Project-level copy or symlink:

```bash
mkdir -p .pi/skills
ln -s ../../showcase/pi_skills_compat/skills/pi-skills .pi/skills/pi-skills
```

Settings-level path in `.pi/settings.json` or `~/.pi/agent/settings.json`:

```json
{{
  "resources": {{
    "skills": ["./showcase/pi_skills_compat/skills/pi-skills"]
  }}
}}
```

User-level copy or symlink:

```bash
mkdir -p ~/.pi/agent/skills
ln -s "$(pwd)/showcase/pi_skills_compat/skills/pi-skills" ~/.pi/agent/skills/pi-skills
```

## Execution Boundary

All helper commands in these skills must run through NeoMAGI's governed `bash` tool. The skills
only describe prompt-level behavior and safety gates; they do not bypass shell policy or audit.

Before using helper-backed skills, run each skill's setup check. Do not print API keys, OAuth
tokens, cookies, mail bodies, Drive file contents, browser profile data, or non-public audio
transcripts into session logs or findings.

## Upstream Notes

The upstream README at this ref lists a `subagent` requirement, but the repository contains no
`subagent/SKILL.md`. This pack generates only the eight concrete skill directories found in the
pinned source tree.
"""


def _git_head(source: Path) -> str | None:
    if not (source / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Local pi-skills clone")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("showcase/pi_skills_compat"),
        help="Output showcase directory",
    )
    args = parser.parse_args(argv)

    head = _git_head(args.source)
    if head is not None and head != UPSTREAM_REF:
        raise SystemExit(f"source HEAD {head} does not match expected {UPSTREAM_REF}")

    summary = build_compat_pack(args.source, args.output)
    print(f"upstream_ref={summary.upstream_ref}")
    print(f"skills={','.join(summary.skills)}")
    print(f"copied_files={len(summary.copied_files)}")
    if summary.skipped_files:
        print("skipped_files=" + ",".join(str(path) for path in summary.skipped_files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

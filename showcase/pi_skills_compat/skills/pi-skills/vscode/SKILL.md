---
name: vscode
description: VS Code integration for viewing diffs and comparing files. Use when showing file differences to the user.
---
Derived from badlogic/pi-skills@75d32a382b0c8aafce356d68e17d2dc94c0c953b (MIT). NeoMAGI additions document execution and safety boundaries.

## NeoMAGI execution boundary

Run every command from this skill through NeoMAGI's governed `bash` tool so shell policy, timeout, audit logging, truncation, and artifact handling remain active. Commands using `{baseDir}` are resolved by NeoMAGI during `/skill:<name>` expansion to this skill's `location` path.

## Setup check

```bash
command -v code && echo "code CLI found" || echo "code CLI missing"
mkdir -p .tmp/vscode-diff
```

## Credentials

No external credential is required.

## Sensitive operations

Opening `code -d` is local UI only. If temporary files are needed, prefer repo-local `.tmp/vscode-diff` and clean it up after review.

## Output hygiene

Do not record API keys, OAuth tokens, cookies, private email bodies, private Drive file contents, browser profile data, or non-public audio/transcript content in durable logs, findings, or summaries. Redact identifiers unless the user explicitly asks to preserve them.

## Failure mode

If a dependency, credential, OAuth account, browser, or transcript is unavailable, stop and report the missing prerequisite plus the next setup command. Do not continue by guessing, printing secret values, or switching to another external service without user approval.

# VS Code CLI Tools

Tools for integrating with VS Code, primarily for viewing diffs.

## Requirements

VS Code must be installed with the `code` CLI available in PATH.

## Opening a Diff

Compare two files side by side in VS Code:

```bash
code -d <file1> <file2>
```

## Git Diffs in VS Code

### Simple Approach (no config needed)

Extract the old version to a temp file, then diff:

```bash
# Compare with previous commit
git show HEAD~1:path/to/file > .tmp/vscode-diff/old && code -d .tmp/vscode-diff/old path/to/file

# Compare with specific commit
git show abc123:path/to/file > .tmp/vscode-diff/old && code -d .tmp/vscode-diff/old path/to/file

# Compare staged version with working tree
git show :path/to/file > .tmp/vscode-diff/staged && code -d .tmp/vscode-diff/staged path/to/file
```

### Gotchas

- File must exist and have changes between the compared revisions
- Use `git log --oneline -5 -- path/to/file` to verify file has history before diffing

## When to Use

- Showing the user what changed in a file
- Comparing two versions of code
- Reviewing git changes visually

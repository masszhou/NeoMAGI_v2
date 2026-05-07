---
name: gdcli
description: Google Drive CLI for listing, searching, uploading, downloading, and sharing files and folders.
---
Derived from badlogic/pi-skills@75d32a382b0c8aafce356d68e17d2dc94c0c953b (MIT). NeoMAGI additions document execution and safety boundaries.

## NeoMAGI execution boundary

Run every command from this skill through NeoMAGI's governed `bash` tool so shell policy, timeout, audit logging, truncation, and artifact handling remain active. Commands using `{baseDir}` are resolved by NeoMAGI during `/skill:<name>` expansion to this skill's `location` path.

## Setup check

```bash
command -v gdcli && echo "gdcli found" || echo "gdcli missing"
gdcli accounts list
```

## Credentials

Uses OAuth files under `~/.gdcli/`. Do not print or copy those files.

## Sensitive operations

Read-only `ls`, `search`, and explicit downloads to a user-approved path are allowed after account selection. Require same-turn user confirmation before Drive `upload`, `mkdir`, `share --anyone`, or delete operations.

## Output hygiene

Do not record API keys, OAuth tokens, cookies, private email bodies, private Drive file contents, browser profile data, or non-public audio/transcript content in durable logs, findings, or summaries. Redact identifiers unless the user explicitly asks to preserve them.

## Failure mode

If a dependency, credential, OAuth account, browser, or transcript is unavailable, stop and report the missing prerequisite plus the next setup command. Do not continue by guessing, printing secret values, or switching to another external service without user approval.

# Google Drive CLI

Command-line interface for Google Drive operations.

## Installation

```bash
npm install -g @mariozechner/gdcli
```

## Setup

### Google Cloud Console (one-time)

1. [Create a new project](https://console.cloud.google.com/projectcreate) (or select existing)
2. [Enable the Google Drive API](https://console.cloud.google.com/apis/api/drive.googleapis.com)
3. [Set app name](https://console.cloud.google.com/auth/branding) in OAuth branding
4. [Add test users](https://console.cloud.google.com/auth/audience) (all Gmail addresses you want to use)
5. [Create OAuth client](https://console.cloud.google.com/auth/clients):
   - Click "Create Client"
   - Application type: "Desktop app"
   - Download the JSON file

### Configure gdcli

First check if already configured:
```bash
gdcli accounts list
```

If no accounts, guide the user through setup:
1. Ask if they have a Google Cloud project with Drive API enabled
2. If not, walk them through the Google Cloud Console steps above
3. Have them download the OAuth credentials JSON
4. Run: `gdcli accounts credentials ~/path/to/credentials.json`
5. Run: `gdcli accounts add <email>` (use `--manual` for browserless OAuth)

## Usage

Run `gdcli --help` for full command reference.

Common operations:
- `gdcli <email> ls [folderId]` - List files/folders
- `gdcli <email> ls --query "<query>"` - List with Drive query filter
- `gdcli <email> search "<text>"` - Full-text content search
- `gdcli <email> download <fileId> [destPath]` - Download a file
- `gdcli <email> upload <localPath> [--folder <folderId>]` - Upload a file
- `gdcli <email> mkdir <name>` - Create a folder
- `gdcli <email> share <fileId> --anyone` - Share publicly

## Search

**Two different commands:**
- `search "<text>"` - Searches inside file contents (fullText)
- `ls --query "<query>"` - Filters by metadata (name, type, date, etc.)

**Use `ls --query` for filename searches!**

## Query Syntax (for ls --query)

Format: `field operator value`. Combine with `and`/`or`, group with `()`.

**Operators:** `=`, `!=`, `contains`, `<`, `>`, `<=`, `>=`

**Examples:**
```bash
# By filename
ls --query "name = 'report.pdf'"           # exact match
ls --query "name contains 'IMG'"           # prefix match

# By type
ls --query "mimeType = 'application/pdf'"
ls --query "mimeType contains 'image/'"
ls --query "mimeType = 'application/vnd.google-apps.folder'"  # folders

# By date
ls --query "modifiedTime > '2024-01-01'"

# By owner/sharing
ls --query "'me' in owners"
ls --query "sharedWithMe"

# Exclude trash
ls --query "trashed = false"

# Combined
ls --query "name contains 'report' and mimeType = 'application/pdf'"
```

Ref: https://developers.google.com/drive/api/guides/ref-search-terms

## Data Storage

- `~/.gdcli/credentials.json` - OAuth client credentials
- `~/.gdcli/accounts.json` - Account tokens
- `~/.gdcli/downloads/` - Default download location

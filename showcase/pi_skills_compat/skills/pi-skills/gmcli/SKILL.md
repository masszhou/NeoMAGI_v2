---
name: gmcli
description: Gmail CLI for searching emails, reading threads, sending messages, managing drafts, and handling labels/attachments.
---
Derived from badlogic/pi-skills@75d32a382b0c8aafce356d68e17d2dc94c0c953b (MIT). NeoMAGI additions document execution and safety boundaries.

## NeoMAGI execution boundary

Run every command from this skill through NeoMAGI's governed `bash` tool so shell policy, timeout, audit logging, truncation, and artifact handling remain active. Commands using `{baseDir}` are resolved by NeoMAGI during `/skill:<name>` expansion to this skill's `location` path.

## Setup check

```bash
command -v gmcli && echo "gmcli found" || echo "gmcli missing"
gmcli accounts list
```

## Credentials

Uses OAuth files under `~/.gmcli/`. Do not print or copy those files.

## Sensitive operations

Search and thread reads may expose private email; summarize instead of copying full bodies by default. Require same-turn user confirmation before Gmail `send`, `drafts` mutation, label mutation, or any attachment download risk to a non-user-specified path.

## Output hygiene

Do not record API keys, OAuth tokens, cookies, private email bodies, private Drive file contents, browser profile data, or non-public audio/transcript content in durable logs, findings, or summaries. Redact identifiers unless the user explicitly asks to preserve them.

## Failure mode

If a dependency, credential, OAuth account, browser, or transcript is unavailable, stop and report the missing prerequisite plus the next setup command. Do not continue by guessing, printing secret values, or switching to another external service without user approval.

# Gmail CLI

Command-line interface for Gmail operations.

## Installation

```bash
npm install -g @mariozechner/gmcli
```

## Setup

### Google Cloud Console (one-time)

1. [Create a new project](https://console.cloud.google.com/projectcreate) (or select existing)
2. [Enable the Gmail API](https://console.cloud.google.com/apis/api/gmail.googleapis.com)
3. [Set app name](https://console.cloud.google.com/auth/branding) in OAuth branding
4. [Add test users](https://console.cloud.google.com/auth/audience) (all Gmail addresses you want to use)
5. [Create OAuth client](https://console.cloud.google.com/auth/clients):
   - Click "Create Client"
   - Application type: "Desktop app"
   - Download the JSON file

### Configure gmcli

First check if already configured:
```bash
gmcli accounts list
```

If no accounts, guide the user through setup:
1. Ask if they have a Google Cloud project with Gmail API enabled
2. If not, walk them through the Google Cloud Console steps above
3. Have them download the OAuth credentials JSON
4. Run: `gmcli accounts credentials ~/path/to/credentials.json`
5. Run: `gmcli accounts add <email>` (use `--manual` for browserless OAuth)

## Usage

Run `gmcli --help` for full command reference.

Common operations:
- `gmcli <email> search "<query>"` - Search emails using Gmail query syntax
- `gmcli <email> thread <threadId>` - Read a thread with all messages
- `gmcli <email> send --to <emails> --subject <s> --body <b>` - Send email
- `gmcli <email> labels list` - List all labels
- `gmcli <email> drafts list` - List drafts

## Data Storage

- `~/.gmcli/credentials.json` - OAuth client credentials
- `~/.gmcli/accounts.json` - Account tokens
- `~/.gmcli/attachments/` - Downloaded attachments

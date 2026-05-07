---
name: gccli
description: Google Calendar CLI for listing calendars, viewing/creating/updating events, and checking availability.
---
Derived from badlogic/pi-skills@75d32a382b0c8aafce356d68e17d2dc94c0c953b (MIT). NeoMAGI additions document execution and safety boundaries.

## NeoMAGI execution boundary

Run every command from this skill through NeoMAGI's governed `bash` tool so shell policy, timeout, audit logging, truncation, and artifact handling remain active. Commands using `{baseDir}` are resolved by NeoMAGI during `/skill:<name>` expansion to this skill's `location` path.

## Setup check

```bash
command -v gccli && echo "gccli found" || echo "gccli missing"
gccli accounts list
```

## Credentials

Uses OAuth files under `~/.gccli/`. Do not print or copy those files.

## Sensitive operations

Read-only `calendars`, `events`, `event`, and `freebusy` are allowed after account selection. Require same-turn user confirmation before Calendar `create`, `update`, or `delete` operations.

## Output hygiene

Do not record API keys, OAuth tokens, cookies, private email bodies, private Drive file contents, browser profile data, or non-public audio/transcript content in durable logs, findings, or summaries. Redact identifiers unless the user explicitly asks to preserve them.

## Failure mode

If a dependency, credential, OAuth account, browser, or transcript is unavailable, stop and report the missing prerequisite plus the next setup command. Do not continue by guessing, printing secret values, or switching to another external service without user approval.

# Google Calendar CLI

Command-line interface for Google Calendar operations.

## Installation

```bash
npm install -g @mariozechner/gccli
```

## Setup

### Google Cloud Console (one-time)

1. [Create a new project](https://console.cloud.google.com/projectcreate) (or select existing)
2. [Enable the Google Calendar API](https://console.cloud.google.com/apis/api/calendar-json.googleapis.com)
3. [Set app name](https://console.cloud.google.com/auth/branding) in OAuth branding
4. [Add test users](https://console.cloud.google.com/auth/audience) (all Gmail addresses you want to use)
5. [Create OAuth client](https://console.cloud.google.com/auth/clients):
   - Click "Create Client"
   - Application type: "Desktop app"
   - Download the JSON file

### Configure gccli

First check if already configured:
```bash
gccli accounts list
```

If no accounts, guide the user through setup:
1. Ask if they have a Google Cloud project with Calendar API enabled
2. If not, walk them through the Google Cloud Console steps above
3. Have them download the OAuth credentials JSON
4. Run: `gccli accounts credentials ~/path/to/credentials.json`
5. Run: `gccli accounts add <email>` (use `--manual` for browserless OAuth)

## Usage

Run `gccli --help` for full command reference.

Common operations:
- `gccli <email> calendars` - List all calendars
- `gccli <email> events <calendarId> [--from <dt>] [--to <dt>]` - List events
- `gccli <email> event <calendarId> <eventId>` - Get event details
- `gccli <email> create <calendarId> --summary <s> --start <dt> --end <dt>` - Create event
- `gccli <email> freebusy <calendarIds> --from <dt> --to <dt>` - Check availability

Use `primary` as calendarId for the main calendar.

## Date/Time Format

- Timed events: `YYYY-MM-DDTHH:MM:SSZ` (UTC) or `YYYY-MM-DDTHH:MM:SS` (local)
- All-day events: `YYYY-MM-DD` with `--all-day` flag

## Data Storage

- `~/.gccli/credentials.json` - OAuth client credentials
- `~/.gccli/accounts.json` - Account tokens

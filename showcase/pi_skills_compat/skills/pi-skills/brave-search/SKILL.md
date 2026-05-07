---
name: brave-search
description: Web search and content extraction via Brave Search API. Use for searching documentation, facts, or any web content. Lightweight, no browser required.
---
Derived from badlogic/pi-skills@75d32a382b0c8aafce356d68e17d2dc94c0c953b (MIT). NeoMAGI additions document execution and safety boundaries.

## NeoMAGI execution boundary

Run every command from this skill through NeoMAGI's governed `bash` tool so shell policy, timeout, audit logging, truncation, and artifact handling remain active. Commands using `{baseDir}` are resolved by NeoMAGI during `/skill:<name>` expansion to this skill's `location` path.

## Setup check

```bash
node --version && echo "node found" || echo "node missing"
test -f "{baseDir}/package.json" && echo "package.json found" || echo "package.json missing"
test -d "{baseDir}/node_modules" && echo "dependencies installed" || echo "run npm install in {baseDir}"
test -n "${BRAVE_API_KEY:-}" && echo "BRAVE_API_KEY is set" || echo "BRAVE_API_KEY is not set"
```

## Credentials

Requires `BRAVE_API_KEY`. Check only whether it is set; never echo the value. NeoMAGI may inject it through `resources.skillEnv.brave-search` when configured.

## Sensitive operations

Search and page extraction send the query or URL to Brave and target sites. Confirm before using `--content` on private, internal, or user-sensitive URLs.

## Output hygiene

Do not record API keys, OAuth tokens, cookies, private email bodies, private Drive file contents, browser profile data, or non-public audio/transcript content in durable logs, findings, or summaries. Redact identifiers unless the user explicitly asks to preserve them.

## Failure mode

If a dependency, credential, OAuth account, browser, or transcript is unavailable, stop and report the missing prerequisite plus the next setup command. Do not continue by guessing, printing secret values, or switching to another external service without user approval.

# Brave Search

Web search and content extraction using the official Brave Search API. No browser required.

## Setup

Requires a Brave Search API account with a free subscription. A credit card is required to create the free subscription (you won't be charged).

1. Create an account at https://api-dashboard.search.brave.com/register
2. Create a "Free AI" subscription
3. Create an API key for the subscription
4. Add to your shell profile (`~/.profile` or `~/.zprofile` for zsh):
   ```bash
   export BRAVE_API_KEY="your-api-key-here"
   ```
5. Install dependencies (run once):
   ```bash
   cd {baseDir}
   npm install
   ```

## Search

```bash
{baseDir}/search.js "query"                         # Basic search (5 results)
{baseDir}/search.js "query" -n 10                   # More results (max 20)
{baseDir}/search.js "query" --content               # Include page content as markdown
{baseDir}/search.js "query" --freshness pw          # Results from last week
{baseDir}/search.js "query" --freshness 2024-01-01to2024-06-30  # Date range
{baseDir}/search.js "query" --country DE            # Results from Germany
{baseDir}/search.js "query" -n 3 --content          # Combined options
```

### Options

- `-n <num>` - Number of results (default: 5, max: 20)
- `--content` - Fetch and include page content as markdown
- `--country <code>` - Two-letter country code (default: US)
- `--freshness <period>` - Filter by time:
  - `pd` - Past day (24 hours)
  - `pw` - Past week
  - `pm` - Past month
  - `py` - Past year
  - `YYYY-MM-DDtoYYYY-MM-DD` - Custom date range

## Extract Page Content

```bash
{baseDir}/content.js https://example.com/article
```

Fetches a URL and extracts readable content as markdown.

## Output Format

```
--- Result 1 ---
Title: Page Title
Link: https://example.com/page
Age: 2 days ago
Snippet: Description from search results
Content: (if --content flag used)
  Markdown content extracted from the page...

--- Result 2 ---
...
```

## When to Use

- Searching for documentation or API references
- Looking up facts or current information
- Fetching content from specific URLs
- Any task requiring web search without interactive browsing

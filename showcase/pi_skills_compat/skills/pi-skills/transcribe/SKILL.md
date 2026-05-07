---
name: transcribe
description: Speech-to-text transcription using Groq Whisper API. Supports m4a, mp3, wav, ogg, flac, webm.
---
Derived from badlogic/pi-skills@75d32a382b0c8aafce356d68e17d2dc94c0c953b (MIT). NeoMAGI additions document execution and safety boundaries.

## NeoMAGI execution boundary

Run every command from this skill through NeoMAGI's governed `bash` tool so shell policy, timeout, audit logging, truncation, and artifact handling remain active. Commands using `{baseDir}` are resolved by NeoMAGI during `/skill:<name>` expansion to this skill's `location` path.

## Setup check

```bash
curl --version && echo "curl found" || echo "curl missing"
test -x "{baseDir}/transcribe.sh" && echo "transcribe.sh executable" || echo "transcribe.sh missing"
test -n "${GROQ_API_KEY:-}" && echo "GROQ_API_KEY is set" || echo "GROQ_API_KEY is not set"
```

## Credentials

Requires `GROQ_API_KEY`. Check only whether it is set; never echo the value. NeoMAGI may inject it through `resources.skillEnv.transcribe` when configured.

## Sensitive operations

Running `transcribe.sh <audio-file>` uploads local audio to Groq. Require same-turn user confirmation that the file path is correct and the audio is non-sensitive or approved for upload to Groq.

## Output hygiene

Do not record API keys, OAuth tokens, cookies, private email bodies, private Drive file contents, browser profile data, or non-public audio/transcript content in durable logs, findings, or summaries. Redact identifiers unless the user explicitly asks to preserve them.

## Failure mode

If a dependency, credential, OAuth account, browser, or transcript is unavailable, stop and report the missing prerequisite plus the next setup command. Do not continue by guessing, printing secret values, or switching to another external service without user approval.

# Transcribe

Speech-to-text using Groq Whisper API.

## Setup

The script needs `GROQ_API_KEY` environment variable. Check if already set:
```bash
test -n "${GROQ_API_KEY:-}" && echo "GROQ_API_KEY is set" || echo "GROQ_API_KEY is not set"
```

If not set, guide the user through setup:
1. Ask if they have a Groq API key
2. If not, have them sign up at https://console.groq.com/ and create an API key
3. Have them add to their shell profile (~/.zshrc or ~/.bashrc):
   ```bash
   export GROQ_API_KEY="<their-api-key>"
   ```
4. Then run `source ~/.zshrc` (or restart terminal)

## Usage

```bash
{baseDir}/transcribe.sh <audio-file>
```

## Supported Formats

- m4a, mp3, wav, ogg, flac, webm
- Max file size: 25MB

## Output

Returns plain text transcription with punctuation and proper capitalization to stdout.

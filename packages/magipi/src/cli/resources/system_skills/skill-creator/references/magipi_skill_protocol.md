# MagiPi Skill Protocol (normative)

This document is the authoritative description of what a valid, well-behaved magipi skill
looks like. It is derived from the magipi runtime implementation (resource loader, shell
policy, skill env grants). Follow it when creating a new skill or adapting a foreign one.

## Contents

1. Discovery and materialization
2. SKILL.md format
3. Naming rules
4. Body conventions
5. Execution boundary (shell and file policy)
6. Credentials: skillEnv and secrets files
7. Recommended body sections
8. Progressive disclosure layout
9. Validation checklist

## 1. Discovery and materialization

- Active skills come ONLY from the current workspace: `.magipi/skills/<skill-name>/SKILL.md`.
  Global directories, settings paths, symlinks resolving outside `.magipi/skills`, and
  extension-contributed paths are NOT active skill sources.
- `.magipi/skills/.system/` is host-managed (re-synced from the magipi package on every
  startup and `/reload`). Never install or edit skills there. To customize a system skill,
  copy it to `.magipi/skills/<skill-name>/`; the workspace copy wins name collisions.
- The skill snapshot refreshes only at workspace startup or explicit `/reload`. A skill
  materialized mid-session is invisible until the user runs `/reload`.
- Skill name collisions keep the first discovery; duplicates are reported as diagnostics.

## 2. SKILL.md format

Every skill is a directory with a `SKILL.md` at its root. `SKILL.md` starts with YAML
frontmatter followed by a Markdown body:

```markdown
---
name: my-skill
description: What it does and when to use it. This is the only trigger signal.
---
<body>
```

- `name` (required): must equal the directory name. See naming rules below.
- `description` (required, max 1024 chars): the ONLY text the model sees before deciding
  to use the skill. Include both what the skill does and concrete triggers ("Use when ...").
  Do not put "when to use" prose only in the body — the body is loaded after triggering.
- `disable-model-invocation: true` (optional): hides the skill from `<available_skills>`;
  it stays usable via the explicit `/skill:<name>` command.
- The frontmatter parser is flat `key: value` only — no nested YAML blocks, no lists.
  Any other keys are ignored by magipi; drop foreign metadata (e.g. `allowed-tools`,
  `metadata:`, `license:` blocks) or move the information into the body.

## 3. Naming rules

- Lowercase `a-z`, digits `0-9`, hyphens only; max 64 chars.
- No leading/trailing hyphen, no consecutive hyphens.
- `name` must match the parent directory name exactly.
- Prefer short verb-led phrases (`pdf-rotate`, `gh-address-comments`).

## 4. Body conventions

- Write imperative instructions addressed to the agent that will execute the skill.
- The body is loaded by the model with the ordinary `read` tool after triggering, or
  expanded inline by the explicit `/skill:<name>` command.
- `{baseDir}` is replaced with the skill directory path only during `/skill:` expansion.
  For model-triggered use, instruct readers to resolve relative paths against the
  directory containing SKILL.md. Safe pattern: mention both ("paths are relative to this
  skill's directory; `{baseDir}` resolves to it under `/skill:` expansion").
- Keep the body under ~500 lines; move detail into `references/` (see section 8).

## 5. Execution boundary (shell and file policy)

All skill work runs through magipi's governed atomic tools (`read`, `bash`, `edit`,
`write`, `grep`, `find`, `ls`). The policies below are enforced by the runtime; a skill
whose instructions violate them will hard-fail at execution time, so adapt instructions
up front:

- **Everything is cwd-bound.** `read`/`write`/`edit` refuse paths outside the workspace
  (symlinks are resolved and checked). Bash redirects, `-o`/`--output` targets, and
  upload sources (`curl -d @file`, `-T`) must stay inside the workspace.
- **`> /dev/null` and `> /tmp/...` are BLOCKED** (paths escape the workspace). The loader
  warns at load time about skill bodies containing such redirects. Rewrite snippets:
  - `command -v node >/dev/null` → `command -v node` (let output show)
  - `... > /tmp/out.json` → `... > tmp/out.json` (workspace-relative)
- **Downloads are fine, scoped to the workspace.** `git clone`/`curl -o` into a
  workspace-relative path works. Download once and reuse; do not re-fetch on every run.
- **Sensitive paths are blocked** (`~/.ssh`, shell profiles, system credential stores).
  Never instruct edits to `~/.profile`, `~/.zshrc`, etc. Use skillEnv instead (section 6).
- **Bash env is allowlisted.** Arbitrary host env vars are NOT visible to skill commands.
  An API key exported in the user's shell will not reach the skill; this is the most
  common foreign-skill assumption that must be adapted.
- **No daemons.** Commands run with timeouts; long-lived background processes are out of
  scope for skills.
- Long output is truncated; design scripts to print compact, parseable results.

## 6. Credentials: skillEnv and secrets files

Skills never read secrets from the user's shell environment. The magipi-native protocol:

1. Workspace `.magipi/settings.json` declares, per skill:

   ```json
   {
     "resources": {
       "skillEnv": {
         "my-skill": {
           "envFile": ".magipi/secrets/my-skill.env",
           "allow": ["MY_API_KEY"]
         }
       }
     }
   }
   ```

2. `.magipi/secrets/my-skill.env` holds `KEY=value` lines (file mode 0600). The
   `.magipi/secrets/` directory must be gitignored.
3. The grant activates when the model reads the skill's SKILL.md (or the user runs
   `/skill:<name>`), lasts for the current run only, covers one skill at a time, and is
   injected ONLY into model-originated `bash` calls.
4. Values shorter than 8 chars or matching common placeholders (`test`, `dummy`,
   `secret`, ...) are rejected as low-quality; the placeholder file must be filled with a
   real value before the grant works.
5. In skill bodies: check presence with `test -n "${MY_API_KEY:-}"`; NEVER echo the
   value; never write it to files or logs.

## 7. Recommended body sections

The proven adaptation template (used by the pi-skills compatibility pack). Include the
sections that apply, in this order, before the skill's own instructions:

```markdown
## MagiPi execution boundary
Run every command through magipi's governed `bash` tool... (one short paragraph)

## Setup check
```bash
command -v node && echo "node found" || echo "node missing"
test -n "${MY_API_KEY:-}" && echo "MY_API_KEY is set" || echo "MY_API_KEY is not set"
```

## Credentials
Requires `MY_API_KEY` via `resources.skillEnv.my-skill`. Check only whether it is set;
never echo the value.

## Sensitive operations
(what leaves the machine, what to confirm with the user first)

## Output hygiene
Do not record API keys, tokens, private content in durable logs or summaries.

## Failure mode
If a dependency or credential is unavailable, stop and report the missing prerequisite
plus the next setup command. Do not guess or switch services without user approval.
```

For derived/adapted skills, keep upstream attribution and license notes as the first body
line (e.g. `Derived from <repo>@<commit> (<license>). MagiPi additions document execution
and safety boundaries.`).

## 8. Progressive disclosure layout

```
my-skill/
├── SKILL.md          # required; lean; <500 lines
├── scripts/          # executable helpers (run via bash; read into context only to debug)
├── references/       # docs loaded on demand; link each from SKILL.md and say when to read it
└── assets/           # files used in outputs (templates, boilerplate); never loaded into context
```

- Metadata (name + description) is always in context; the body only after triggering;
  bundled files only when needed. Spend tokens accordingly.
- Do NOT add README.md, CHANGELOG.md, or installation guides inside a skill.
- Keep references one level deep and linked directly from SKILL.md.

## 9. Validation checklist

Run `python3 <skill-creator>/scripts/validate_skill.py <skill-dir>` (resolve
`<skill-creator>` to this skill's own directory). It checks, mirroring the loader:

- SKILL.md exists and frontmatter parses
- `name` present, valid charset/length, equals directory name
- `description` present, non-empty, ≤1024 chars
- no policy-incompatible redirects (`>/dev/null`, `>/tmp/...`) in the body
- referenced relative paths (`references/...`, `scripts/...`) that exist as literal
  mentions resolve inside the skill directory

A clean validation plus a `/reload` showing the skill in `<available_skills>` is the
acceptance bar for any new or installed skill.

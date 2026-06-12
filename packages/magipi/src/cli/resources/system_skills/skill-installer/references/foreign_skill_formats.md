# Foreign skill ecosystems and how to adapt them to magipi

Agent-skill repos across ecosystems share one core mechanism: a folder with a markdown
instruction file (usually `SKILL.md` with YAML frontmatter) plus optional scripts and
reference docs, surfaced to the model via a name+description index. The differences are
in metadata fields, install locations, tool assumptions, and credential handling. This
reference maps the known ecosystems to the magipi protocol.

## Contents

1. Ecosystem map
2. Detection heuristics
3. Adaptation checklist (any source)
4. Credential and OS-resource mapping
5. Things that are NOT skills

## 1. Ecosystem map

### Anthropic / Claude Code skills (closest relative)

- Layout: `<name>/SKILL.md` with frontmatter `name`, `description`, sometimes
  `allowed-tools`, `license`, `metadata`. Collections often live in `skills/` or
  `.claude/skills/`; plugins add `.claude-plugin/` manifests.
- Adapt: keep `name`/`description`; drop other frontmatter keys (magipi ignores them —
  if `allowed-tools` implies a safety boundary, restate it in the body). Bodies often
  reference `Bash`, `Read`, `Write` tools — magipi's lowercase `bash`/`read`/`write`
  equivalents exist, so instructions usually transfer with light edits.

### OpenAI / Codex skills (openai/skills)

- Layout: `<name>/SKILL.md` (+ `agents/openai.yaml` UI metadata, `scripts/`,
  `references/`, `assets/`). Installed to `$CODEX_HOME/skills`.
- Adapt: drop the `agents/` directory entirely (UI metadata has no magipi equivalent);
  drop nested `metadata:` frontmatter blocks. Body conventions (progressive disclosure,
  imperative form) match magipi's directly.

### Pi skills (badlogic/pi-skills and derivatives)

- Layout: `<name>/SKILL.md` + node scripts, `{baseDir}` placeholders.
- Adapt: `{baseDir}` works in magipi only under `/skill:` expansion; add the note that
  paths resolve relative to the skill directory. Setup snippets routinely use
  `command -v X` with redirects to `/dev/null` — strip those redirects (shell policy
  blocks them). See `showcase/pi_skills_compat/` in the NeoMAGI repo for six fully
  adapted examples of exactly this ecosystem.

### Other agent-CLI ecosystems (OpenClaw, Hermes, Gemini CLI, ...)

- Layouts vary: `skills/`, `abilities/`, `tools/` folders with markdown + scripts; some
  use TOML/JSON manifests instead of frontmatter.
- Adapt: find the instruction file, synthesize a magipi frontmatter (`name` from the
  folder, `description` from the manifest/first paragraph), and carry the body over.
  If there is no markdown instruction file at all (pure code), this is not a skill —
  see section 5.

## 2. Detection heuristics

Run `fetch_repo.py` first; it lists every `SKILL.md` and prints layout hints. Then:

- Repo root has `SKILL.md` → single-skill repo; install with the repo name.
- `skills/<name>/SKILL.md` or `.claude/skills/<name>/SKILL.md` → collection; ask the
  user which ones to install if more than three and the user did not say "all".
- Frontmatter missing → look for a manifest (`plugin.json`, `marketplace.json`,
  `*.toml`) or README to recover name/description.
- Scripts present → identify the runtime (node/python/bash) and check whether the
  workspace has it before promising the skill works.

## 3. Adaptation checklist (any source)

Read `.magipi/skills/.system/skill-creator/references/magipi_skill_protocol.md` for the
target format, then for each skill:

1. **Frontmatter**: keep/synthesize `name` (must match install dir; lowercase-hyphen)
   and `description` (≤1024 chars, contains "use when" triggers). Remove all other keys;
   the parser is flat key:value.
2. **Attribution**: first body line `Derived from <repo>@<commit-or-ref> (<license>).
   MagiPi additions document execution and safety boundaries.` Keep upstream LICENSE
   files inside the skill directory if present.
3. **Policy sweep** over the body and bundled scripts:
   - strip redirects targeting `/dev/null` or `/tmp` (rewrite to workspace paths or drop)
   - rewrite absolute output paths to workspace-relative ones
   - remove "add to your shell profile" credential steps → skillEnv (section 4)
   - remove `sudo`, global installs (`npm install -g`, `pip install --user`) → prefer
     skill-local installs (`npm install` in the skill dir) or tell the user what to install
   - background daemons/watchers do not survive magipi's timeout model — restructure to
     one-shot commands or document the limitation
4. **MagiPi sections**: prepend the execution-boundary/setup-check/credentials/output-
   hygiene/failure-mode sections per the protocol reference §7.
5. **Validate**: `python3 .magipi/skills/.system/skill-creator/scripts/validate_skill.py
   .magipi/skills/<name>` must pass.

## 4. Credential and OS-resource mapping

| Upstream pattern | MagiPi equivalent |
| --- | --- |
| `export API_KEY=...` in shell profile | `resources.skillEnv.<name>` + `.magipi/secrets/<name>.env` (install_skill.py `--env`) |
| reads `~/.config/<tool>/credentials` | not reachable (cwd-bound + sensitive paths); store a workspace copy under `.magipi/secrets/` only if the user explicitly provides it |
| OAuth browser flow scripts | run as ordinary governed bash; tokens must land in workspace files, not home-dir caches |
| global CLI dependency (`brew install X`) | detect with `command -v X`; report to the user; never attempt system installs |
| Docker / system services | out of scope for the skill itself; document as a user-managed prerequisite in the Setup check |

Empty values in the secrets placeholder are deliberately inert (the grant rejects them),
so installing a skill never activates a half-configured credential.

## 5. Things that are NOT skills

- **MCP servers** (`mcp.json`, server processes): magipi has no MCP client; tell the
  user this part cannot be installed as a skill.
- **Slash-command / prompt packs** (`commands/`, `prompts/` with template markdown):
  these map to `.magipi/prompts/` prompt templates, not skills. Offer to copy them
  there instead.
- **Extensions/plugins with executable host hooks** (TypeScript/Python plugin code):
  magipi extensions are a separate, trusted-code mechanism; do not auto-install code as
  an extension. Surface the option to the user and stop.
- **Pure libraries/apps**: a repo without agent instructions is a dependency, not a
  skill. A skill may wrap it, but write that skill with skill-creator instead.

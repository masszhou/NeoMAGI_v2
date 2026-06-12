---
name: skill-creator
description: Create a new magipi skill or update an existing one. Use when the user wants to add a skill, package a workflow/tool integration/domain knowledge as a reusable skill, or asks how skills work in magipi. Also the protocol authority consulted by skill-installer when adapting foreign skills.
---
# Skill Creator

Create skills that extend magipi with specialized knowledge, workflows, and tools.
Paths below are relative to this skill's directory (`{baseDir}` under `/skill:` expansion).

A skill is a directory under the workspace `.magipi/skills/` containing a `SKILL.md`
(YAML frontmatter `name` + `description`, then Markdown instructions) plus optional
`scripts/`, `references/`, and `assets/`. The full normative format — discovery rules,
naming, execution boundary, credentials protocol, validation — is in
`references/magipi_skill_protocol.md`. Read it before writing any skill files.

## Core principles

- **Concise is key.** The context window is shared. Assume the executing agent is already
  smart; add only knowledge it cannot have (procedures, schemas, tested commands, domain
  facts). Challenge every paragraph's token cost.
- **Match freedom to fragility.** Reliable-but-fragile sequences become scripts (low
  freedom); heuristic work stays prose (high freedom); preferred-but-flexible patterns
  become pseudocode or parameterized examples (medium freedom).
- **Description is the trigger.** Only frontmatter `name` + `description` are visible
  before the skill fires. Put what it does AND when to use it there, never only in the body.
- **Policy-clean by construction.** magipi blocks shell redirects to `/dev/null` or
  `/tmp`, out-of-cwd writes, shell-profile edits, and arbitrary host env vars. Write
  instructions that pass the execution boundary in `references/magipi_skill_protocol.md`
  §5–6 the first time.

## Creation workflow

1. **Understand with concrete examples.** Ask the user for 2–3 real requests the skill
   should handle and what should trigger it. Skip only if usage is already clear.
2. **Plan reusable contents.** For each example, work out how to execute it from scratch,
   then extract what is worth bundling: repeated code → `scripts/`, looked-up knowledge →
   `references/`, output boilerplate → `assets/`.
3. **Initialize.** Run:
   ```bash
   python3 scripts/init_skill.py <skill-name> --path .magipi/skills [--resources scripts,references,assets]
   ```
   (resolve `scripts/init_skill.py` against this skill's directory). It validates the
   name, creates the directory, and writes a SKILL.md template with the magipi sections.
4. **Edit.** Fill the frontmatter description (what + when, ≤1024 chars). Write the body
   imperatively for another agent. Implement bundled resources; actually run every script
   you add. If the skill needs credentials or external APIs, follow the skillEnv protocol
   (`references/magipi_skill_protocol.md` §6) — never shell-profile exports.
5. **Validate.**
   ```bash
   python3 scripts/validate_skill.py .magipi/skills/<skill-name>
   ```
   Fix reported issues and re-run until clean.
6. **Activate and iterate.** Tell the user to run `/reload`, confirm the skill appears,
   then exercise it on the original examples. Fold observed struggles back into SKILL.md
   or the bundled resources.

## What NOT to do

- No README.md/CHANGELOG.md/setup guides inside a skill — SKILL.md is the only doc.
- No "When to use" section in the body — that belongs in the description.
- No frontmatter fields beyond `name`, `description`, `disable-model-invocation`.
- Never write secrets, tokens, or user-private data into skill files.

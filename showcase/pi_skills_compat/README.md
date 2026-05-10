# Pi Skills Compatibility Pack

NeoMAGI-compatible prompt-resource pack derived from `badlogic/pi-skills` at `75d32a382b0c8aafce356d68e17d2dc94c0c953b` (MIT).

This pack is a showcase fixture for Pi-style Agent Skills compatibility. It does not add native
Google, Gmail, Drive, Calendar, Brave, Groq, browser, or VS Code providers to NeoMAGI.

## Contents

- `brave-search`
- `browser-tools`
- `gmcli`
- `transcribe`
- `vscode`
- `youtube-transcript`

## Expose The Pack

Project-level copy or symlink:

```bash
mkdir -p .magipi/skills
cp -R showcase/pi_skills_compat/skills/pi-skills .magipi/skills/pi-skills
```

Runtime skill discovery is workspace-local. Settings-level, global, symlinked external, and pool
paths are not active skill inputs; copy the pack into `.magipi/skills/` before startup or `/reload`.

## Execution Boundary

All helper commands in these skills must run through NeoMAGI's governed `bash` tool. The skills
only describe prompt-level behavior and safety gates; they do not bypass shell policy or audit.

Before using helper-backed skills, run each skill's setup check. Do not print API keys, OAuth
tokens, cookies, mail bodies, Drive file contents, browser profile data, or non-public audio
transcripts into session logs or findings.

## Upstream Notes

The upstream README at this ref lists a `subagent` requirement, but the repository contains no
`subagent/SKILL.md`. This pack generates only the six concrete skill directories found in the
pinned source tree.

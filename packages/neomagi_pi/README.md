# neomagi-pi

`neomagi-pi` is the package boundary for the NeoMAGI Pi-compatible local agent shell.

It intentionally preserves the current top-level Python import names (`cli`, `ai_provider`, `agent_core`, `tui`, `storage`, `policy`, and `infra`) while exposing the installable console command `magipi`.

Development from the repository root still uses:

```bash
uv run python -m cli --help
```

Installed or explicit package entrypoint usage can use:

```bash
uv run magipi --help
uv run --package neomagi-pi magipi --help
```

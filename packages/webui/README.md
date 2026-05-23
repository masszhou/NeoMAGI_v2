# neomagi-webui

Operator-facing NeoMAGI WebUI package.

First slice: authenticated, read-only database dashboard over the configured
Postgres business schema.

Generate `WEBUI_PASSWORD_HASH` with the default command:

```bash
uv run --package neomagi-webui magipi-webui hash-password
```

The generated PBKDF2-SHA256 hash uses the supported 600k iteration floor; do not
hand-edit the iteration count downward.

# NeoMAGI_v2

NeoMAGI v2 is a local-first personal agent CLI. The P1 Pi-compatible shell now
lives under `packages/neomagi_pi/` as the `neomagi-pi` package.

Development entrypoints:

```bash
uv run python -m cli --help
uv run python -m cli --playback tests/fixtures/pi_compat/assistant_text_delta
```

Package entrypoints:

```bash
uv run magipi --help
uv run --package neomagi-pi magipi --help
uv build --package neomagi-pi --out-dir dist/neomagi-pi
```

`neomagi` remains as a one-cycle compatibility console script; new docs and
tests should prefer `magipi` for installed/package usage.

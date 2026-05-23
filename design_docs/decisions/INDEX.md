---
doc_id: 019d68bb-7950-7b46-84f2-898fd7508f93
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-07T18:16:50+02:00
---
# Decision Index

使用轻量决策日志（ADR-lite）：关键取舍可追溯，文档保持简短。

| ID | Title | Status | Date | File |
| --- | --- | --- | --- | --- |
| 0001 | Adopt ADR-lite decision log | accepted | 2026-02-16 | `design_docs/decisions/0001-adopt-adr-lite-decision-log.md` |
| 0002 | Use uv as python package manager | accepted | 2026-02-16 | `design_docs/decisions/0002-use-uv-as-python-package-manager.md` |
| 0003 | Use just as command runner | accepted | 2026-02-16 | `design_docs/decisions/0003-use-just-as-command-runner.md` |
| 0004 | Use PostgreSQL 18 with pgvector and ParadeDB pg_search | accepted | 2026-04-24 | `design_docs/decisions/0004-use-postgresql-pgvector-instead-of-sqlite.md` |
| 0005 | ParadeDB tokenization ICU primary Jieba fallback | accepted | 2026-04-24 | `design_docs/decisions/0005-paradedb-tokenization-icu-primary-jieba-fallback.md` |
| 0006 | Database schema default neomagi | accepted | 2026-04-24 | `design_docs/decisions/0006-database-schema-default-neomagi.md` |
| 0007 | Database hard dependency fail fast | accepted | 2026-04-24 | `design_docs/decisions/0007-database-hard-dependency-fail-fast.md` |
| 0008 | Memory truth & workspace projection | accepted | 2026-04-25 | `design_docs/decisions/0008-memory-truth-closure-postgres-with-workspace-projection.md` |
| 0009 | Pi CLI product equivalence contract | accepted | 2026-04-25 | `design_docs/decisions/0009-pi-cli-product-equivalence-contract.md` |
| 0010 | Use pydantic v2 for protocol types | accepted | 2026-04-25 | `design_docs/decisions/0010-use-pydantic-v2-for-protocol-types.md` |
| 0011 | Freeze pi-mono baseline at 97a38bf6 | accepted | 2026-04-25 | `design_docs/decisions/0011-freeze-pi-mono-baseline-at-97a38bf6.md` |
| 0012 | Python-native extension MVP boundary | accepted | 2026-04-25 | `design_docs/decisions/0012-python-native-extension-mvp-boundary.md` |
| 0013 | Python async for Pi Promise extension methods | accepted | 2026-04-25 | `design_docs/decisions/0013-python-async-for-pi-promise-extension-methods.md` |
| 0014 | Extend async protocol rule to extension UI context | accepted | 2026-04-25 | `design_docs/decisions/0014-extend-async-protocol-rule-to-extension-ui-context.md` |
| 0015 | Native ANSI TUI runtime | accepted | 2026-04-25 | `design_docs/decisions/0015-native-ansi-tui-runtime.md` |
| 0016 | Provider-side prompt cache contract | accepted | 2026-04-26 | `design_docs/decisions/0016-provider-side-prompt-cache-contract.md` |
| 0017 | Use provider SDKs for OpenAI and Anthropic | accepted | 2026-04-26 | `design_docs/decisions/0017-use-provider-sdks-for-openai-and-anthropic.md` |
| 0018 | Package neomagi_pi as monorepo product boundary | accepted | 2026-05-06 | `design_docs/decisions/0018-package-neomagi-pi-as-monorepo-product-boundary.md` |
| 0019 | User config dir as default database secret source | accepted | 2026-05-08 | `design_docs/decisions/0019-user-config-dir-as-default-env-source.md` |
| 0020 | Magipi workspace and global resource layout | accepted | 2026-05-10 | `design_docs/decisions/0020-magipi-workspace-and-global-resource-layout.md` |
| 0021 | Workspace materialized skills and env grants | accepted | 2026-05-10 | `design_docs/decisions/0021-workspace-materialized-skills-and-env-grants.md` |
| 0022 | Align magipi package directory name | accepted | 2026-05-12 | `design_docs/decisions/0022-align-magipi-package-directory-name.md` |
| 0023 | Agent core pi-mono protocol parity | accepted | 2026-05-17 | `design_docs/decisions/0023-agent-core-pi-mono-protocol-parity.md` |
| 0024 | Introduce WebUI operator surface | accepted | 2026-05-23 | `design_docs/decisions/0024-introduce-webui-operator-surface.md` |

## Amendments

- 2026-04-26: ADR-0015 §影响 amended for P1-M1 follow-ups: anchored renderer / DSR ownership, lifecycle cursor placement, late CPR discard, Spinner primitive, and `packages/magipi/src/tui/components/` substrate primitive boundary.
- 2026-04-30: ADR-0015 §影响 amended for P1-M4 follow-up render modes: `present()` remains the canvas frame entry, while command mode uses `present_live()` / `commit_lines()` / `clear_live_region()` with SGR reset and synchronized live-region output.
- 2026-05-10: ADR-0019 amended by ADR-0020: the default user database config file is `secrets/database.env`, and the surrounding NeoMAGI user config layout is defined by ADR-0020.
- 2026-05-10: ADR-0020 amended by ADR-0021: only workspace materialized skills are active runtime skills; skill pool/global skill roots are not provider-visible; `/skill:<name>` is a development/debug shortcut.
- 2026-05-12: ADR-0018 amended by ADR-0022: the Pi-compatible agent shell source directory is `packages/magipi/`; CLI/import/runtime behavior remains unchanged.

from __future__ import annotations

from pathlib import Path

from scripts.pi_skills.build_compat_pack import (
    build_compat_pack,
    rewrite_skill_markdown,
    should_copy_file,
)


def test_rewrite_inserts_neomagi_sections_and_redacts_env_checks() -> None:
    source = (
        "---\n"
        "name: transcribe\n"
        "description: Speech-to-text transcription using Groq Whisper API.\n"
        "---\n"
        "# Transcribe\n\n"
        "```bash\n"
        "echo $GROQ_API_KEY\n"
        "```\n\n"
        "{baseDir}/transcribe.sh <audio-file>\n"
    )

    rewritten = rewrite_skill_markdown("transcribe", source)

    assert "Derived from badlogic/pi-skills@" in rewritten
    assert "## NeoMAGI execution boundary" in rewritten
    assert "## Setup check" in rewritten
    assert "## Sensitive operations" in rewritten
    assert "upload to Groq" in rewritten
    assert "transcribe.sh <audio-file>" in rewritten
    assert "echo $GROQ_API_KEY" not in rewritten
    assert 'test -n "${GROQ_API_KEY:-}"' in rewritten
    assert ">/dev/null" not in rewritten


def test_copy_filter_allows_helper_files_and_blocks_runtime_artifacts() -> None:
    assert should_copy_file(Path("SKILL.md"))
    assert should_copy_file(Path("package-lock.json"))
    assert should_copy_file(Path("browser-cookies.js"))
    assert should_copy_file(Path("transcribe.sh"))

    assert not should_copy_file(Path("node_modules/package/index.js"))
    assert not should_copy_file(Path("config"))
    assert not should_copy_file(Path("credentials.json"))
    assert not should_copy_file(Path("accounts.json"))
    assert not should_copy_file(Path("downloads/audio.mp3"))
    assert not should_copy_file(Path("screenshots/current.png"))
    assert not should_copy_file(Path("client_secret_123.json"))


def test_build_compat_pack_copies_allowed_files_and_rewrites_skill(tmp_path) -> None:
    source = tmp_path / "source"
    skill = source / "brave-search"
    (skill / "node_modules" / "blocked").mkdir(parents=True)
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: brave-search\n"
        "description: Web search and content extraction via Brave Search API.\n"
        "---\n"
        "# Brave Search\n\n"
        "{baseDir}/search.js \"query\"\n",
        encoding="utf-8",
    )
    (skill / "search.js").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (skill / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    (skill / "credentials.json").write_text("{}\n", encoding="utf-8")
    (skill / "node_modules" / "blocked" / "index.js").write_text("// no\n", encoding="utf-8")
    (source / "LICENSE").write_text("MIT\n", encoding="utf-8")

    output = tmp_path / "output"
    summary = build_compat_pack(source, output, expected_skills=("brave-search",))

    generated_skill = output / "skills" / "pi-skills" / "brave-search" / "SKILL.md"
    assert summary.skills == ("brave-search",)
    assert generated_skill.is_file()
    assert (output / "LICENSE.pi-skills").read_text(encoding="utf-8") == "MIT\n"
    assert (generated_skill.parent / "search.js").is_file()
    assert not (generated_skill.parent / "credentials.json").exists()
    assert not (generated_skill.parent / "node_modules").exists()
    assert "## Credentials" in generated_skill.read_text(encoding="utf-8")


def test_rewrite_policy_compatible_setup_check_mentions_skill_env_settings() -> None:
    rewritten = rewrite_skill_markdown(
        "brave-search",
        "---\nname: brave-search\ndescription: Search.\n---\n# Brave\n",
    )

    assert ">/dev/null" not in rewritten
    assert 'test -n "${BRAVE_API_KEY:-}"' in rewritten
    assert "resources.skillEnv.brave-search" in rewritten

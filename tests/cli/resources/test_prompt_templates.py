from __future__ import annotations

from cli.resources.prompt_templates import (
    expand_prompt_template,
    load_prompt_templates,
    parse_command_args,
    substitute_args,
)


def test_prompt_template_loads_frontmatter_and_fallback(tmp_path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "review.md").write_text(
        "---\ndescription: Review code\nargument-hint: FILE\n---\nReview $1\n",
        encoding="utf-8",
    )
    (prompts / "explain.md").write_text("\nExplain this file\n\nDetails\n", encoding="utf-8")

    loaded = load_prompt_templates([prompts])

    by_name = {template.name: template for template in loaded.templates}
    assert by_name["review"].description == "Review code"
    assert by_name["review"].argument_hint == "FILE"
    assert by_name["explain"].description == "Explain this file"


def test_prompt_template_substitution_matrix() -> None:
    assert parse_command_args('"one two" three') == ["one two", "three"]
    assert substitute_args("$1|$2|$3|$@|$ARGUMENTS|${@:2}|${@:1:2}", ["a", "b", "$1"]) == (
        "a|b|$1|a b $1|a b $1|b $1|a b"
    )


def test_expand_prompt_template_only_matches_slash_command(tmp_path) -> None:
    template_path = tmp_path / "ask.md"
    template_path.write_text("Ask $1", encoding="utf-8")
    loaded = load_prompt_templates([template_path])

    assert expand_prompt_template("/ask question", list(loaded.templates)) == "Ask question"
    assert expand_prompt_template("ask question", list(loaded.templates)) is None
    assert expand_prompt_template("/missing question", list(loaded.templates)) is None

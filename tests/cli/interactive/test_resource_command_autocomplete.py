from __future__ import annotations

from cli.interactive.app import InteractiveController
from cli.interactive.runtime import InteractiveAgentRuntime
from tui.app import TUIApp
from tui.stdin_buffer import KeyEvent


def test_skill_and_prompt_templates_contribute_autocomplete_items(tmp_path) -> None:
    (tmp_path / ".magipi" / "skills" / "reviewer").mkdir(parents=True)
    (tmp_path / ".magipi" / "prompts").mkdir(parents=True)
    (tmp_path / ".magipi" / "skills" / "reviewer" / "SKILL.md").write_text(
        "---\nname: reviewer\ndescription: Review files.\n---\nReview carefully.\n",
        encoding="utf-8",
    )
    (tmp_path / ".magipi" / "prompts" / "ask.md").write_text(
        "---\ndescription: Ask a question.\nargument-hint: '<topic>'\n---\nQuestion: $1\n",
        encoding="utf-8",
    )
    app = TUIApp()
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    controller = InteractiveController(tui_app=app, runtime=runtime)
    try:
        controller.bootstrap()
        app.inject_input(KeyEvent("/", raw="/"))
        app.step()
        labels = [item.label for item in controller._slash_overlay.items]  # noqa: SLF001
        details = {item.label: item.detail for item in controller._slash_overlay.items}  # noqa: SLF001
    finally:
        runtime.shutdown()

    assert "/ask" in labels
    assert "/skill:reviewer" in labels
    assert details["/ask"] == "Ask a question. <topic>"
    assert details["/skill:reviewer"] == "Review files."

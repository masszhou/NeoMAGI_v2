from __future__ import annotations

import asyncio

from cli.extensions.ui import NoopExtensionUIContext


def test_noop_ui_context_has_deterministic_dialog_defaults() -> None:
    ui = NoopExtensionUIContext()

    assert asyncio.run(ui.select("title", [])) is None
    assert asyncio.run(ui.confirm("title", "message")) is False
    assert asyncio.run(ui.input("title")) is None
    assert asyncio.run(ui.editor("title", "prefill")) == "prefill"


def test_noop_ui_context_records_unimplemented_primitives() -> None:
    ui = NoopExtensionUIContext()

    ui.set_theme("dark")

    assert any("set_theme" in diagnostic.message for diagnostic in ui.diagnostics)

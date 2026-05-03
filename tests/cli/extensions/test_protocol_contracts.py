from __future__ import annotations

from cli.extensions import protocols
from cli.tools.definitions import BUILTIN_TOOL_NAMES, ToolName


def test_extension_protocol_documents_setup_entrypoint() -> None:
    assert "setup(api: ExtensionAPI)" in (protocols.__doc__ or "")
    assert "activate(api: ExtensionAPI)" not in (protocols.__doc__ or "")


def test_tool_name_allows_extension_names() -> None:
    name: ToolName = "custom-extension-tool"
    assert name == "custom-extension-tool"
    assert "read" in BUILTIN_TOOL_NAMES

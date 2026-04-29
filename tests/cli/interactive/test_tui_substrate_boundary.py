from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FORBIDDEN_PROTOCOL_MODULES = ("agent_core", "cli.core", "ai_provider")


def _walk_py(root: Path):
    for path in root.rglob("*.py"):
        yield path


def test_src_tui_does_not_import_agent_or_provider_protocol_modules() -> None:
    bad: list[tuple[Path, str]] = []
    for path in _walk_py(REPO / "src" / "tui"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                top = module.split(".")[0]
                second = ".".join(module.split(".")[:2])
                if top in FORBIDDEN_PROTOCOL_MODULES or second in FORBIDDEN_PROTOCOL_MODULES:
                    bad.append((path, module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    top = name.split(".")[0]
                    second = ".".join(name.split(".")[:2])
                    if top in FORBIDDEN_PROTOCOL_MODULES or second in FORBIDDEN_PROTOCOL_MODULES:
                        bad.append((path, name))
    assert not bad, f"src/tui imports protocol modules: {bad}"

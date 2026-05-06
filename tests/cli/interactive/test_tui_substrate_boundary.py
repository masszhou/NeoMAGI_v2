from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PACKAGE_SRC = REPO / "packages" / "neomagi_pi" / "src"
FORBIDDEN_PROTOCOL_MODULES = (
    "agent_core",
    "cli.core",
    "cli.tools",
    "policy",
    "ai_provider",
    "storage",
)


def _walk_py(root: Path):
    for path in root.rglob("*.py"):
        yield path


def _imported_modules(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "",)
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    return ()


def _is_forbidden_protocol_import(module: str) -> bool:
    top = module.split(".")[0]
    second = ".".join(module.split(".")[:2])
    return top in FORBIDDEN_PROTOCOL_MODULES or second in FORBIDDEN_PROTOCOL_MODULES


def test_src_tui_does_not_import_agent_or_provider_protocol_modules() -> None:
    bad: list[tuple[Path, str]] = []
    for path in _walk_py(PACKAGE_SRC / "tui"):
        tree = ast.parse(path.read_text())
        bad.extend(
            (path, module)
            for node in ast.walk(tree)
            for module in _imported_modules(node)
            if _is_forbidden_protocol_import(module)
        )
    assert not bad, f"packages/neomagi_pi/src/tui imports protocol modules: {bad}"

from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_IMPORT_ROOTS = {
    "anthropic",
    "cli",
    "openai",
    "policy",
    "storage",
}


def _import_roots(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".", 1)[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module.split(".", 1)[0]]
    return []


def test_agent_core_does_not_import_product_storage_policy_or_provider_sdks() -> None:
    source_root = Path(__file__).parents[2] / "src" / "agent_core"
    offenders: list[str] = []

    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            for root in _import_roots(node):
                if root in FORBIDDEN_IMPORT_ROOTS:
                    offenders.append(f"{path.relative_to(source_root)} imports {root}")

    assert offenders == []

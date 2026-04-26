from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
ALLOWED_SDK_IMPORTERS = {
    "src/ai_provider/providers/anthropic.py",
    "src/ai_provider/providers/openai_responses.py",
    "src/ai_provider/providers/openai_completions.py",
}


def _sdk_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in {"anthropic", "openai"}:
                    found.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".", 1)[0] in {"anthropic", "openai"}:
                found.add(node.module.split(".", 1)[0])
    return found


def test_sdk_imports_stay_inside_provider_adapters() -> None:
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        imports = _sdk_imports(path)
        if imports and relative not in ALLOWED_SDK_IMPORTERS:
            violations.append(f"{relative}: {sorted(imports)}")

    assert violations == []


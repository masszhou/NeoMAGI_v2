from __future__ import annotations

import asyncio
from pathlib import Path

from cli.extensions.loader import load_extensions
from cli.extensions.runner import ExtensionRunner
from cli.resources.loader import ResourceLoader
from cli.resources.prompt_templates import expand_prompt_template
from cli.resources.skills import expand_skill_command


REPO_ROOT = Path(__file__).resolve().parents[3]
SHOWCASE_WORKSPACE = REPO_ROOT / "showcase" / "qmd_autoresearch_mini" / "workspace"


def test_autoresearch_showcase_fixture_loads_extension_skill_and_prompt(tmp_path: Path) -> None:
    async def run() -> None:
        loader = ResourceLoader(cwd=SHOWCASE_WORKSPACE, agent_dir=tmp_path / ".magipi" / "agent")
        await loader.reload()
        first_snapshot = loader.snapshot
        first_extensions = await load_extensions([resource.path for resource in first_snapshot.extensions], cwd=SHOWCASE_WORKSPACE)
        first_runner = ExtensionRunner(first_extensions.runtime)

        await loader.reload()
        second_snapshot = loader.snapshot
        second_extensions = await load_extensions([resource.path for resource in second_snapshot.extensions], cwd=SHOWCASE_WORKSPACE)
        second_runner = ExtensionRunner(second_extensions.runtime)

        extension_names = [resource.name for resource in second_snapshot.extensions]
        skill_names = [skill.name for skill in second_snapshot.skills]
        prompt_names = [prompt.name for prompt in second_snapshot.prompts]
        tool_names = [tool.name for tool in second_runner.get_all_registered_tools()]
        expanded_skill = expand_skill_command("/skill:autoresearch-mini score", list(second_snapshot.skills))
        expanded_prompt = expand_prompt_template("/autoresearch-next score", list(second_snapshot.prompts))

        assert extension_names == ["autoresearch"]
        assert "autoresearch-mini" in skill_names
        assert "autoresearch-next" in prompt_names
        assert sorted(tool_names) == ["init_experiment", "log_experiment", "recover_experiment", "run_experiment"]
        assert expanded_skill is not None and "## Restart Note" in expanded_skill
        assert expanded_prompt is not None and "Optional focus: score" in expanded_prompt
        assert sorted(tool.name for tool in first_runner.get_all_registered_tools()) == sorted(tool_names)
        assert not any(diagnostic.severity == "error" for diagnostic in second_extensions.diagnostics)

    asyncio.run(run())

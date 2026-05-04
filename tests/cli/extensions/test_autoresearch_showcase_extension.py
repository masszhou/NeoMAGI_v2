from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cli.extensions.loader import load_extensions
from cli.extensions.runner import ExtensionRunner


REPO_ROOT = Path(__file__).resolve().parents[3]
SHOWCASE_WORKSPACE = REPO_ROOT / "showcase" / "qmd_autoresearch_mini" / "workspace"
EXTENSION_PATH = SHOWCASE_WORKSPACE / ".pi" / "extensions" / "autoresearch.py"


def test_mini_benchmark_outputs_stable_metric_lines() -> None:
    command = [sys.executable, "finetune/benchmark.py", "--config", "finetune/configs/baseline.json"]

    first = subprocess.run(command, cwd=SHOWCASE_WORKSPACE, text=True, capture_output=True, check=True)
    second = subprocess.run(command, cwd=SHOWCASE_WORKSPACE, text=True, capture_output=True, check=True)

    assert first.stdout == second.stdout
    assert "METRIC score=" in first.stdout
    assert "METRIC exact_match=" in first.stdout


def test_metric_parser_accepts_safe_numbers_and_rejects_bad_names() -> None:
    module = _extension_module()

    metrics = module._parse_metrics(
        "\n".join(
            [
                "METRIC score=1",
                "METRIC latency_ms=1.25e2",
                "METRIC score=2.5",
                "METRIC _hidden=9",
                "METRIC bad.name=8",
                "METRIC path/name=7",
            ]
        )
    )

    assert metrics == {"score": 2.5, "latency_ms": 125.0}


def test_init_creates_files_without_overwriting_existing_content(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)

    first = asyncio.run(_call_tool(workspace, "init_experiment", {"objective": "Improve score"}))
    (workspace / "autoresearch.md").write_text("custom\n", encoding="utf-8")
    second = asyncio.run(_call_tool(workspace, "init_experiment", {"objective": "Overwrite attempt"}))

    assert first["isError"] is False
    assert (workspace / "autoresearch.sh").stat().st_mode & 0o111
    assert (workspace / "autoresearch.md").read_text(encoding="utf-8") == "custom\n"
    assert "autoresearch.md" in second["details"]["preserved"]


def test_init_rejects_path_escape(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)

    result = asyncio.run(_call_tool(workspace, "init_experiment", {"working_dir": "../escape"}))

    assert result["isError"] is True
    assert "escapes extension cwd" in result["content"][0]["text"]


def test_run_experiment_parses_returned_metrics_through_exec(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    calls: list[tuple[str, list[str], dict[str, Any]]] = []

    async def fake_exec(command: str, args: list[str], options: dict[str, Any]) -> dict[str, Any]:
        calls.append((command, args, options))
        return {"output": "METRIC score=0.75\nMETRIC latency_ms=12\n", "exitCode": 0, "truncated": False}

    result = asyncio.run(
        _call_tool(
            workspace,
            "run_experiment",
            {"trial_id": "baseline", "command": "bash autoresearch.sh", "timeout_seconds": 5},
            exec_impl=fake_exec,
        )
    )

    assert result["details"]["status"] == "baseline"
    assert result["details"]["metrics"] == {"score": 0.75, "latency_ms": 12}
    assert result["details"]["metrics_source"] == "returned_output"
    assert calls[0][0] == "cd . && bash autoresearch.sh"
    assert calls[0][2]["timeout"] == 5.0


def test_run_experiment_reparses_metrics_from_truncated_artifact(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    artifact = tmp_path / "full.out"
    artifact.write_text("header\nMETRIC score=0.91\n", encoding="utf-8")

    async def fake_exec(_command: str, _args: list[str], _options: dict[str, Any]) -> dict[str, Any]:
        return {
            "output": "tail without metrics",
            "exitCode": 0,
            "truncated": True,
            "fullOutputPath": str(artifact),
            "details": {"truncation": {"truncated": True}, "fullOutputPath": str(artifact)},
        }

    result = asyncio.run(
        _call_tool(workspace, "run_experiment", {"trial_id": "trial-1", "command": "bash autoresearch.sh"}, exec_impl=fake_exec)
    )

    assert result["details"]["metrics"] == {"score": 0.91}
    assert result["details"]["metrics_source"] == "artifact"


def test_run_experiment_maps_check_failure(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    (workspace / "autoresearch.checks.sh").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    seen: list[str] = []

    async def fake_exec(command: str, _args: list[str], _options: dict[str, Any]) -> dict[str, Any]:
        seen.append(command)
        if "autoresearch.checks.sh" in command:
            return {"output": "check failed", "exitCode": 1, "truncated": False}
        return {"output": "METRIC score=0.75\n", "exitCode": 0, "truncated": False}

    result = asyncio.run(
        _call_tool(workspace, "run_experiment", {"trial_id": "trial-1", "command": "bash autoresearch.sh"}, exec_impl=fake_exec)
    )

    assert result["details"]["status"] == "checks_failed"
    assert seen == ["cd . && bash autoresearch.sh", "cd . && bash autoresearch.checks.sh"]


def test_log_appends_jsonl_and_redacts_secret_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    monkeypatch.setenv("HF_TOKEN", "hf_" + "A" * 32)

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "Token was not persisted.",
                "trial_id": "baseline",
                "hypothesis": "",
                "changes": "used hf_" + "A" * 32,
                "command": "echo $HF_TOKEN",
                "metrics": {"score": 0.5, "output_tokens": 1234, "tokens_per_sec": 42.5},
                "metrics_source": "returned_output",
                "exit_code": 0,
                "duration_ms": 10,
            },
        )
    )

    line = (workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert result["isError"] is False
    assert entry["status"] == "baseline"
    assert entry["metrics"]["output_tokens"] == 1234
    assert entry["metrics"]["tokens_per_sec"] == 42.5
    assert "hf_" + "A" * 32 not in line
    assert "<redacted:HF_TOKEN>" in line


def test_keep_creates_commit_on_scratch_branch(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    _replace_in_file(workspace / "finetune" / "configs" / "baseline.json", '"n_examples": 4', '"n_examples": 5')

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "keep",
                "restart_note": "Kept n_examples increase.",
                "trial_id": "trial-keep",
                "hypothesis": "More examples improves score.",
                "changes": "Increase n_examples.",
                "command": "bash autoresearch.sh",
                "metrics": {"score": 0.7},
                "metrics_source": "returned_output",
                "exit_code": 0,
                "duration_ms": 10,
            },
        )
    )

    assert result["isError"] is False
    assert len(result["details"]["entry"]["commit"]) == 40
    assert _git(workspace, "log", "--oneline", "-1").stdout.strip().endswith("chore(autoresearch): keep experiment")


def test_keep_rejects_default_branch(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "main")
    _replace_in_file(workspace / "finetune" / "configs" / "baseline.json", '"n_examples": 4', '"n_examples": 5')

    result = asyncio.run(_call_tool(workspace, "log_experiment", {"status": "keep", "restart_note": "Do not keep on main."}))

    assert result["isError"] is True
    assert "refuses default" in result["content"][0]["text"]


def test_discard_rejects_default_branch_without_removing_untracked_files(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "main")
    (workspace / "IMPORTANT_USER_NOTES.txt").write_text("keep me\n", encoding="utf-8")
    (workspace / "my_workdir").mkdir()
    (workspace / "my_workdir" / "wip.txt").write_text("keep me too\n", encoding="utf-8")

    result = asyncio.run(_call_tool(workspace, "log_experiment", {"status": "discard", "restart_note": "Do not discard on main."}))

    assert result["isError"] is True
    assert "refuses default" in result["content"][0]["text"]
    assert (workspace / "IMPORTANT_USER_NOTES.txt").is_file()
    assert (workspace / "my_workdir" / "wip.txt").is_file()


def test_discard_reverts_non_autoresearch_files_only(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    original_config = (workspace / "finetune" / "configs" / "baseline.json").read_text(encoding="utf-8")
    _replace_in_file(workspace / "finetune" / "configs" / "baseline.json", '"n_examples": 4', '"n_examples": 5')
    (workspace / "scratch.txt").write_text("remove me\n", encoding="utf-8")
    (workspace / "scratch_dir").mkdir()
    (workspace / "scratch_dir" / "scratch.txt").write_text("remove me too\n", encoding="utf-8")
    (workspace / "autoresearch.md").write_text("preserve me\n", encoding="utf-8")

    result = asyncio.run(_call_tool(workspace, "log_experiment", {"status": "discard", "restart_note": "Discarded noisy change."}))

    assert result["isError"] is False
    assert (workspace / "finetune" / "configs" / "baseline.json").read_text(encoding="utf-8") == original_config
    assert not (workspace / "scratch.txt").exists()
    assert not (workspace / "scratch_dir").exists()
    assert (workspace / "autoresearch.md").read_text(encoding="utf-8") == "preserve me\n"
    entry = json.loads((workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip())
    assert entry["revert"]["removed"] == ["scratch.txt", "scratch_dir/scratch.txt"]
    assert entry["revert"]["removed_dirs"] == ["scratch_dir"]


async def _call_tool(
    workspace: Path,
    name: str,
    args: dict[str, Any],
    exec_impl: Any | None = None,
) -> dict[str, Any]:
    loaded = await load_extensions([workspace / ".pi" / "extensions" / "autoresearch.py"], cwd=workspace)
    runner = ExtensionRunner(loaded.runtime)
    if exec_impl is not None:
        runner.bind_core(exec=exec_impl)
    tool = next(tool for tool in loaded.runtime.extensions[0].tools if tool.name == name)
    result = tool.execute(args, None, None, None)
    if inspect.isawaitable(result):
        result = await result
    return result


def _extension_module() -> Any:
    spec = importlib.util.spec_from_file_location("_autoresearch_showcase_test", EXTENSION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_workspace(tmp_path: Path) -> Path:
    target = tmp_path / "workspace"
    shutil.copytree(SHOWCASE_WORKSPACE, target)
    return target


def _git_init_with_commit(workspace: Path, branch: str) -> None:
    _git(workspace, "init")
    _git(workspace, "checkout", "-b", branch)
    _git(workspace, "add", "-A")
    _git(workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")


def _replace_in_file(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=workspace, text=True, capture_output=True, check=True)

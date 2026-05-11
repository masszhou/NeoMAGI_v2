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
EXTENSION_PATH = SHOWCASE_WORKSPACE / ".magipi" / "extensions" / "autoresearch.py"


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
                "METRIC api_key_latency=6",
                "METRIC secret_token_count=5",
                "METRIC overflow=1e10000",
            ]
        )
    )

    assert metrics == {"score": 2.5, "latency_ms": 125.0}


def test_run_experiment_schema_allows_real_qmd_training_timeout() -> None:
    loaded = asyncio.run(load_extensions([EXTENSION_PATH], cwd=SHOWCASE_WORKSPACE))
    tool = next(tool for tool in loaded.runtime.extensions[0].tools if tool.name == "run_experiment")

    properties = tool.parameters["properties"]

    assert properties["timeout_seconds"]["maximum"] == 1500
    assert properties["checks_timeout_seconds"]["maximum"] == 1500


def test_init_creates_files_without_overwriting_existing_content(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")

    first = asyncio.run(_call_tool(workspace, "init_experiment", {"objective": "Improve score"}))
    (workspace / "autoresearch.md").write_text("custom\n", encoding="utf-8")
    (workspace / "autoresearch.jsonl").write_text('{"trial_id":"baseline"}\n', encoding="utf-8")
    second = asyncio.run(_call_tool(workspace, "init_experiment", {"objective": "Overwrite attempt", "overwrite": True}))

    assert first["isError"] is False
    assert (workspace / "autoresearch.sh").stat().st_mode & 0o111
    assert (workspace / "autoresearch.md").read_text(encoding="utf-8").startswith("# Autoresearch Session")
    assert (workspace / "autoresearch.jsonl").read_text(encoding="utf-8") == '{"trial_id":"baseline"}\n'
    assert "autoresearch.jsonl" in second["details"]["preserved"]


def test_init_rejects_path_escape(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")

    result = asyncio.run(_call_tool(workspace, "init_experiment", {"working_dir": "../escape"}))

    assert result["isError"] is True
    assert "escapes extension cwd" in result["content"][0]["text"]


def test_init_rejects_subdirectory_and_non_scratch_branch(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "main")
    (workspace / "nested").mkdir()

    subdir_result = asyncio.run(_call_tool(workspace, "init_experiment", {"working_dir": "nested"}))
    branch_result = asyncio.run(_call_tool(workspace, "init_experiment", {}))

    assert subdir_result["isError"] is True
    assert "git top-level" in subdir_result["content"][0]["text"]
    assert branch_result["isError"] is True
    assert "non-scratch" in branch_result["content"][0]["text"]


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
    saved = json.loads((workspace / "autoresearch-artifacts" / "baseline" / "run_result.json").read_text(encoding="utf-8"))
    assert saved["metrics"] == {"score": 0.75, "latency_ms": 12}


def test_run_experiment_reparses_metrics_from_truncated_artifact(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    artifact = workspace / "autoresearch-artifacts" / "trial-1" / "full.out"
    artifact.parent.mkdir(parents=True)
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


def test_run_experiment_ignores_untrusted_full_output_path(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    artifact = tmp_path / "full.out"
    artifact.write_text("METRIC score=0.99\n", encoding="utf-8")

    async def fake_exec(_command: str, _args: list[str], _options: dict[str, Any]) -> dict[str, Any]:
        return {"output": "METRIC score=0.1\n", "exitCode": 0, "truncated": True, "fullOutputPath": str(artifact)}

    result = asyncio.run(
        _call_tool(workspace, "run_experiment", {"trial_id": "trial-1", "command": "bash autoresearch.sh"}, exec_impl=fake_exec)
    )

    assert result["details"]["metrics"] == {"score": 0.1}
    assert result["details"]["metrics_source"] == "returned_output"
    assert "fullOutputPath" not in result["details"]["artifact"]
    assert result["details"]["artifact"]["fullOutputPathRejected"] is True


def test_log_uses_saved_run_result_when_model_omits_run_result(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)

    async def fake_exec(_command: str, _args: list[str], _options: dict[str, Any]) -> dict[str, Any]:
        return {"output": "METRIC score=0.75\n", "exitCode": 0, "truncated": False}

    run = asyncio.run(
        _call_tool(workspace, "run_experiment", {"trial_id": "baseline", "command": "bash autoresearch.sh"}, exec_impl=fake_exec)
    )
    log = asyncio.run(_call_tool(workspace, "log_experiment", {"status": "baseline", "restart_note": "saved result"}))

    entry = json.loads((workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip())
    assert run["details"]["status"] == "baseline"
    assert log["isError"] is False
    assert entry["trial_id"] == "baseline"
    assert entry["metrics"] == {"score": 0.75}


def test_log_rejects_success_without_run_result_or_with_incompatible_result(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    empty = asyncio.run(_call_tool(workspace, "log_experiment", {"status": "baseline", "restart_note": "missing result"}))
    wrong_status = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "wrong status",
                "run_result": _run_result("trial-1", status="ready", metrics={"score": 0.2}),
            },
        )
    )
    module._write_run_result(workspace, _run_result("trial-not-baseline", status="ready", metrics={"score": 0.3}))
    saved_wrong_status = asyncio.run(_call_tool(workspace, "log_experiment", {"status": "baseline", "restart_note": "saved wrong"}))
    empty_metrics = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "keep",
                "restart_note": "empty metrics",
                "run_result": _run_result("trial-2", status="ready", metrics={}),
            },
        )
    )

    assert empty["isError"] is True
    assert "requires run_result" in empty["content"][0]["text"]
    assert wrong_status["isError"] is True
    assert "requires run_result.status=baseline" in wrong_status["content"][0]["text"]
    assert saved_wrong_status["isError"] is True
    assert "requires run_result.status=baseline" in saved_wrong_status["content"][0]["text"]
    assert empty_metrics["isError"] is True
    assert "requires non-empty run_result.metrics" in empty_metrics["content"][0]["text"]
    assert not (workspace / "autoresearch.jsonl").exists()


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


def test_run_experiment_exec_exception_returns_crash_details(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)

    async def fake_exec(_command: str, _args: list[str], _options: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError("timed out")

    result = asyncio.run(
        _call_tool(workspace, "run_experiment", {"trial_id": "trial-1", "command": "bash autoresearch.sh"}, exec_impl=fake_exec)
    )

    assert result["isError"] is True
    assert result["details"]["status"] == "crash"
    assert result["details"]["trial_id"] == "trial-1"
    assert result["details"]["metrics"] == {}
    assert result["details"]["exit_code"] is None


def test_run_and_log_reject_invalid_trial_id_paths(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)

    run = asyncio.run(_call_tool(workspace, "run_experiment", {"trial_id": "../escape"}))
    log = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {"status": "baseline", "restart_note": "bad id", "trial_id": ".", "metrics": {}, "metrics_source": "returned_output"},
        )
    )

    assert run["isError"] is True
    assert "invalid trial_id" in run["content"][0]["text"]
    assert log["isError"] is True
    assert "invalid trial_id" in log["content"][0]["text"]


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
                "run_result": _run_result(
                    "baseline",
                    status="baseline",
                    changes="used hf_" + "A" * 32,
                    command="echo $HF_TOKEN",
                    metrics={"score": 0.5, "output_tokens": 1234, "tokens_per_sec": 42.5},
                ),
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


def test_log_rejects_ready_status_and_nonfinite_metrics(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)

    ready = asyncio.run(_call_tool(workspace, "log_experiment", {"status": "ready", "restart_note": "not durable"}))
    nonfinite = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "bad metric",
                "run_result": _run_result("baseline", status="baseline", metrics={"score": float("inf")}),
            },
        )
    )

    assert ready["isError"] is True
    assert "invalid status" in ready["content"][0]["text"]
    assert nonfinite["isError"] is True
    assert "non-finite" in nonfinite["content"][0]["text"]
    assert not (workspace / "autoresearch.jsonl").exists()


def test_log_rejects_metrics_and_source_provenance_mismatch(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)

    metrics = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "mismatch",
                "metrics": {"score": 0.1},
                "run_result": _run_result("baseline", status="baseline", metrics={"score": 0.2}),
            },
        )
    )
    source = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "mismatch",
                "metrics_source": "artifact",
                "run_result": _run_result("baseline", status="baseline", metrics={"score": 0.2}),
            },
        )
    )

    assert metrics["isError"] is True
    assert "metrics conflicts" in metrics["content"][0]["text"]
    assert source["isError"] is True
    assert "metrics_source conflicts" in source["content"][0]["text"]


def test_log_rejects_duplicate_trial_id_and_bounds_command(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)

    first = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "first",
                "run_result": _run_result("baseline", status="baseline", command="x" * 5000, metrics={"score": 0.1}),
            },
        )
    )
    duplicate = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "duplicate",
                "run_result": _run_result("baseline", status="baseline", metrics={"score": 0.2}),
            },
        )
    )

    entry = json.loads((workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip())
    assert first["isError"] is False
    assert len(entry["command"].encode("utf-8")) <= 4096
    assert entry["command"].endswith("<truncated>")
    assert duplicate["isError"] is True
    assert "already exists" in duplicate["content"][0]["text"]


def test_log_secret_redaction_ignores_short_test_env_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    monkeypatch.setenv("HF_TOKEN", "test")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "test text should remain",
                "run_result": _run_result(
                    "baseline",
                    status="baseline",
                    changes="normal test text",
                    command="echo test",
                    metrics={"score": 0.5},
                ),
            },
        )
    )

    line = (workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip()
    assert result["isError"] is False
    assert "normal test text" in line
    assert "echo test" in line
    assert "<redacted:HF_TOKEN>" not in line


def test_log_sanitizes_untrusted_artifact_paths_and_full_output_fields(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    trusted = workspace / "autoresearch-artifacts" / "baseline" / "full.out"
    trusted.parent.mkdir(parents=True)
    trusted.write_text("METRIC score=0.5\n", encoding="utf-8")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "baseline",
                "restart_note": "artifact sanitized",
                "run_result": _run_result(
                    "baseline",
                    status="baseline",
                    metrics={"score": 0.5},
                    metrics_source="artifact",
                    artifact={
                        "fullOutputPath": str(trusted),
                        "stdout": "raw full output",
                        "stderr": "raw error output",
                        "truncation": {
                            "fullOutputPath": str(tmp_path / "outside.out"),
                            "stdout": "nested raw output",
                            "truncated": True,
                        },
                    },
                ),
            },
        )
    )

    entry = json.loads((workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip())
    assert result["isError"] is False
    assert entry["artifact"]["fullOutputPath"] == str(trusted.resolve())
    assert "stdout" not in entry["artifact"]
    assert "stderr" not in entry["artifact"]
    assert "fullOutputPath" not in entry["artifact"]["truncation"]
    assert entry["artifact"]["truncation"]["fullOutputPathRejected"] is True
    assert "nested raw output" not in json.dumps(entry)


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
                "run_result": _run_result(
                    "trial-keep",
                    status="ready",
                    hypothesis="More examples improves score.",
                    changes="Increase n_examples.",
                    metrics={"score": 0.7},
                ),
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

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "keep",
                "restart_note": "Do not keep on main.",
                "run_result": _run_result("trial-keep", status="ready"),
            },
        )
    )

    assert result["isError"] is True
    assert "refuses default" in result["content"][0]["text"]


def test_discard_rejects_default_branch_without_removing_untracked_files(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "main")
    (workspace / "IMPORTANT_USER_NOTES.txt").write_text("keep me\n", encoding="utf-8")
    (workspace / "my_workdir").mkdir()
    (workspace / "my_workdir" / "wip.txt").write_text("keep me too\n", encoding="utf-8")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "discard",
                "restart_note": "Do not discard on main.",
                "run_result": _run_result("trial-discard", status="ready"),
            },
        )
    )

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

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "discard",
                "restart_note": "Discarded noisy change.",
                "run_result": _run_result("trial-discard", status="ready", metrics={"score": 0.1}),
            },
        )
    )

    assert result["isError"] is False
    assert (workspace / "finetune" / "configs" / "baseline.json").read_text(encoding="utf-8") == original_config
    assert not (workspace / "scratch.txt").exists()
    assert not (workspace / "scratch_dir").exists()
    assert (workspace / "autoresearch.md").read_text(encoding="utf-8") == "preserve me\n"
    entry = json.loads((workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip())
    assert entry["revert"]["removed"] == ["scratch.txt", "scratch_dir/scratch.txt"]
    assert entry["revert"]["removed_dirs"] == ["scratch_dir"]


@pytest.mark.parametrize("status", ["crash", "checks_failed"])
def test_crash_and_checks_failed_revert_non_autoresearch_files(tmp_path: Path, status: str) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    original_config = (workspace / "finetune" / "configs" / "baseline.json").read_text(encoding="utf-8")
    _replace_in_file(workspace / "finetune" / "configs" / "baseline.json", '"n_examples": 4', '"n_examples": 5')
    (workspace / "scratch.txt").write_text("remove me\n", encoding="utf-8")
    (workspace / "autoresearch.tmp").write_text("remove me too\n", encoding="utf-8")
    (workspace / "autoresearch.checks.sh").write_text("preserve checks\n", encoding="utf-8")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": status,
                "restart_note": "reverted failed trial",
                "trial_id": f"trial-{status}",
                "metrics": {},
                "metrics_source": "returned_output",
                "exit_code": 1,
                "duration_ms": 10,
            },
        )
    )

    assert result["isError"] is False
    assert (workspace / "finetune" / "configs" / "baseline.json").read_text(encoding="utf-8") == original_config
    assert not (workspace / "scratch.txt").exists()
    assert not (workspace / "autoresearch.tmp").exists()
    assert (workspace / "autoresearch.checks.sh").read_text(encoding="utf-8") == "preserve checks\n"
    entry = json.loads((workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip())
    assert entry["status"] == status
    assert entry["revert"]["removed"] == ["autoresearch.tmp", "scratch.txt"]


@pytest.mark.parametrize("branch", ["main", "feature/demo", "scratchpad", "scratch"])
def test_mutating_statuses_reject_non_scratch_branches_without_deleting_files(tmp_path: Path, branch: str) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, branch)
    (workspace / "IMPORTANT_USER_NOTES.txt").write_text("keep me\n", encoding="utf-8")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "crash",
                "restart_note": "do not mutate",
                "trial_id": "trial-crash",
                "metrics": {},
                "metrics_source": "returned_output",
            },
        )
    )

    assert result["isError"] is True
    assert "non-scratch" in result["content"][0]["text"]
    assert (workspace / "IMPORTANT_USER_NOTES.txt").is_file()
    assert not (workspace / "autoresearch.jsonl").exists()


@pytest.mark.parametrize("branch", ["scratch/feat/login", "experiment/demo.1"])
def test_branch_allowlist_accepts_scratch_and_experiment_prefixes(tmp_path: Path, branch: str) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, branch)

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "crash",
                "restart_note": "empty revert",
                "trial_id": "trial-crash",
                "metrics": {},
                "metrics_source": "returned_output",
            },
        )
    )

    assert result["isError"] is False
    assert result["details"]["entry"]["revert"] == {"removed": [], "removed_dirs": [], "reverted": []}


def test_keep_refuses_preexisting_staged_non_preserved_change(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    _replace_in_file(workspace / "finetune" / "configs" / "baseline.json", '"n_examples": 4', '"n_examples": 5')
    _git(workspace, "add", "finetune/configs/baseline.json")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "keep",
                "restart_note": "reject staged",
                "run_result": _run_result("trial-keep", status="ready", metrics={"score": 0.7}),
            },
        )
    )

    assert result["isError"] is True
    assert "pre-existing staged" in result["content"][0]["text"]


def test_mutating_status_rejects_unborn_head(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git(workspace, "init")
    _git(workspace, "checkout", "-b", "scratch/autoresearch-test")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "crash",
                "restart_note": "unborn",
                "trial_id": "trial-crash",
                "metrics": {},
                "metrics_source": "returned_output",
            },
        )
    )

    assert result["isError"] is True
    assert "unborn HEAD" in result["content"][0]["text"]


def test_keep_excludes_preserved_checks_file_from_commit(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    (workspace / "autoresearch.checks.sh").write_text("preserve me\n", encoding="utf-8")
    _replace_in_file(workspace / "finetune" / "configs" / "baseline.json", '"n_examples": 4', '"n_examples": 5')

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "keep",
                "restart_note": "kept config only",
                "run_result": _run_result("trial-keep", status="ready", metrics={"score": 0.7}),
            },
        )
    )

    committed = _git(workspace, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert result["isError"] is False
    assert "finetune/configs/baseline.json" in committed
    assert "autoresearch.checks.sh" not in committed


def test_discard_unlinks_untracked_symlink_without_following_target(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    (workspace / "autoresearch.md").write_text("preserve target\n", encoding="utf-8")
    (workspace / "evil.txt").symlink_to("autoresearch.md")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "discard",
                "restart_note": "discard symlink",
                "run_result": _run_result("trial-discard", status="ready", metrics={"score": 0.1}),
            },
        )
    )

    assert result["isError"] is False
    assert not (workspace / "evil.txt").exists()
    assert (workspace / "autoresearch.md").read_text(encoding="utf-8") == "preserve target\n"


def test_discard_restores_preserved_staged_deletion(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    (workspace / "autoresearch.md").write_text("tracked session fact\n", encoding="utf-8")
    _git(workspace, "add", "autoresearch.md")
    _git(workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "track session fact")
    _git(workspace, "rm", "autoresearch.md")

    result = asyncio.run(
        _call_tool(
            workspace,
            "log_experiment",
            {
                "status": "discard",
                "restart_note": "restore fact",
                "run_result": _run_result("trial-discard", status="ready", metrics={"score": 0.1}),
            },
        )
    )

    assert result["isError"] is False
    assert (workspace / "autoresearch.md").read_text(encoding="utf-8") == "tracked session fact\n"
    assert "autoresearch.md" not in result["details"]["entry"]["revert"]["reverted"]


def test_stale_pending_blocks_normal_tools(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    pending = workspace / "autoresearch-artifacts" / "trial-1" / "pending.json"
    pending.parent.mkdir(parents=True)
    pending.write_text('{"trial_id":"trial-1"}', encoding="utf-8")

    init = asyncio.run(_call_tool(workspace, "init_experiment", {}))
    run = asyncio.run(_call_tool(workspace, "run_experiment", {"trial_id": "trial-2"}))
    log = asyncio.run(_call_tool(workspace, "log_experiment", {"status": "baseline", "restart_note": "blocked"}))

    assert init["isError"] is True
    assert run["isError"] is True
    assert log["isError"] is True
    assert "pending recovery journal" in init["content"][0]["text"]
    assert "pending recovery journal" in run["content"][0]["text"]
    assert "pending recovery journal" in log["content"][0]["text"]


def test_recover_auto_resolves_keep_after_commit_pending(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    _replace_in_file(workspace / "finetune" / "configs" / "baseline.json", '"n_examples": 4', '"n_examples": 5')
    entry = _planned_entry("trial-keep", "keep", {"score": 0.7})
    module._write_pending_journal(workspace, "trial-keep", module._pending_journal(entry, workspace))
    commit = module._keep_changes(workspace)

    result = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-keep", "action": "auto"}))

    persisted = json.loads((workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip())
    assert result["isError"] is False
    assert persisted["commit"] == commit
    assert not (workspace / "autoresearch-artifacts" / "trial-keep" / "pending.json").exists()


def test_recover_auto_keep_pending_before_commit_clears_without_append(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    entry = _planned_entry("trial-keep", "keep", {"score": 0.7})
    module._write_pending_journal(workspace, "trial-keep", module._pending_journal(entry, workspace))

    result = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-keep", "action": "auto"}))

    assert result["isError"] is False
    assert not (workspace / "autoresearch.jsonl").exists()
    assert not (workspace / "autoresearch-artifacts" / "trial-keep" / "pending.json").exists()


def test_recover_auto_rejects_wrong_branch_and_corrupt_journal(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    entry = _planned_entry("trial-discard", "discard", {"score": 0.1})
    module._write_pending_journal(workspace, "trial-discard", module._pending_journal(entry, workspace))
    _git(workspace, "checkout", "-b", "scratch/other")

    wrong_branch = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-discard", "action": "auto"}))
    _git(workspace, "checkout", "scratch/autoresearch-test")
    (workspace / "autoresearch-artifacts" / "trial-discard" / "pending.json").write_text("{bad", encoding="utf-8")
    corrupt = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-discard", "action": "auto"}))

    assert wrong_branch["isError"] is True
    assert "branch mismatch" in wrong_branch["content"][0]["text"]
    assert corrupt["isError"] is True
    assert "corrupt pending journal" in corrupt["content"][0]["text"]


def test_recover_rejects_subdirectory_workdir_without_reverting_preserved_files(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    (workspace / "autoresearch.md").write_text("tracked session fact\n", encoding="utf-8")
    _git(workspace, "add", "autoresearch.md")
    _git(workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "track session fact")
    (workspace / "autoresearch.md").write_text("modified session fact\n", encoding="utf-8")
    (workspace / "nested").mkdir()
    entry = _planned_entry("trial-discard", "discard", {"score": 0.1})
    module._write_pending_journal(workspace / "nested", "trial-discard", module._pending_journal(entry, workspace))

    result = asyncio.run(
        _call_tool(workspace, "recover_experiment", {"trial_id": "trial-discard", "action": "auto", "working_dir": "nested"})
    )

    assert result["isError"] is True
    assert "git top-level" in result["content"][0]["text"]
    assert (workspace / "autoresearch.md").read_text(encoding="utf-8") == "modified session fact\n"


def test_recover_refuses_preexisting_staged_non_preserved_change(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    entry = _planned_entry("trial-discard", "discard", {"score": 0.1})
    module._write_pending_journal(workspace, "trial-discard", module._pending_journal(entry, workspace))
    (workspace / "stray.txt").write_text("user staged work\n", encoding="utf-8")
    _git(workspace, "add", "stray.txt")

    result = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-discard", "action": "auto"}))

    assert result["isError"] is True
    assert "pre-existing staged" in result["content"][0]["text"]
    assert "stray.txt" in _git(workspace, "diff", "--cached", "--name-only").stdout
    assert not (workspace / "autoresearch.jsonl").exists()


def test_recover_auto_rejects_keep_attribution_mismatch_without_append(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    _replace_in_file(workspace / "finetune" / "configs" / "baseline.json", '"n_examples": 4', '"n_examples": 5')
    entry = _planned_entry("trial-keep", "keep", {"score": 0.7})
    module._write_pending_journal(workspace, "trial-keep", module._pending_journal(entry, workspace))
    module._keep_changes(workspace)
    (workspace / "stray.txt").write_text("manual work\n", encoding="utf-8")
    _git(workspace, "add", "stray.txt")
    _git(workspace, "-c", "user.name=Other", "-c", "user.email=other@example.invalid", "commit", "-m", "user manual commit")

    result = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-keep", "action": "auto"}))

    assert result["isError"] is True
    assert "parent mismatch" in result["content"][0]["text"]
    assert not (workspace / "autoresearch.jsonl").exists()


def test_recover_auto_rejects_same_branch_revert_head_drift(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    entry = _planned_entry("trial-discard", "discard", {"score": 0.1})
    module._write_pending_journal(workspace, "trial-discard", module._pending_journal(entry, workspace))
    (workspace / "stray.txt").write_text("manual work\n", encoding="utf-8")
    _git(workspace, "add", "stray.txt")
    _git(workspace, "-c", "user.name=Other", "-c", "user.email=other@example.invalid", "commit", "-m", "user manual commit")

    result = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-discard", "action": "auto"}))

    assert result["isError"] is True
    assert "requires HEAD to match pending pre_head" in result["content"][0]["text"]
    assert not (workspace / "autoresearch.jsonl").exists()
    assert (workspace / "autoresearch-artifacts" / "trial-discard" / "pending.json").is_file()


def test_recover_abort_requires_pre_head_and_appends_crash(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    entry = _planned_entry("trial-discard", "discard", {"score": 0.1})
    module._write_pending_journal(workspace, "trial-discard", module._pending_journal(entry, workspace))

    result = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-discard", "action": "abort"}))

    persisted = json.loads((workspace / "autoresearch.jsonl").read_text(encoding="utf-8").strip())
    assert result["isError"] is False
    assert persisted["status"] == "crash"
    assert persisted["metrics"] == {}
    assert "recovery aborted" in persisted["restart_note"]
    assert "revert" not in persisted


def test_recover_abort_rejects_same_branch_head_drift_without_clearing_pending(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    module = _extension_module()
    _git_init_with_commit(workspace, "scratch/autoresearch-test")
    entry = _planned_entry("trial-discard", "discard", {"score": 0.1})
    module._write_pending_journal(workspace, "trial-discard", module._pending_journal(entry, workspace))
    (workspace / "stray.txt").write_text("manual work\n", encoding="utf-8")
    _git(workspace, "add", "stray.txt")
    _git(workspace, "-c", "user.name=Other", "-c", "user.email=other@example.invalid", "commit", "-m", "user manual commit")

    result = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-discard", "action": "abort"}))

    assert result["isError"] is True
    assert "abort recovery requires HEAD" in result["content"][0]["text"]
    assert not (workspace / "autoresearch.jsonl").exists()
    assert (workspace / "autoresearch-artifacts" / "trial-discard" / "pending.json").is_file()


def test_recover_schema_rejects_invalid_trial_action_and_missing_pending(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace, "scratch/autoresearch-test")

    invalid_trial = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "../x", "action": "auto"}))
    invalid_action = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-1", "action": "delete"}))
    missing = asyncio.run(_call_tool(workspace, "recover_experiment", {"trial_id": "trial-1", "action": "auto"}))

    assert invalid_trial["isError"] is True
    assert "invalid trial_id" in invalid_trial["content"][0]["text"]
    assert invalid_action["isError"] is True
    assert "invalid recovery action" in invalid_action["content"][0]["text"]
    assert missing["isError"] is True
    assert "no pending found" in missing["content"][0]["text"]


async def _call_tool(
    workspace: Path,
    name: str,
    args: dict[str, Any],
    exec_impl: Any | None = None,
) -> dict[str, Any]:
    loaded = await load_extensions([workspace / ".magipi" / "extensions" / "autoresearch.py"], cwd=workspace)
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


def _planned_entry(trial_id: str, status: str, metrics: dict[str, float]) -> dict[str, Any]:
    return {
        "ts": "2026-05-10T00:00:00+00:00",
        "trial_id": trial_id,
        "hypothesis": "test hypothesis",
        "changes": "test changes",
        "command": "bash autoresearch.sh",
        "status": status,
        "metrics": metrics,
        "metrics_source": "returned_output",
        "exit_code": 0,
        "duration_ms": 10,
        "restart_note": "continue",
    }


def _run_result(
    trial_id: str,
    *,
    status: str,
    metrics: dict[str, float | int] | None = None,
    metrics_source: str = "returned_output",
    hypothesis: str = "",
    changes: str = "",
    command: str = "bash autoresearch.sh",
    exit_code: int | None = 0,
    duration_ms: int = 10,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "trial_id": trial_id,
        "hypothesis": hypothesis,
        "changes": changes,
        "command": command,
        "status": status,
        "metrics": {"score": 0.7} if metrics is None else metrics,
        "metrics_source": metrics_source,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "artifact": {} if artifact is None else artifact,
    }


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=workspace, text=True, capture_output=True, check=True)

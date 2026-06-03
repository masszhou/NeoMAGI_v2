from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import cli.core.taskrun_parameter_golf_attempt as pga
from cli.core.taskrun_experiments import HostCommandResult
from cli.core.taskrun_parameter_golf_attempt import (
    ANCHOR_NAME,
    ParameterGolfAttemptOptions,
    ParameterGolfHarnessError,
    ensure_runtime_records_gitignore,
    parse_final_exact_val_bpb,
    run_parameter_golf_harness,
    run_single_parameter_golf_attempt,
    closeout_runtime_git_commit,
    submission_artifact_size,
    write_attempt_bundle,
)
from policy.permission_profiles import build_permission_profile_snapshot
from test_taskrun_service import _FakeTaskRunRepository, _seed_record, _service

BUDGET_COMMAND = (
    "DATA_PATH=./data/datasets/fineweb10B_sp1024/ "
    "TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model "
    "VOCAB_SIZE=1024 MAX_WALLCLOCK_SECONDS=480 python train_gpt.py"
)
BUDGET_LOG_PREFIX = (
    "val_bpb:enabled tokenizer_kind=sentencepiece "
    "tokenizer_path=./data/tokenizers/fineweb_1024_bpe.model\n"
    "train_loader:dataset:fineweb10B_sp1024 train_shards:1\n"
    "val_loader:shards "
    "pattern=./data/datasets/fineweb10B_sp1024/fineweb_val_*.bin tokens:62021632\n"
)


def test_parse_final_exact_val_bpb_uses_last_exact_line() -> None:
    value, line = parse_final_exact_val_bpb(
        "final_int8_zlib_roundtrip_exact val_loss:2.9 val_bpb:1.7\n"
        "noise val_bpb:0\n"
        "final_int8_zlib_roundtrip_exact val_loss:2.8 val_bpb:1.59\n"
    )

    assert value == 1.59
    assert "val_bpb:1.59" in line


@pytest.mark.parametrize(
    ("log", "code"),
    [
        ("final_int8_zlib_roundtrip val_bpb:1.5\n", "missing_final_exact_val_bpb"),
        (
            "final_int8_zlib_roundtrip_exact val_bpb:nan\n",
            "non_finite_final_exact_val_bpb",
        ),
        (
            "final_int8_zlib_roundtrip_exact val_bpb:not-a-number\n",
            "invalid_final_exact_val_bpb",
        ),
    ],
)
def test_parse_final_exact_val_bpb_fails_closed(log: str, code: str) -> None:
    with pytest.raises(ParameterGolfHarnessError) as exc:
        parse_final_exact_val_bpb(log)

    assert exc.value.code == code


def test_submission_artifact_size_only_counts_submission_dir(tmp_path: Path) -> None:
    records = tmp_path / "records" / "attempt"
    submission = records / "submission"
    submission.mkdir(parents=True)
    (records / "train_log.txt").write_text("x" * 100, encoding="utf-8")
    (records / "manifest.json").write_text("{}", encoding="utf-8")
    (submission / "train_gpt.py").write_text("print(1)\n", encoding="utf-8")
    (submission / "model.bin.zlib").write_bytes(b"abc")

    size, files = submission_artifact_size(submission)

    assert size == len("print(1)\n") + 3
    assert {file["path"] for file in files} == {
        "submission/train_gpt.py",
        "submission/model.bin.zlib",
    }


def test_bundle_writer_and_harness_accept_valid_improved_attempt(
    tmp_path: Path,
) -> None:
    train_gpt = tmp_path / "train_gpt.py"
    train_gpt.write_text("print('train')\n", encoding="utf-8")
    model = tmp_path / "model.bin.zlib"
    model.write_bytes(b"model")
    command_result = _host_result(
        "final_int8_zlib_roundtrip_exact val_loss:2.6 val_bpb:1.55\n"
    )

    records_dir = write_attempt_bundle(
        records_root=tmp_path / "records",
        attempt_id="019e2200-0000-7000-8000-000000000999",
        task_run_id="019e2200-0000-7000-8000-000000000111",
        parent_experiment_id=None,
        hypothesis="lower bpb",
        command=BUDGET_COMMAND,
        seed=42,
        timeout_seconds=600,
        train_log=command_result.output,
        submission_files=(train_gpt, model),
        command_result=command_result,
    )

    harness = run_parameter_golf_harness(records_dir)
    pga.finalize_attempt_bundle(records_dir, harness)
    manifest = json.loads((records_dir / "manifest.json").read_text(encoding="utf-8"))
    eval_result = json.loads(
        (records_dir / "eval_result.json").read_text(encoding="utf-8")
    )

    assert harness.status == "valid"
    assert harness.verdict["status"] == "accepted"
    assert "not_final_significance_verdict" in harness.verdict["reasons"]
    assert manifest["attempt_id"] == "019e2200-0000-7000-8000-000000000999"
    assert manifest["metrics"]["val_bpb"] == 1.55
    assert eval_result["metrics"]["artifact_size_bytes"] > 0


def test_harness_rejects_budget_mismatch(tmp_path: Path) -> None:
    records_dir = _valid_records_dir(tmp_path, val_bpb="1.55")
    manifest_path = records_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["budget"]["tier"] = "debug"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    harness = run_parameter_golf_harness(records_dir)

    assert harness.status == "invalid"
    assert harness.verdict["status"] == "rejected"
    assert "budget_mismatch:tier" in harness.reasons


def test_harness_rejects_actual_budget_mismatch_from_command(tmp_path: Path) -> None:
    records_dir = _valid_records_dir(
        tmp_path,
        val_bpb="1.55",
        command=BUDGET_COMMAND.replace("VOCAB_SIZE=1024", "VOCAB_SIZE=2048"),
    )

    harness = run_parameter_golf_harness(records_dir)

    assert harness.status == "invalid"
    assert harness.verdict["status"] == "rejected"
    assert "budget_mismatch:vocab_size" in harness.reasons
    assert harness.details["actual_budget"]["vocab_size"] == 2048


def test_harness_rejects_missing_actual_budget_sources(tmp_path: Path) -> None:
    records_dir = _valid_records_dir(
        tmp_path,
        val_bpb="1.55",
        command="python train_gpt.py",
        log_prefix="",
    )

    harness = run_parameter_golf_harness(records_dir)

    assert harness.status == "invalid"
    assert "budget_actual_missing:max_wallclock_seconds" in harness.reasons
    assert "budget_log_missing:tokenizer_path" in harness.reasons


def test_validate_attempt_options_uses_shell_timeout_hard_cap(tmp_path: Path) -> None:
    train_gpt = tmp_path / "train_gpt.py"
    train_gpt.write_text("print('train')\n", encoding="utf-8")
    hypothesis = tmp_path / "hypothesis.md"
    hypothesis.write_text("h", encoding="utf-8")

    with pytest.raises(ValueError, match="--timeout-seconds must be <= 1500"):
        pga.validate_attempt_options(
            ParameterGolfAttemptOptions(
                anchor=ANCHOR_NAME,
                workspace=tmp_path,
                hypothesis_file=hypothesis,
                command=BUDGET_COMMAND,
                seed=42,
                timeout_seconds=1501,
                submission_files=(Path("train_gpt.py"),),
            )
        )


def test_missing_post_run_submission_artifact_is_invalid(tmp_path: Path) -> None:
    train_gpt = tmp_path / "train_gpt.py"
    train_gpt.write_text("print('train')\n", encoding="utf-8")
    missing_model = tmp_path / "model.bin.zlib"
    command_result = _host_result(
        "final_int8_zlib_roundtrip_exact val_loss:2.6 val_bpb:1.55\n"
    )
    pga.validate_attempt_options(
        ParameterGolfAttemptOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            hypothesis_file=_hypothesis(tmp_path),
            command=BUDGET_COMMAND,
            seed=42,
            timeout_seconds=600,
            submission_files=(Path("train_gpt.py"), Path("model.bin.zlib")),
        )
    )

    records_dir = write_attempt_bundle(
        records_root=tmp_path / "records",
        attempt_id="019e2200-0000-7000-8000-000000000999",
        task_run_id="019e2200-0000-7000-8000-000000000111",
        parent_experiment_id=None,
        hypothesis="lower bpb",
        command=BUDGET_COMMAND,
        seed=42,
        timeout_seconds=600,
        train_log=command_result.output,
        submission_files=(train_gpt, missing_model),
        command_result=command_result,
    )

    harness = run_parameter_golf_harness(records_dir)

    assert harness.status == "invalid"
    assert "missing_submission_file:submission/model.bin.zlib" in harness.reasons


def test_harness_rejects_valid_but_worse_metric(tmp_path: Path) -> None:
    records_dir = _valid_records_dir(tmp_path, val_bpb="1.70")

    harness = run_parameter_golf_harness(records_dir)

    assert harness.status == "valid"
    assert harness.verdict["status"] == "rejected"
    assert "not_better_than_baseline_mean" in harness.verdict["reasons"]


def test_single_attempt_executor_writes_ledger_and_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    profile = build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["python", "git"]},
        },
    )
    record = _seed_record(repo, tmp_path, permission_profile=profile)
    service = _service(repo)
    hypothesis = tmp_path / "hypothesis.md"
    hypothesis.write_text("try a smaller model", encoding="utf-8")
    train_gpt = tmp_path / "train_gpt.py"
    train_gpt.write_text("print('ok')\n", encoding="utf-8")
    model = tmp_path / "model.bin.zlib"
    model.write_bytes(b"model")
    monkeypatch.setattr(
        pga,
        "capture_workspace_snapshot",
        lambda *args, **kwargs: {"git_head": "abc", "status": []},
    )
    monkeypatch.setattr(
        pga,
        "capture_diff_ref",
        lambda *args, **kwargs: {
            "git_head": "abc",
            "status_before": [],
            "status_after": [],
            "changed_paths": [],
        },
    )
    monkeypatch.setattr(
        pga,
        "run_host_command",
        lambda *args, **kwargs: _host_result(
            "final_int8_zlib_roundtrip_exact val_loss:2.6 val_bpb:1.55\n"
        ),
    )

    result = run_single_parameter_golf_attempt(
        service,
        record.id,
        tmp_path,
        ParameterGolfAttemptOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            hypothesis_file=hypothesis,
            command=BUDGET_COMMAND,
            seed=42,
            timeout_seconds=600,
            submission_files=(train_gpt, model),
        ),
    )

    assert (
        result.experiment.id == result.experiment.diff_ref["records_ref"].split("/")[-1]
    )
    assert result.experiment.step_id == repo.steps[0].id
    assert result.experiment.decision == "keep"
    assert result.experiment.result["verdict"]["status"] == "accepted"
    assert result.experiment.result["significance"] == {
        "final": False,
        "reason": "single_run_only",
    }
    assert result.experiment.diff_ref["parent_experiment_id"] is None
    manifest = json.loads(
        (tmp_path / result.records_ref / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["parent_experiment_id"] is None
    assert (tmp_path / result.records_ref / "manifest.json").is_file()
    assert repo.steps[0].status == "done"


def test_single_attempt_accepts_same_taskrun_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    profile = build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["python", "git"]},
        },
    )
    record = _seed_record(repo, tmp_path, permission_profile=profile)
    parent = repo.append_experiment(
        task_run_id=record.id,
        step_id="019e2200-0000-7000-8000-000000000111",
        experiment_id="019e2200-0000-7000-8000-000000000112",
        hypothesis="parent",
        change={"anchor": ANCHOR_NAME},
        command={},
        metrics={"val_bpb": 1.56, "artifact_size_bytes": 10},
        result={
            "verdict": {"status": "accepted", "reasons": []},
            "harness": {
                "status": "valid",
                "budget_comparable": True,
                "required_files_ok": True,
            },
            "artifact": {"content_ref": "records/parent"},
            "significance": {"final": False, "reason": "single_run_only"},
        },
        decision="keep",
        diff_ref={"records_ref": "records/parent"},
    )
    service = _service(repo)
    hypothesis = _hypothesis(tmp_path)
    train_gpt = tmp_path / "train_gpt.py"
    train_gpt.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(
        pga,
        "capture_workspace_snapshot",
        lambda *args, **kwargs: {"git_head": "abc", "status": []},
    )
    monkeypatch.setattr(
        pga,
        "capture_diff_ref",
        lambda *args, **kwargs: {"git_head": "abc", "status_after": []},
    )
    monkeypatch.setattr(
        pga,
        "run_host_command",
        lambda *args, **kwargs: _host_result(
            "final_int8_zlib_roundtrip_exact val_loss:2.6 val_bpb:1.55\n"
        ),
    )

    result = run_single_parameter_golf_attempt(
        service,
        record.id,
        tmp_path,
        ParameterGolfAttemptOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            hypothesis_file=hypothesis,
            command=BUDGET_COMMAND,
            seed=43,
            timeout_seconds=600,
            submission_files=(train_gpt,),
            parent_experiment_id=parent.id,
        ),
    )

    manifest = json.loads(
        (tmp_path / result.records_ref / "manifest.json").read_text(encoding="utf-8")
    )
    assert result.experiment.diff_ref["parent_experiment_id"] == parent.id
    assert manifest["parent_experiment_id"] == parent.id


def test_single_attempt_diff_ref_omits_runtime_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    repo = _FakeTaskRunRepository()
    profile = build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["python", "git"]},
        },
    )
    record = _seed_record(repo, tmp_path, permission_profile=profile)
    service = _service(repo)
    hypothesis = _hypothesis(tmp_path)
    train_gpt = tmp_path / "train_gpt.py"
    train_gpt.write_text("print('ok')\n", encoding="utf-8")
    _git(tmp_path, "add", "hypothesis.md", "train_gpt.py")
    _git(tmp_path, "commit", "-m", "base")
    monkeypatch.setattr(
        pga,
        "capture_workspace_snapshot",
        lambda *args, **kwargs: {"git_head": "abc", "status": []},
    )

    def fake_capture_diff_ref(*args, **kwargs):
        status = _git_output(
            tmp_path, "status", "--porcelain=v1", "--untracked-files=all"
        )
        assert "records/" not in status
        return {
            "git_head": "abc",
            "status_before": [],
            "status_after": status.splitlines(),
            "changed_paths": [],
        }

    monkeypatch.setattr(pga, "capture_diff_ref", fake_capture_diff_ref)
    monkeypatch.setattr(
        pga,
        "run_host_command",
        lambda *args, **kwargs: _host_result(
            "final_int8_zlib_roundtrip_exact val_loss:2.6 val_bpb:1.55\n"
        ),
    )

    result = run_single_parameter_golf_attempt(
        service,
        record.id,
        tmp_path,
        ParameterGolfAttemptOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            hypothesis_file=hypothesis,
            command=BUDGET_COMMAND,
            seed=42,
            timeout_seconds=600,
            submission_files=(train_gpt,),
        ),
    )

    assert result.experiment.diff_ref["status_after"] == []
    assert result.experiment.diff_ref["changed_paths"] == []
    assert (tmp_path / result.records_ref / "manifest.json").is_file()


def test_single_attempt_rejects_missing_parent_before_host_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    profile = build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["python", "git"]},
        },
    )
    record = _seed_record(repo, tmp_path, permission_profile=profile)
    service = _service(repo)
    train_gpt = tmp_path / "train_gpt.py"
    train_gpt.write_text("print('ok')\n", encoding="utf-8")
    called = False

    def fake_run_host_command(*args, **kwargs):
        nonlocal called
        called = True
        return _host_result("")

    monkeypatch.setattr(pga, "run_host_command", fake_run_host_command)

    with pytest.raises(ValueError, match="same TaskRun"):
        run_single_parameter_golf_attempt(
            service,
            record.id,
            tmp_path,
            ParameterGolfAttemptOptions(
                anchor=ANCHOR_NAME,
                workspace=tmp_path,
                hypothesis_file=_hypothesis(tmp_path),
                command=BUDGET_COMMAND,
                seed=42,
                timeout_seconds=600,
                submission_files=(train_gpt,),
                parent_experiment_id="019e2200-0000-7000-8000-000000009999",
            ),
        )

    assert called is False


def test_runtime_git_closeout_commits_and_restores_original_branch(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "train_gpt.py").write_text("print('base')\n", encoding="utf-8")
    _git(tmp_path, "add", "train_gpt.py")
    _git(tmp_path, "commit", "-m", "base")
    parent = _git_output(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "train_gpt.py").write_text("print('changed')\n", encoding="utf-8")

    result = closeout_runtime_git_commit(
        tmp_path,
        attempt_id="019e2200-0000-7000-8000-000000000999",
    )

    assert result["git_closeout"]["status"] == "committed"
    assert result["parent_commit"] == parent
    assert result["branch"] == "p3/parameter-golf/019e2200"
    assert result["commit_sha"] != parent
    assert result["git_closeout"]["restored_ref"] == "main"
    assert _git_output(tmp_path, "branch", "--show-current") == "main"
    assert (
        _git_output(tmp_path, "rev-parse", "p3/parameter-golf/019e2200")
        == result["commit_sha"]
    )


def test_runtime_records_gitignore_hides_runtime_records_from_status(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "train_gpt.py").write_text("print('base')\n", encoding="utf-8")
    _git(tmp_path, "add", "train_gpt.py")
    _git(tmp_path, "commit", "-m", "base")

    ignore_path = ensure_runtime_records_gitignore(tmp_path)
    (tmp_path / "records" / "attempt-1").mkdir()
    (tmp_path / "records" / "attempt-1" / "manifest.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (tmp_path / "records" / "_loop_inputs").mkdir()
    (tmp_path / "records" / "_loop_inputs" / "attempt_001_hypothesis.md").write_text(
        "h\n", encoding="utf-8"
    )

    status = _git_output(tmp_path, "status", "--porcelain=v1", "--untracked-files=all")
    assert status == ""
    assert ignore_path.read_text(encoding="utf-8") == "*\n"


def test_runtime_git_closeout_preserves_records_across_attempts(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "train_gpt.py").write_text("print('base')\n", encoding="utf-8")
    _git(tmp_path, "add", "train_gpt.py")
    _git(tmp_path, "commit", "-m", "base")

    parent = _git_output(tmp_path, "rev-parse", "HEAD")
    records_1 = tmp_path / "records" / "019e2200-0000-7000-8000-000000000991"
    records_1.mkdir(parents=True)
    (records_1 / "manifest.json").write_text("{}\n", encoding="utf-8")
    (records_1 / "eval_result.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "train_gpt.py").write_text("print('attempt 1')\n", encoding="utf-8")
    result_1 = closeout_runtime_git_commit(
        tmp_path,
        attempt_id="019e2200-0000-7000-8000-000000000991",
    )

    records_2 = tmp_path / "records" / "019e2200-0000-7000-8000-000000000992"
    records_2.mkdir(parents=True)
    (records_2 / "manifest.json").write_text("{}\n", encoding="utf-8")
    (records_2 / "eval_result.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "train_gpt.py").write_text("print('attempt 2')\n", encoding="utf-8")
    result_2 = closeout_runtime_git_commit(
        tmp_path,
        attempt_id="019e2201-0000-7000-8000-000000000992",
    )

    assert result_1["git_closeout"]["status"] == "committed"
    assert result_2["git_closeout"]["status"] == "committed"
    assert (records_1 / "manifest.json").is_file()
    assert (records_1 / "eval_result.json").is_file()
    assert (records_2 / "manifest.json").is_file()
    assert (records_2 / "eval_result.json").is_file()
    assert _git_output(tmp_path, "rev-parse", "HEAD") == parent
    branch_1_files = _git_output(
        tmp_path, "ls-tree", "-r", "--name-only", "p3/parameter-golf/019e2200"
    )
    branch_2_files = _git_output(
        tmp_path, "ls-tree", "-r", "--name-only", "p3/parameter-golf/019e2201"
    )
    assert "train_gpt.py" in branch_1_files
    assert "train_gpt.py" in branch_2_files
    assert "records/" not in branch_1_files
    assert "records/" not in branch_2_files


def test_runtime_git_closeout_is_clean_when_only_records_changed(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "train_gpt.py").write_text("print('base')\n", encoding="utf-8")
    _git(tmp_path, "add", "train_gpt.py")
    _git(tmp_path, "commit", "-m", "base")
    parent = _git_output(tmp_path, "rev-parse", "HEAD")
    records = tmp_path / "records" / "019e2200-0000-7000-8000-000000000997"
    records.mkdir(parents=True)
    (records / "manifest.json").write_text("{}\n", encoding="utf-8")

    result = closeout_runtime_git_commit(
        tmp_path,
        attempt_id="019e2200-0000-7000-8000-000000000997",
    )

    assert result["git_closeout"]["status"] == "clean"
    assert result["commit_sha"] == parent
    assert result["git_closeout"]["restored_ref"] == "main"
    assert (records / "manifest.json").is_file()


def test_runtime_git_closeout_restores_original_branch_on_commit_failure(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "commit.gpgsign", "true")
    (tmp_path / "train_gpt.py").write_text("print('base')\n", encoding="utf-8")
    _git(tmp_path, "add", "train_gpt.py")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-m", "base")
    (tmp_path / "train_gpt.py").write_text("print('changed')\n", encoding="utf-8")

    result = closeout_runtime_git_commit(
        tmp_path,
        attempt_id="019e2200-0000-7000-8000-000000000998",
    )

    assert result["git_closeout"]["status"] == "error"
    assert result["git_closeout"]["reason"] == "git_commit_failed"
    assert result["git_closeout"]["restored_ref"] == "main"
    assert _git_output(tmp_path, "branch", "--show-current") == "main"


def _valid_records_dir(
    tmp_path: Path,
    *,
    val_bpb: str,
    command: str = BUDGET_COMMAND,
    log_prefix: str = BUDGET_LOG_PREFIX,
) -> Path:
    train_gpt = tmp_path / "train_gpt.py"
    train_gpt.write_text("print('train')\n", encoding="utf-8")
    command_result = _host_result(
        f"final_int8_zlib_roundtrip_exact val_loss:2.6 val_bpb:{val_bpb}\n",
        log_prefix=log_prefix,
    )
    return write_attempt_bundle(
        records_root=tmp_path / "records",
        attempt_id="019e2200-0000-7000-8000-000000000999",
        task_run_id="019e2200-0000-7000-8000-000000000111",
        parent_experiment_id=None,
        hypothesis="h",
        command=command,
        seed=42,
        timeout_seconds=600,
        train_log=command_result.output,
        submission_files=(train_gpt,),
        command_result=command_result,
    )


def _hypothesis(tmp_path: Path) -> Path:
    hypothesis = tmp_path / "hypothesis.md"
    hypothesis.write_text("h", encoding="utf-8")
    return hypothesis


def _host_result(
    output: str, *, log_prefix: str = BUDGET_LOG_PREFIX
) -> HostCommandResult:
    return HostCommandResult(
        phase="trial",
        command=BUDGET_COMMAND,
        output=log_prefix + output,
        exit_code=0,
        cancelled=False,
        timed_out=False,
        policy_effect="allow",
        reason=None,
        permission_decision_id=None,
    )


def _git(path: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=path, check=True, text=True)


def _git_output(path: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()

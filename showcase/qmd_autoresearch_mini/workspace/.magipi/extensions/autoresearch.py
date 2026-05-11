"""QMD autoresearch mini showcase extension.

The extension is intentionally self-contained so the real QMD runbook can copy
this file into a scratch checkout without expanding NeoMAGI core.
"""

from __future__ import annotations

import inspect
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


METRIC_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
METRIC_RE = re.compile(r"^METRIC\s+([A-Za-z][A-Za-z0-9_]{0,63})=(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$")
TRIAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SCRATCH_BRANCH_RE = re.compile(r"^(scratch|experiment)/(?!.*\.\.)[A-Za-z0-9._-][A-Za-z0-9._/-]{0,127}$")
SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(?:token|secret|password|authorization|cookie)(?:$|[_-])|(?:^|[_-])api[_-]?key(?:$|[_-])",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|hf_[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|"
    r"github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b"
)
VALID_STATUSES = {"baseline", "keep", "discard", "crash", "checks_failed"}
SUCCESS_STATUSES = {"baseline", "keep", "discard"}
REVERT_STATUSES = {"discard", "crash", "checks_failed"}
PRESERVED_DIRS = {".magipi", "autoresearch-artifacts"}
PRESERVED_FILES = {"autoresearch.md", "autoresearch.sh", "autoresearch.jsonl", "autoresearch.checks.sh"}
MAX_COMMAND_BYTES = 4096


def setup(api: Any) -> None:
    api.register_tool(
        {
            "name": "init_experiment",
            "label": "init experiment",
            "description": "Create or validate autoresearch.md, autoresearch.sh, and autoresearch.jsonl.",
            "parameters": _object_schema(
                {
                    "objective": {"type": "string"},
                    "command": {"type": "string"},
                    "working_dir": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                    "create_checks": {"type": "boolean"},
                }
            ),
            "execute": lambda args, _context, _signal, _on_update: _init_experiment(api, args),
            "executionMode": "sequential",
        }
    )
    api.register_tool(
        {
            "name": "run_experiment",
            "label": "run experiment",
            "description": (
                "Run the benchmark via governed api.exec, parse METRIC lines, and run optional checks. "
                "A successful non-baseline run returns status=ready; the agent must then choose keep or discard."
            ),
            "parameters": _object_schema(
                {
                    "trial_id": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "changes": {"type": "string"},
                    "command": {"type": "string"},
                    "working_dir": {"type": "string"},
                    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 600},
                    "checks_timeout_seconds": {"type": "number", "minimum": 1, "maximum": 600},
                }
            ),
            "execute": lambda args, _context, _signal, _on_update: _run_experiment(api, args),
            "executionMode": "sequential",
        }
    )
    api.register_tool(
        {
            "name": "log_experiment",
            "label": "log experiment",
            "description": "Append autoresearch.jsonl and perform keep/discard git operations.",
            "parameters": _object_schema(
                {
                    "run_result": {"type": "object", "additionalProperties": True},
                    "status": {"type": "string", "enum": sorted(VALID_STATUSES)},
                    "restart_note": {"type": "string"},
                    "trial_id": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "changes": {"type": "string"},
                    "command": {"type": "string"},
                    "metrics": {"type": "object", "additionalProperties": {"type": "number"}},
                    "metrics_source": {"type": "string", "enum": ["returned_output", "artifact"]},
                    "exit_code": {"type": ["integer", "null"]},
                    "duration_ms": {"type": "integer"},
                    "artifact": {"type": "object", "additionalProperties": True},
                    "working_dir": {"type": "string"},
                },
                required=["status", "restart_note"],
            ),
            "execute": lambda args, _context, _signal, _on_update: _log_experiment(api, args),
            "executionMode": "sequential",
        }
    )
    api.register_tool(
        {
            "name": "recover_experiment",
            "label": "recover experiment",
            "description": "Resolve one explicit autoresearch pending recovery journal.",
            "parameters": _object_schema(
                {
                    "trial_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["auto", "abort"]},
                    "working_dir": {"type": "string"},
                },
                required=["trial_id", "action"],
            ),
            "execute": lambda args, _context, _signal, _on_update: _recover_experiment(api, args),
            "executionMode": "sequential",
        }
    )


def _init_experiment(api: Any, args: dict[str, Any]) -> dict[str, Any]:
    try:
        workdir = _resolve_workdir(api.cwd, args.get("working_dir", "."))
        _assert_no_pending(workdir)
        _require_git_top_level(workdir)
        _require_scratch_branch(workdir)
        objective = str(args.get("objective") or "Improve the QMD mini benchmark score.")
        command = str(args.get("command") or "bash autoresearch.sh")
        overwrite = bool(args.get("overwrite", False))
        created: list[str] = []
        preserved: list[str] = []

        _write_once(
            workdir / "autoresearch.md",
            _session_doc(objective, command),
            overwrite=overwrite,
            executable=False,
            created=created,
            preserved=preserved,
        )
        _write_once(
            workdir / "autoresearch.sh",
            _benchmark_script(),
            overwrite=overwrite,
            executable=True,
            created=created,
            preserved=preserved,
        )
        _write_once(
            workdir / "autoresearch.jsonl",
            "",
            overwrite=False,
            executable=False,
            created=created,
            preserved=preserved,
        )
        if bool(args.get("create_checks", False)):
            _write_once(
                workdir / "autoresearch.checks.sh",
                "#!/usr/bin/env bash\nset -euo pipefail\npython3 finetune/benchmark.py --config finetune/configs/baseline.json >/dev/null\n",
                overwrite=overwrite,
                executable=True,
                created=created,
                preserved=preserved,
            )
        details = {"created": created, "preserved": preserved, "working_dir": str(workdir)}
        return _tool_result("autoresearch session initialized", details)
    except Exception as exc:
        return _tool_result(str(exc), {"error": type(exc).__name__}, is_error=True)


async def _run_experiment(api: Any, args: dict[str, Any]) -> dict[str, Any]:
    trial_id = "trial"
    started = time.monotonic()
    try:
        workdir = _resolve_workdir(api.cwd, args.get("working_dir", "."))
        _assert_no_pending(workdir)
        trial_id = _validate_trial_id(args.get("trial_id") or "trial")
        command = str(args.get("command") or "bash autoresearch.sh")
        timeout = float(args.get("timeout_seconds") or 300)
        checks_timeout = float(args.get("checks_timeout_seconds") or 300)

        result = await _exec(api, _command_in_workdir(api.cwd, workdir, command), timeout)
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        exit_code = _exit_code(result)
        output = str(result.get("output") or "")
        artifact = _artifact_details(result, workdir)
        metrics, metrics_source = _metrics_from_result(output, artifact)
        status = _run_status(trial_id, exit_code, metrics)
        checks_result: dict[str, Any] | None = None

        if status in {"baseline", "ready"} and (workdir / "autoresearch.checks.sh").is_file():
            checks_result = await _exec(api, _command_in_workdir(api.cwd, workdir, "bash autoresearch.checks.sh"), checks_timeout)
            if _exit_code(checks_result) not in (0, None):
                status = "checks_failed"

        details = {
            "trial_id": trial_id,
            "hypothesis": str(args.get("hypothesis") or ""),
            "changes": str(args.get("changes") or ""),
            "command": command,
            "status": status,
            "metrics": metrics,
            "metrics_source": metrics_source,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "output_tail": _tail(output),
            "artifact": artifact,
            "checks": _checks_details(checks_result),
        }
        _write_run_result(workdir, details)
        return _tool_result(f"experiment {trial_id} finished with status {status}", details, is_error=status == "crash")
    except Exception as exc:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        details = {
            "error": type(exc).__name__,
            "trial_id": trial_id,
            "hypothesis": str(args.get("hypothesis") or ""),
            "changes": str(args.get("changes") or ""),
            "command": str(args.get("command") or "bash autoresearch.sh"),
            "status": "crash",
            "metrics": {},
            "metrics_source": "returned_output",
            "exit_code": None,
            "duration_ms": duration_ms,
            "output_tail": "",
            "artifact": {},
        }
        return _tool_result(str(exc), details, is_error=True)


def _log_experiment(api: Any, args: dict[str, Any]) -> dict[str, Any]:
    try:
        workdir = _resolve_workdir(api.cwd, args.get("working_dir", "."))
        _assert_no_pending(workdir)
        status = str(args["status"])
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")

        run_result = _resolve_run_result(args, workdir)
        _validate_log_run_result(status, run_result, explicit="run_result" in args)
        entry = _entry_from_args(args, run_result, status, workdir)
        _assert_unique_trial_id(workdir, entry["trial_id"])
        if status in {"keep", *REVERT_STATUSES}:
            _prepare_mutating_transaction(workdir)

        pending_path: Path | None = None
        pending: dict[str, Any] = {}
        if status in {"keep", *REVERT_STATUSES}:
            pending = _pending_journal(entry, workdir)
            pending_path = _write_pending_journal(workdir, entry["trial_id"], pending)

        if status == "keep":
            commit = _keep_changes(workdir)
            entry["commit"] = commit
            pending["mutation_result"] = {"commit": commit}
            _write_pending_journal(workdir, entry["trial_id"], pending)
        revert: dict[str, Any] | None = None
        if status in REVERT_STATUSES:
            revert = _discard_changes(workdir)
            entry["revert"] = revert
            pending["mutation_result"] = {"revert": revert}
            pending["revert_applied"] = True
            _write_pending_journal(workdir, entry["trial_id"], pending)
        entry = _redact(entry)
        _validate_entry(entry, final=True)
        _append_jsonl(workdir / "autoresearch.jsonl", entry)
        if pending_path is not None:
            pending_path.unlink(missing_ok=True)
        details = {"entry": entry, "working_dir": str(workdir)}
        if revert is not None:
            details["revert"] = revert
        return _tool_result(f"logged {entry['trial_id']} as {status}", details)
    except Exception as exc:
        return _tool_result(str(exc), {"error": type(exc).__name__}, is_error=True)


def _recover_experiment(api: Any, args: dict[str, Any]) -> dict[str, Any]:
    try:
        workdir = _resolve_workdir(api.cwd, args.get("working_dir", "."))
        trial_id = _validate_trial_id(args.get("trial_id"))
        action = str(args.get("action") or "")
        if action not in {"auto", "abort"}:
            raise ValueError(f"invalid recovery action: {action}")
        journal_path = _pending_journal_path(workdir, trial_id)
        if not journal_path.exists():
            raise ValueError(f"no pending found for {trial_id}")
        journal = _read_pending_journal(workdir, trial_id)
        if journal.get("trial_id") != trial_id:
            raise ValueError("pending journal trial_id mismatch")
        current_head = _prepare_recovery_transaction(workdir, str(journal.get("branch") or ""))
        status = str(journal.get("status") or "")
        pre_head = str(journal.get("pre_head") or "")
        planned_entry = dict(journal.get("planned_entry") or {})

        if action == "abort":
            if current_head != pre_head:
                raise ValueError("abort recovery requires HEAD to match pending pre_head")
            entry = _abort_entry(planned_entry)
            _append_if_missing(workdir, entry)
            journal_path.unlink(missing_ok=True)
            return _tool_result(f"aborted pending recovery for {trial_id}", {"entry": entry, "working_dir": str(workdir)})

        if status == "keep":
            if current_head == pre_head:
                journal_path.unlink(missing_ok=True)
                return _tool_result(
                    f"cleared pre-commit pending recovery for {trial_id}",
                    {"resolved": "pre_commit", "working_dir": str(workdir)},
                )
            _assert_autoresearch_commit(workdir, current_head, pre_head)
            planned_entry["commit"] = current_head
            planned_entry = _redact(planned_entry)
            _validate_entry(planned_entry, final=True)
            _append_if_missing(workdir, planned_entry)
            journal_path.unlink(missing_ok=True)
            return _tool_result(
                f"recovered keep entry for {trial_id}",
                {"entry": planned_entry, "working_dir": str(workdir)},
            )

        if status in REVERT_STATUSES:
            if current_head != pre_head:
                raise ValueError(f"{status} recovery requires HEAD to match pending pre_head")
            revert = _discard_changes(workdir)
            planned_entry["revert"] = revert
            planned_entry = _redact(planned_entry)
            _validate_entry(planned_entry, final=True)
            journal["mutation_result"] = {"revert": revert}
            journal["revert_applied"] = True
            _write_pending_journal(workdir, trial_id, journal)
            _append_if_missing(workdir, planned_entry)
            journal_path.unlink(missing_ok=True)
            return _tool_result(
                f"recovered {status} entry for {trial_id}",
                {"entry": planned_entry, "revert": revert, "working_dir": str(workdir)},
            )

        raise ValueError(f"unsupported pending status: {status}")
    except Exception as exc:
        return _tool_result(str(exc), {"error": type(exc).__name__}, is_error=True)


def _parse_metrics(text: str) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {}
    for line in text.splitlines():
        match = METRIC_RE.match(line.strip())
        if not match:
            continue
        name, raw_value = match.groups()
        if _is_secret_key(name):
            continue
        value = float(raw_value)
        if not math.isfinite(value):
            continue
        metrics[name] = int(value) if value.is_integer() and "." not in raw_value and "e" not in raw_value.lower() else value
    return metrics


def _metrics_from_result(output: str, artifact: dict[str, Any]) -> tuple[dict[str, float | int], str]:
    if artifact.get("truncated") and artifact.get("fullOutputPath"):
        path = Path(str(artifact["fullOutputPath"]))
        if path.is_file():
            return _parse_metrics(path.read_text(encoding="utf-8", errors="replace")), "artifact"
    return _parse_metrics(output), "returned_output"


async def _exec(api: Any, command: str, timeout: float) -> dict[str, Any]:
    result = api.exec(command, [], {"timeout": timeout})
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise TypeError("api.exec returned a non-object result")
    return result


def _entry_from_args(args: dict[str, Any], run_result: dict[str, Any], status: str, workdir: Path) -> dict[str, Any]:
    metrics = _provenance_value(args, run_result, "metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("metrics must be an object")
    metrics_source = _provenance_value(args, run_result, "metrics_source", "returned_output")
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "trial_id": _validate_trial_id(args.get("trial_id") or run_result.get("trial_id") or "trial"),
        "hypothesis": str(args.get("hypothesis") or run_result.get("hypothesis") or ""),
        "changes": str(args.get("changes") or run_result.get("changes") or ""),
        "command": _bounded_text(str(args.get("command") or run_result.get("command") or "")),
        "status": status,
        "metrics": metrics,
        "metrics_source": str(metrics_source),
        "exit_code": args.get("exit_code", run_result.get("exit_code")),
        "duration_ms": int(args.get("duration_ms", run_result.get("duration_ms", 0)) or 0),
        "restart_note": str(args["restart_note"]),
    }
    artifact = args.get("artifact", run_result.get("artifact"))
    if isinstance(artifact, dict) and artifact:
        sanitized_artifact = _sanitize_artifact_metadata(artifact, workdir)
        if sanitized_artifact:
            entry["artifact"] = sanitized_artifact
    _validate_entry(entry, final=False)
    return entry


def _resolve_run_result(args: dict[str, Any], workdir: Path) -> dict[str, Any]:
    if "run_result" in args:
        run_result = args.get("run_result")
        if not isinstance(run_result, dict):
            raise ValueError("run_result must be an object")
        return dict(run_result)
    trial_id = args.get("trial_id")
    if trial_id:
        return _read_run_result(workdir, _validate_trial_id(trial_id))
    unlogged = _unlogged_run_results(workdir)
    if len(unlogged) == 1:
        return unlogged[0]
    if len(unlogged) > 1:
        trial_ids = ", ".join(str(result.get("trial_id")) for result in unlogged)
        raise ValueError(f"trial_id is required because multiple unlogged run results exist: {trial_ids}")
    return {}


def _validate_log_run_result(status: str, run_result: dict[str, Any], *, explicit: bool) -> None:
    if status not in SUCCESS_STATUSES:
        if explicit and run_result and str(run_result.get("status") or status) != status:
            raise ValueError(f"{status} conflicts with run_result.status={run_result.get('status')}")
        return
    if not run_result:
        raise ValueError(f"{status} requires run_result from run_experiment")
    result_status = str(run_result.get("status") or "")
    expected = "baseline" if status == "baseline" else "ready"
    if result_status != expected:
        raise ValueError(f"{status} requires run_result.status={expected}")
    metrics = run_result.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError(f"{status} requires non-empty run_result.metrics")
    if run_result.get("exit_code") != 0:
        raise ValueError(f"{status} requires run_result.exit_code=0")


def _validate_entry(entry: dict[str, Any], *, final: bool) -> None:
    required = {
        "ts",
        "trial_id",
        "hypothesis",
        "changes",
        "command",
        "status",
        "metrics",
        "metrics_source",
        "exit_code",
        "duration_ms",
        "restart_note",
    }
    missing = required.difference(entry)
    if missing:
        raise ValueError(f"missing required fields: {', '.join(sorted(missing))}")
    _validate_trial_id(entry["trial_id"])
    if entry["status"] not in VALID_STATUSES:
        raise ValueError(f"invalid status: {entry['status']}")
    if entry["metrics_source"] not in {"returned_output", "artifact"}:
        raise ValueError(f"invalid metrics_source: {entry['metrics_source']}")
    if not isinstance(entry["metrics"], dict):
        raise ValueError("metrics must be an object")
    for name, value in entry["metrics"].items():
        if not METRIC_NAME_RE.match(str(name)) or _is_secret_key(name):
            raise ValueError(f"invalid metric name: {name}")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"invalid metric value for {name}")
        if not math.isfinite(float(value)):
            raise ValueError(f"non-finite metric value for {name}")
    if "commit" in entry and entry["status"] != "keep":
        raise ValueError("commit is only valid for keep entries")
    if "revert" in entry and entry["status"] not in REVERT_STATUSES:
        raise ValueError("revert is only valid for discard, crash, or checks_failed entries")
    if final and entry["status"] == "keep" and not entry.get("commit"):
        raise ValueError("keep entry requires commit")
    if final and entry["status"] in REVERT_STATUSES and not isinstance(entry.get("revert"), dict):
        raise ValueError(f"{entry['status']} entry requires revert")
    if entry["status"] == "baseline" and ("commit" in entry or "revert" in entry):
        raise ValueError("baseline cannot include commit or revert")


def _provenance_value(args: dict[str, Any], run_result: dict[str, Any], key: str, default: Any) -> Any:
    args_has = key in args
    result_has = key in run_result
    if args_has and result_has and args[key] != run_result[key]:
        raise ValueError(f"{key} conflicts with run_result.{key}")
    if result_has:
        return run_result[key]
    if args_has:
        return args[key]
    return default


def _validate_trial_id(value: Any) -> str:
    trial_id = str(value or "")
    if not TRIAL_ID_RE.fullmatch(trial_id):
        raise ValueError(f"invalid trial_id: {trial_id}")
    return trial_id


def _bounded_text(text: str, max_bytes: int = MAX_COMMAND_BYTES) -> str:
    encoded = text.encode("utf-8")
    marker = "<truncated>"
    if len(encoded) <= max_bytes:
        return text
    keep = max(0, max_bytes - len(marker.encode("utf-8")))
    return encoded[:keep].decode("utf-8", errors="ignore") + marker


def _is_trusted_artifact_path(workdir: Path, value: Any, artifact_root: Any = None) -> bool:
    try:
        path = Path(str(value)).expanduser().resolve()
    except OSError:
        return False
    trusted_roots = [(workdir / "autoresearch-artifacts").resolve()]
    if artifact_root:
        try:
            trusted_roots.append(Path(str(artifact_root)).expanduser().resolve())
        except OSError:
            pass
    return any(path == root or root in path.parents for root in trusted_roots)


def _sanitize_artifact_metadata(value: Any, workdir: Path) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if normalized in {"output", "stdout", "stderr", "full_output", "fulloutput", "raw_output", "rawoutput"}:
                continue
            if normalized in {"fulloutputpath", "full_output_path"}:
                if _is_trusted_artifact_path(workdir, item, value.get("artifactRoot")):
                    sanitized["fullOutputPath"] = str(Path(str(item)).expanduser().resolve())
                else:
                    sanitized["fullOutputPathRejected"] = True
                continue
            if normalized in {"artifactroot", "artifact_root"}:
                continue
            sanitized[key_text] = _sanitize_artifact_metadata(item, workdir)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_artifact_metadata(item, workdir) for item in value]
    if isinstance(value, str):
        return _bounded_text(value)
    return value


def _run_status(trial_id: str, exit_code: int | None, metrics: dict[str, float | int]) -> str:
    if exit_code not in (0, None):
        return "crash"
    if not metrics:
        return "crash"
    if trial_id == "baseline":
        return "baseline"
    return "ready"


def _artifact_details(result: dict[str, Any], workdir: Path) -> dict[str, Any]:
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    truncation = details.get("truncation") if isinstance(details.get("truncation"), dict) else {}
    artifact = {
        "truncated": bool(result.get("truncated") or truncation.get("truncated")),
        "truncation": truncation,
    }
    full_output_path = result.get("fullOutputPath") or details.get("fullOutputPath")
    if full_output_path and _is_trusted_artifact_path(workdir, full_output_path, details.get("artifactRoot")):
        artifact["fullOutputPath"] = str(Path(str(full_output_path)).expanduser().resolve())
    elif full_output_path:
        artifact["fullOutputPathRejected"] = True
    return artifact


def _checks_details(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {"exit_code": _exit_code(result), "output_tail": _tail(str(result.get("output") or ""))}


def _exit_code(result: dict[str, Any]) -> int | None:
    value = result.get("exitCode", result.get("exit_code"))
    return value if isinstance(value, int) else None


def _tail(text: str, limit: int = 4096) -> str:
    return text if len(text) <= limit else text[-limit:]


def _resolve_workdir(root_value: str, value: Any) -> Path:
    root = Path(root_value).expanduser().resolve()
    workdir = (root / str(value or ".")).resolve()
    if not (workdir == root or root in workdir.parents):
        raise ValueError(f"working_dir escapes extension cwd: {value}")
    if not workdir.exists():
        raise ValueError(f"working_dir does not exist: {value}")
    if not workdir.is_dir():
        raise ValueError(f"working_dir is not a directory: {value}")
    return workdir


def _command_in_workdir(root_value: str, workdir: Path, command: str) -> str:
    root = Path(root_value).expanduser().resolve()
    relative = workdir.relative_to(root)
    return f"cd {shlex.quote(str(relative) or '.')} && {command}"


def _write_once(
    path: Path,
    content: str,
    *,
    overwrite: bool,
    executable: bool,
    created: list[str],
    preserved: list[str],
) -> None:
    if path.exists() and not overwrite:
        preserved.append(path.name)
        return
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    created.append(path.name)


def _session_doc(objective: str, command: str) -> str:
    return (
        "# Autoresearch Session\n\n"
        f"## Objective\n{objective}\n\n"
        "## Metric\nPrimary metric is `score`; higher is better.\n\n"
        "## Files in Scope\n- `finetune/configs/baseline.json`\n- `finetune/benchmark.py`\n\n"
        "## Benchmark\n"
        f"`{command}`\n\n"
        "## Tried\nNo trials yet.\n\n"
        "## Restart Note\nRead this file, `autoresearch.jsonl`, and `git log --oneline -5` before proposing the next trial.\n"
    )


def _benchmark_script() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "python3 finetune/benchmark.py --config finetune/configs/baseline.json\n"
    )


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    serialized = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(serialized + "\n")


def _keep_changes(workdir: Path) -> str:
    _reset_preserved_staged_paths(workdir)
    _git(workdir, "add", "-A", "--", ".")
    for rel in _preserved_pathspecs(workdir):
        _git(workdir, "reset", "--", rel, check=False)
        if not (workdir / rel).exists():
            _git(workdir, "checkout", "--", rel, check=False)
    if _git(workdir, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise ValueError("keep requested but no non-autoresearch changes are staged")
    _git(
        workdir,
        "-c",
        "user.name=NeoMAGI Autoresearch",
        "-c",
        "user.email=autoresearch@example.invalid",
        "commit",
        "-m",
        "chore(autoresearch): keep experiment",
    )
    return _git(workdir, "rev-parse", "HEAD").stdout.strip()


def _discard_changes(workdir: Path) -> dict[str, Any]:
    reverted: list[str] = []
    removed: list[str] = []
    removed_dirs: list[str] = []
    _reset_preserved_staged_paths(workdir)
    for rel in _git_lines(workdir, "diff", "--name-only"):
        if not _is_preserved(rel):
            _git(workdir, "checkout", "--", rel, check=False)
            reverted.append(rel)
    for rel in _git_lines(workdir, "diff", "--cached", "--name-only"):
        if not _is_preserved(rel):
            _git(workdir, "reset", "--", rel, check=False)
            _git(workdir, "checkout", "--", rel, check=False)
            reverted.append(rel)
    _restore_deleted_preserved_paths(workdir)
    for rel in _git_lines(workdir, "ls-files", "--others", "--exclude-standard"):
        if _is_preserved(rel):
            continue
        target = workdir / rel
        if not _is_safe_child_path(workdir, target):
            continue
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)
        removed.append(rel)
        _remove_empty_parents(workdir, target.parent, removed_dirs)
    return {"reverted": sorted(set(reverted)), "removed": sorted(set(removed)), "removed_dirs": sorted(set(removed_dirs))}


def _prepare_mutating_transaction(workdir: Path) -> None:
    _require_git_top_level(workdir)
    _require_existing_head(workdir)
    _require_scratch_branch(workdir)
    _reset_preserved_staged_paths(workdir)
    if _git(workdir, "diff", "--cached", "--quiet", check=False).returncode != 0:
        raise ValueError("autoresearch refuses pre-existing staged changes")


def _prepare_recovery_transaction(workdir: Path, expected_branch: str) -> str:
    _require_git_top_level(workdir)
    current_head = _require_existing_head(workdir)
    branch = _require_scratch_branch(workdir)
    if branch != expected_branch:
        raise ValueError(f"pending journal branch mismatch: {expected_branch}")
    if _git(workdir, "diff", "--cached", "--quiet", check=False).returncode != 0:
        raise ValueError("autoresearch refuses pre-existing staged changes")
    return current_head


def _require_git_top_level(workdir: Path) -> None:
    result = _git(workdir, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise ValueError("autoresearch working_dir must be a git repository")
    top = Path(result.stdout.strip()).resolve()
    if top != workdir.resolve():
        raise ValueError("autoresearch working_dir must be the git top-level")


def _require_existing_head(workdir: Path) -> str:
    result = _git(workdir, "rev-parse", "--verify", "HEAD", check=False)
    if result.returncode != 0:
        raise ValueError("autoresearch refuses mutation on unborn HEAD")
    return result.stdout.strip()


def _current_branch(workdir: Path) -> str:
    branch = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch == "HEAD":
        raise ValueError("autoresearch refuses detached HEAD")
    return branch


def _require_scratch_branch(workdir: Path) -> str:
    branch = _current_branch(workdir)
    default_branch = _default_branch(workdir)
    if branch in {"main", "master", "scratch", "scratchpad", default_branch} or not SCRATCH_BRANCH_RE.fullmatch(branch):
        raise ValueError(f"autoresearch refuses default or non-scratch branch: {branch}")
    return branch


def _default_branch(workdir: Path) -> str | None:
    result = _git(workdir, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value.rsplit("/", 1)[-1] if value else None


def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=workdir, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"git {' '.join(args)} failed")
    return result


def _git_lines(workdir: Path, *args: str) -> list[str]:
    output = _git(workdir, *args).stdout
    return [line for line in output.splitlines() if line]


def _existing_preserved_paths(workdir: Path) -> list[str]:
    candidates = [*sorted(PRESERVED_DIRS), *sorted(PRESERVED_FILES)]
    return [rel for rel in candidates if (workdir / rel).exists()]


def _preserved_pathspecs(workdir: Path) -> list[str]:
    pathspecs = set(_existing_preserved_paths(workdir))
    for rel in _git_lines(workdir, "ls-files"):
        if not _is_preserved(rel):
            continue
        first = rel.split("/", 1)[0]
        pathspecs.add(first if first in PRESERVED_DIRS else rel)
    return sorted(pathspecs)


def _is_preserved(rel: str) -> bool:
    parts = Path(rel).as_posix().split("/")
    return rel in PRESERVED_FILES or (bool(parts) and parts[0] in PRESERVED_DIRS)


def _reset_preserved_staged_paths(workdir: Path) -> None:
    for rel in _git_lines(workdir, "diff", "--cached", "--name-only"):
        if _is_preserved(rel):
            _git(workdir, "reset", "--", rel, check=False)
            if not (workdir / rel).exists():
                _git(workdir, "checkout", "--", rel, check=False)


def _restore_deleted_preserved_paths(workdir: Path) -> None:
    for rel in _git_lines(workdir, "diff", "--name-only"):
        if _is_preserved(rel) and not (workdir / rel).exists():
            _git(workdir, "checkout", "--", rel, check=False)


def _is_safe_child_path(workdir: Path, path: Path) -> bool:
    if path.is_symlink():
        absolute = path.absolute()
        return absolute != workdir and workdir in absolute.parents
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved != workdir and workdir in resolved.parents


def _remove_empty_parents(workdir: Path, directory: Path, removed_dirs: list[str]) -> None:
    current = directory.resolve()
    while current != workdir and workdir in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        removed_dirs.append(current.relative_to(workdir).as_posix())
        current = current.parent


def _pending_journal(entry: dict[str, Any], workdir: Path) -> dict[str, Any]:
    return {
        "trial_id": entry["trial_id"],
        "status": entry["status"],
        "branch": _require_scratch_branch(workdir),
        "pre_head": _require_existing_head(workdir),
        "started_at": datetime.now(UTC).isoformat(),
        "planned_entry": _redact(dict(entry)),
    }


def _pending_journal_path(workdir: Path, trial_id: str) -> Path:
    return workdir / "autoresearch-artifacts" / _validate_trial_id(trial_id) / "pending.json"


def _write_pending_journal(workdir: Path, trial_id: str, journal: dict[str, Any]) -> Path:
    path = _pending_journal_path(workdir, trial_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name("pending.json.tmp")
    tmp_path.write_text(
        json.dumps(journal, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
    return path


def _read_pending_journal(workdir: Path, trial_id: str) -> dict[str, Any]:
    path = _pending_journal_path(workdir, trial_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"corrupt pending journal for {trial_id}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"corrupt pending journal for {trial_id}: expected object")
    return data


def _pending_journals(workdir: Path) -> list[Path]:
    root = workdir / "autoresearch-artifacts"
    if not root.exists():
        return []
    return sorted(root.glob("*/pending.json"))


def _assert_no_pending(workdir: Path) -> None:
    journals = _pending_journals(workdir)
    if journals:
        rels = ", ".join(path.relative_to(workdir).as_posix() for path in journals)
        raise ValueError(f"pending recovery journal must be resolved first: {rels}")


def _read_jsonl_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"invalid JSONL at line {line_number}: expected object")
        entries.append(data)
    return entries


def _trial_id_exists(workdir: Path, trial_id: str) -> bool:
    return any(entry.get("trial_id") == trial_id for entry in _read_jsonl_entries(workdir / "autoresearch.jsonl"))


def _assert_unique_trial_id(workdir: Path, trial_id: str) -> None:
    if _trial_id_exists(workdir, trial_id):
        raise ValueError(f"trial_id already exists: {trial_id}")


def _append_if_missing(workdir: Path, entry: dict[str, Any]) -> bool:
    if _trial_id_exists(workdir, entry["trial_id"]):
        return False
    _append_jsonl(workdir / "autoresearch.jsonl", entry)
    return True


def _run_result_path(workdir: Path, trial_id: str) -> Path:
    return workdir / "autoresearch-artifacts" / _validate_trial_id(trial_id) / "run_result.json"


def _write_run_result(workdir: Path, details: dict[str, Any]) -> Path:
    trial_id = _validate_trial_id(details.get("trial_id") or "")
    path = _run_result_path(workdir, trial_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name("run_result.json.tmp")
    tmp_path.write_text(
        json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
    return path


def _read_run_result(workdir: Path, trial_id: str) -> dict[str, Any]:
    path = _run_result_path(workdir, trial_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise ValueError(f"corrupt run result for {trial_id}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"corrupt run result for {trial_id}: expected object")
    return data


def _unlogged_run_results(workdir: Path) -> list[dict[str, Any]]:
    root = workdir / "autoresearch-artifacts"
    if not root.exists():
        return []
    logged = {str(entry.get("trial_id")) for entry in _read_jsonl_entries(workdir / "autoresearch.jsonl")}
    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/run_result.json")):
        data = _read_run_result(workdir, path.parent.name)
        if data and str(data.get("trial_id")) not in logged:
            results.append(data)
    return results


def _abort_entry(planned_entry: dict[str, Any]) -> dict[str, Any]:
    entry = dict(planned_entry)
    entry["status"] = "crash"
    entry["metrics"] = {}
    entry["metrics_source"] = "returned_output"
    entry["exit_code"] = None
    entry.pop("commit", None)
    entry.pop("revert", None)
    note = str(entry.get("restart_note") or "")
    entry["restart_note"] = f"{note}; recovery aborted" if note else "recovery aborted"
    entry = _redact(entry)
    _validate_entry(entry, final=False)
    return entry


def _assert_autoresearch_commit(workdir: Path, head: str, pre_head: str) -> None:
    parent = _git(workdir, "rev-parse", f"{head}^").stdout.strip()
    author_name = _git(workdir, "show", "-s", "--format=%an", head).stdout.strip()
    author_email = _git(workdir, "show", "-s", "--format=%ae", head).stdout.strip()
    subject = _git(workdir, "show", "-s", "--format=%s", head).stdout.strip()
    if parent != pre_head:
        raise ValueError("pending keep commit parent mismatch")
    if author_name != "NeoMAGI Autoresearch" or author_email != "autoresearch@example.invalid":
        raise ValueError("pending keep commit attribution mismatch")
    if subject != "chore(autoresearch): keep experiment":
        raise ValueError("pending keep commit subject mismatch")


def _is_secret_key(key: Any) -> bool:
    text = str(key)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return bool(SECRET_KEY_RE.search(normalized))


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            redacted[key] = f"<redacted:{key}>" if _is_secret_key(key) and isinstance(item, str) else _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        text = value
        for key, secret in os.environ.items():
            if _is_secret_key(key) and secret and (len(secret) >= 8 or SECRET_VALUE_RE.search(secret)):
                text = text.replace(secret, f"<redacted:{key}>")
        return SECRET_VALUE_RE.sub("<redacted:secret>", text)
    return value


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _tool_result(message: str, details: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "details": details, "isError": is_error}

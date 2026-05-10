"""QMD autoresearch mini showcase extension.

The extension is intentionally self-contained so the real QMD runbook can copy
this file into a scratch checkout without expanding NeoMAGI core.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


METRIC_RE = re.compile(r"^METRIC\s+([A-Za-z][A-Za-z0-9_]{0,63})=(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$")
SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(?:token|secret|password|authorization|cookie)(?:$|[_-])|(?:^|[_-])api[_-]?key(?:$|[_-])",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|hf_[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|"
    r"github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b"
)
VALID_STATUSES = {"baseline", "keep", "discard", "crash", "checks_failed"}
PRESERVED_PREFIXES = ("autoresearch.", "autoresearch-artifacts/", ".magipi/")
PRESERVED_FILES = {"autoresearch.md", "autoresearch.sh", "autoresearch.jsonl", ".magipi"}


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


def _init_experiment(api: Any, args: dict[str, Any]) -> dict[str, Any]:
    try:
        workdir = _resolve_workdir(api.cwd, args.get("working_dir", "."))
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
            overwrite=overwrite,
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
    try:
        workdir = _resolve_workdir(api.cwd, args.get("working_dir", "."))
        trial_id = str(args.get("trial_id") or "trial")
        command = str(args.get("command") or "bash autoresearch.sh")
        timeout = float(args.get("timeout_seconds") or 300)
        checks_timeout = float(args.get("checks_timeout_seconds") or 300)
        started = time.monotonic()

        result = await _exec(api, _command_in_workdir(api.cwd, workdir, command), timeout)
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        exit_code = _exit_code(result)
        output = str(result.get("output") or "")
        artifact = _artifact_details(result)
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
        return _tool_result(f"experiment {trial_id} finished with status {status}", details, is_error=status == "crash")
    except Exception as exc:
        return _tool_result(str(exc), {"error": type(exc).__name__}, is_error=True)


def _log_experiment(api: Any, args: dict[str, Any]) -> dict[str, Any]:
    try:
        workdir = _resolve_workdir(api.cwd, args.get("working_dir", "."))
        run_result = dict(args.get("run_result") or {})
        status = str(args["status"])
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        if status in {"keep", "discard"}:
            _refuse_default_branch(workdir)

        entry = _entry_from_args(args, run_result, status)
        if status == "keep":
            commit = _keep_changes(workdir)
            entry["commit"] = commit
        revert: dict[str, Any] | None = None
        if status == "discard":
            revert = _discard_changes(workdir)
            entry["revert"] = revert
        entry = _redact(entry)
        _append_jsonl(workdir / "autoresearch.jsonl", entry)
        details = {"entry": entry, "working_dir": str(workdir)}
        if revert is not None:
            details["revert"] = revert
        return _tool_result(f"logged {entry['trial_id']} as {status}", details)
    except Exception as exc:
        return _tool_result(str(exc), {"error": type(exc).__name__}, is_error=True)


def _parse_metrics(text: str) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {}
    for line in text.splitlines():
        match = METRIC_RE.match(line.strip())
        if not match:
            continue
        name, raw_value = match.groups()
        value = float(raw_value)
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


def _entry_from_args(args: dict[str, Any], run_result: dict[str, Any], status: str) -> dict[str, Any]:
    metrics = args.get("metrics", run_result.get("metrics", {}))
    if not isinstance(metrics, dict):
        raise ValueError("metrics must be an object")
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "trial_id": str(args.get("trial_id") or run_result.get("trial_id") or "trial"),
        "hypothesis": str(args.get("hypothesis") or run_result.get("hypothesis") or ""),
        "changes": str(args.get("changes") or run_result.get("changes") or ""),
        "command": str(args.get("command") or run_result.get("command") or ""),
        "status": status,
        "metrics": metrics,
        "metrics_source": str(args.get("metrics_source") or run_result.get("metrics_source") or "returned_output"),
        "exit_code": args.get("exit_code", run_result.get("exit_code")),
        "duration_ms": int(args.get("duration_ms", run_result.get("duration_ms", 0)) or 0),
        "restart_note": str(args["restart_note"]),
    }
    artifact = args.get("artifact", run_result.get("artifact"))
    if isinstance(artifact, dict) and artifact:
        entry["artifact"] = artifact
    _validate_entry(entry)
    return entry


def _validate_entry(entry: dict[str, Any]) -> None:
    if entry["status"] not in VALID_STATUSES:
        raise ValueError(f"invalid status: {entry['status']}")
    if entry["metrics_source"] not in {"returned_output", "artifact"}:
        raise ValueError(f"invalid metrics_source: {entry['metrics_source']}")
    if not isinstance(entry["metrics"], dict):
        raise ValueError("metrics must be an object")
    for name, value in entry["metrics"].items():
        if not METRIC_RE.match(f"METRIC {name}=0"):
            raise ValueError(f"invalid metric name: {name}")
        if not isinstance(value, int | float):
            raise ValueError(f"invalid metric value for {name}")


def _run_status(trial_id: str, exit_code: int | None, metrics: dict[str, float | int]) -> str:
    if exit_code not in (0, None):
        return "crash"
    if not metrics:
        return "crash"
    if trial_id == "baseline":
        return "baseline"
    return "ready"


def _artifact_details(result: dict[str, Any]) -> dict[str, Any]:
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    truncation = details.get("truncation") if isinstance(details.get("truncation"), dict) else {}
    return {
        "truncated": bool(result.get("truncated") or truncation.get("truncated")),
        "fullOutputPath": result.get("fullOutputPath") or details.get("fullOutputPath"),
        "truncation": truncation,
    }


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
    workdir.mkdir(parents=True, exist_ok=True)
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
    serialized = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(serialized + "\n")


def _keep_changes(workdir: Path) -> str:
    _git(workdir, "add", "-A", "--", ".")
    for rel in _existing_preserved_paths(workdir):
        _git(workdir, "reset", "--", rel, check=False)
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
    for rel in _git_lines(workdir, "diff", "--name-only"):
        if not _is_preserved(rel):
            _git(workdir, "checkout", "--", rel, check=False)
            reverted.append(rel)
    for rel in _git_lines(workdir, "diff", "--cached", "--name-only"):
        if not _is_preserved(rel):
            _git(workdir, "reset", "--", rel, check=False)
            _git(workdir, "checkout", "--", rel, check=False)
            reverted.append(rel)
    for rel in _git_lines(workdir, "ls-files", "--others", "--exclude-standard"):
        if _is_preserved(rel):
            continue
        target = (workdir / rel).resolve()
        if target == workdir or workdir not in target.parents:
            continue
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        removed.append(rel)
        _remove_empty_parents(workdir, target.parent, removed_dirs)
    return {"reverted": sorted(set(reverted)), "removed": sorted(set(removed)), "removed_dirs": sorted(set(removed_dirs))}


def _refuse_default_branch(workdir: Path) -> None:
    branch = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch in {"HEAD", "main", "master", _default_branch(workdir)}:
        raise ValueError(f"autoresearch refuses default or detached branch: {branch}")


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
    return [rel for rel in [".magipi", "autoresearch.md", "autoresearch.sh", "autoresearch.jsonl", "autoresearch.checks.sh", "autoresearch-artifacts"] if (workdir / rel).exists()]


def _is_preserved(rel: str) -> bool:
    return rel in PRESERVED_FILES or rel.startswith(PRESERVED_PREFIXES)


def _remove_empty_parents(workdir: Path, directory: Path, removed_dirs: list[str]) -> None:
    current = directory.resolve()
    while current != workdir and workdir in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        removed_dirs.append(current.relative_to(workdir).as_posix())
        current = current.parent


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
            if _is_secret_key(key) and secret and len(secret) >= 4:
                text = text.replace(secret, f"<redacted:{key}>")
        return SECRET_VALUE_RE.sub("<redacted:secret>", text)
    return value


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _tool_result(message: str, details: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "details": details, "isError": is_error}

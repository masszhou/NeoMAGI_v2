"""Detached background execution for long-running research steps.

The magipi bash tool caps a single command at 600 seconds, while a real
Parameter Golf attempt (~480s budget plus eval/records overhead) and an
external xhigh audit both routinely exceed it. Long steps therefore run as
detached worker processes that re-invoke the same synchronous CLI command;
all TaskRun events are appended by the worker, and the conductor polls
`research job-status` until the durable workflow state reflects completion.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from cli.core.research_workflow_store import (
    ResearchWorkflowState,
    ResearchWorkflowStoreError,
)

_LOG_TAIL_BYTES = 4096


def build_worker_argv(source_argv: list[str] | None = None) -> list[str]:
    """Rebuild the current `taskrun ...` invocation minus `--background`."""

    argv = list(source_argv if source_argv is not None else sys.argv)
    try:
        start = argv.index("taskrun")
    except ValueError as exc:
        raise ResearchWorkflowStoreError(
            "cannot derive worker command: 'taskrun' not in argv"
        ) from exc
    tail = [arg for arg in argv[start:] if arg != "--background"]
    return [sys.executable, "-m", "cli", *tail]


def spawn_background_job(
    state: ResearchWorkflowState,
    *,
    kind: str,
    node_id: str,
    worker_argv: list[str],
    cwd: Path,
) -> dict[str, Any]:
    jobs_dir = state.records_root / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    log_path = jobs_dir / f"{job_id}.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            worker_argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            start_new_session=True,
        )
    record = {
        "job_id": job_id,
        "kind": kind,
        "node_id": node_id,
        "pid": process.pid,
        "worker_argv": worker_argv,
        "log_ref": str(log_path),
        "cwd": str(cwd),
    }
    (jobs_dir / f"{job_id}.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    return {**record, "background": True, "poll_with": "research job-status"}


def background_job_status(state: ResearchWorkflowState, job_id: str) -> dict[str, Any]:
    job_file = state.records_root / "jobs" / f"{job_id}.json"
    if not job_file.is_file():
        raise ResearchWorkflowStoreError(f"unknown research job: {job_id}")
    record = json.loads(job_file.read_text())
    pid = int(record.get("pid") or 0)
    record["running"] = _pid_alive(pid)
    record["log_tail"] = _log_tail(Path(str(record.get("log_ref") or "")))
    return record


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # Reap our own exited children first: a zombie still answers kill(0).
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return _proc_state(pid) != "Z"


def _proc_state(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return "?"


def _log_tail(log_path: Path) -> str:
    if not log_path.is_file():
        return ""
    data = log_path.read_bytes()
    return data[-_LOG_TAIL_BYTES:].decode("utf-8", errors="replace")


__all__ = [
    "background_job_status",
    "build_worker_argv",
    "spawn_background_job",
]

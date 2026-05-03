"""Local subprocess sandbox used by governed bash execution."""

from __future__ import annotations

import asyncio
import os
import signal as signal_module
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_SUBPROCESS_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "TERM",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
    }
)
_DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


@dataclass(frozen=True, slots=True)
class SandboxResult:
    output: str
    exit_code: int | None
    cancelled: bool
    timed_out: bool


async def run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: float,
    signal: asyncio.Event | None = None,
    on_data: Callable[[bytes], None] | None = None,
) -> SandboxResult:
    process = await _spawn_shell(command, cwd)
    chunks: list[bytes] = []
    output_task = asyncio.create_task(_read_output(process, chunks, on_data))
    wait_task, timeout_task, abort_task, tasks = _wait_tasks(process, timeout, signal)
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    cancelled, timed_out = _apply_cancellation(process, timeout_task, abort_task, done)
    await _settle_process(process, wait_task, cancelled)
    await _cancel_pending(pending)
    await output_task

    return SandboxResult(
        output=b"".join(chunks).decode("utf-8", errors="replace"),
        exit_code=process.returncode,
        cancelled=cancelled,
        timed_out=timed_out,
    )


async def _spawn_shell(command: str, cwd: Path) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        env=sandbox_environment(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )


def sandbox_environment(cwd: Path) -> dict[str, str]:
    env = {
        key: value
        for key in _SUBPROCESS_ENV_ALLOWLIST
        if isinstance((value := os.environ.get(key)), str)
    }
    env.setdefault("PATH", _DEFAULT_PATH)
    env["PWD"] = str(cwd)
    return env


async def _read_output(
    process: asyncio.subprocess.Process,
    chunks: list[bytes],
    on_data: Callable[[bytes], None] | None,
) -> None:
    assert process.stdout is not None
    while True:
        chunk = await process.stdout.read(4096)
        if not chunk:
            return
        chunks.append(chunk)
        if on_data is not None:
            on_data(chunk)


def _wait_tasks(
    process: asyncio.subprocess.Process,
    timeout: float,
    signal: asyncio.Event | None,
) -> tuple[asyncio.Task[int], asyncio.Task[None], asyncio.Task[bool] | None, list[asyncio.Task[object]]]:
    wait_task = asyncio.create_task(process.wait())
    timeout_task = asyncio.create_task(asyncio.sleep(timeout))
    abort_task = asyncio.create_task(signal.wait()) if signal is not None else None
    tasks: list[asyncio.Task[object]] = [wait_task, timeout_task]
    if abort_task is not None:
        tasks.append(abort_task)
    return wait_task, timeout_task, abort_task, tasks


def _apply_cancellation(
    process: asyncio.subprocess.Process,
    timeout_task: asyncio.Task[None],
    abort_task: asyncio.Task[bool] | None,
    done: set[asyncio.Task[object]],
) -> tuple[bool, bool]:
    timed_out = timeout_task in done
    aborted = abort_task is not None and abort_task in done
    if timed_out or aborted:
        _terminate_process_group(process)
    return timed_out or aborted, timed_out


async def _settle_process(
    process: asyncio.subprocess.Process,
    wait_task: asyncio.Task[int],
    cancelled: bool,
) -> None:
    if not cancelled:
        await wait_task
        return
    try:
        await asyncio.wait_for(wait_task, timeout=2)
    except asyncio.TimeoutError:
        _kill_process_group(process)
        await wait_task


async def _cancel_pending(pending: set[asyncio.Task[object]]) -> None:
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal_module.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        process.terminate()


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal_module.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        process.kill()


__all__ = ["SandboxResult", "run_shell_command", "sandbox_environment"]

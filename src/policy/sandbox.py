"""Local subprocess sandbox used by governed bash execution."""

from __future__ import annotations

import asyncio
import os
import signal as signal_module
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


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
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    chunks: list[bytes] = []
    timed_out = False

    async def read_output() -> None:
        assert process.stdout is not None
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if on_data is not None:
                on_data(chunk)

    output_task = asyncio.create_task(read_output())
    wait_task = asyncio.create_task(process.wait())
    abort_task = asyncio.create_task(signal.wait()) if signal is not None else None
    timeout_task = asyncio.create_task(asyncio.sleep(timeout))
    tasks = [wait_task, timeout_task]
    if abort_task is not None:
        tasks.append(abort_task)

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    cancelled = False
    if timeout_task in done:
        timed_out = True
        cancelled = True
        _terminate_process_group(process)
    elif abort_task is not None and abort_task in done:
        cancelled = True
        _terminate_process_group(process)

    if cancelled:
        try:
            await asyncio.wait_for(wait_task, timeout=2)
        except asyncio.TimeoutError:
            _kill_process_group(process)
            await wait_task
    else:
        await wait_task

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await output_task

    return SandboxResult(
        output=b"".join(chunks).decode("utf-8", errors="replace"),
        exit_code=process.returncode,
        cancelled=cancelled,
        timed_out=timed_out,
    )


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


__all__ = ["SandboxResult", "run_shell_command"]

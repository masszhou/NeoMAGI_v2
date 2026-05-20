"""Read-only workspace projection for TaskRun summaries."""

from __future__ import annotations

import subprocess
from pathlib import Path


def workspace_state(workspace_root: str, projection_path: Path) -> dict[str, object]:
    # Read-only summary projection stays outside the governed host-command seam;
    # it must not create permission-decision writes for status/history/next views.
    state: dict[str, object] = {
        "workspace_root": workspace_root,
        "projection_path": str(projection_path),
        "git": {"status": "unknown"},
    }
    if workspace_root.startswith("-"):
        return state
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                workspace_root,
                "status",
                "--short",
                "--untracked-files=no",
                "--",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return state
    if result.returncode != 0:
        return state
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    state["git"] = {
        "status": "dirty" if lines else "clean",
        "changed_tracked_paths": len(lines),
    }
    return state


__all__ = ["workspace_state"]

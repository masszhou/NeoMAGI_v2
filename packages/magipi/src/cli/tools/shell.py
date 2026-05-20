"""Runtime-scoped bash artifact helpers."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class RuntimeArtifactStore:
    def __init__(self, runtime_session_id: str) -> None:
        self.runtime_session_id = runtime_session_id
        base = Path(tempfile.gettempdir()) / "neomagi-runtime"
        if base.exists() and base.is_symlink():
            raise RuntimeError(f"runtime artifact base is a symlink: {base}")
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        if base.is_symlink():
            raise RuntimeError(f"runtime artifact base is a symlink: {base}")
        base.chmod(0o700)
        safe_runtime_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in runtime_session_id)
        self.root = Path(tempfile.mkdtemp(prefix=f"{safe_runtime_id}-", dir=base))
        self.root.chmod(0o700)

    def output_path(self, tool_call_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in tool_call_id)
        return self.root / f"{safe}.out"

    def write_output(self, tool_call_id: str, output: str) -> Path:
        path = self.output_path(tool_call_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(output)
        path.chmod(0o600)
        return path

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


__all__ = ["RuntimeArtifactStore"]

"""Runtime-scoped bash artifact helpers."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class RuntimeArtifactStore:
    def __init__(self, runtime_session_id: str) -> None:
        self.runtime_session_id = runtime_session_id
        self.root = Path(tempfile.gettempdir()) / "neomagi-runtime" / runtime_session_id
        self.root.mkdir(parents=True, exist_ok=True)

    def output_path(self, tool_call_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in tool_call_id)
        return self.root / f"{safe}.out"

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


__all__ = ["RuntimeArtifactStore"]

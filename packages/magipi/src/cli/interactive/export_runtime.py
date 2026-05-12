"""Interactive runtime helpers for local session export/import."""

from __future__ import annotations

from pathlib import Path

from storage.session_repository import SessionRecord


class SessionExportRuntimeMixin:
    def export_jsonl(self, path: str | Path, *, allowed_root: str | Path | None = None) -> Path:
        self._ensure_idle_for_runtime_action("session export is not available while streaming")
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        return self._session_manager.export_jsonl(
            self._durable_session.id,
            path,
            allowed_root=allowed_root,
        )

    def export_pi_jsonl(self, path: str | Path, *, allowed_root: str | Path | None = None) -> Path:
        self._ensure_idle_for_runtime_action("session export is not available while streaming")
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        return self._session_manager.export_pi_jsonl(
            self._durable_session.id,
            path,
            allowed_root=allowed_root,
        )

    def export_structured_json(
        self,
        path: str | Path,
        *,
        allowed_root: str | Path | None = None,
    ) -> Path:
        self._ensure_idle_for_runtime_action("session export is not available while streaming")
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        return self._session_manager.export_structured_json(
            self._durable_session.id,
            path,
            allowed_root=allowed_root,
        )

    def export_html(self, path: str | Path, *, allowed_root: str | Path | None = None) -> Path:
        self._ensure_idle_for_runtime_action("session export is not available while streaming")
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        return self._session_manager.export_html(
            self._durable_session.id,
            path,
            allowed_root=allowed_root,
        )

    def import_jsonl(
        self,
        path: str | Path,
        *,
        allowed_root: str | Path | None = None,
    ) -> SessionRecord:
        self._ensure_idle_for_session_switch()
        if self._session_manager is None:
            raise RuntimeError("durable session manager is not available")
        session = self._session_manager.import_jsonl(path, allowed_root=allowed_root)
        self._activate_durable_session(session)
        return session

    def last_assistant_text(self) -> str | None:
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        return self._session_manager.last_assistant_text(self._durable_session.id)

__all__ = ["SessionExportRuntimeMixin"]

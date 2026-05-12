"""Model/settings runtime mutation mixin for ``InteractiveAgentRuntime``."""

from __future__ import annotations

from ai_provider.credentials import resolve_api_key
from ai_provider.model_registry import (
    canonical_model_ref,
    list_models,
    resolve_model,
    validate_thinking_level_for_model,
)
from ai_provider.types import CacheRetention, ThinkingLevel
from cli.core.model_settings import apply_settings_models
from cli.core.settings import LoadedSettings, SettingsManager
from cli.tools import convert_coding_messages_to_llm


class ModelRuntimeMixin:
    def _initialize_model_settings(
        self,
        model_ref: str,
        thinking_level: ThinkingLevel,
    ) -> None:
        self._settings_manager = SettingsManager(cwd=self._cwd)
        apply_settings_models(self._settings_manager.load().settings)
        self._model = resolve_model(model_ref)
        self._model_ref = canonical_model_ref(self._model)
        self._thinking_level = validate_thinking_level_for_model(
            self._model,
            thinking_level,
        )

    @property
    def settings_manager(self) -> SettingsManager:
        return self._settings_manager

    def settings_snapshot(self) -> LoadedSettings:
        return self._settings_manager.load()

    def set_model_ref(self, model_ref: str, *, require_credentials: bool = True) -> None:
        self._ensure_idle_for_session_switch()
        model = resolve_model(model_ref)
        old_thinking = self._thinking_level
        try:
            next_thinking = validate_thinking_level_for_model(model, old_thinking)
        except ValueError:
            next_thinking = "off"
        if _requires_credentials(model, require_credentials):
            resolve_api_key(model)
        with self._lock:
            self._model_ref = canonical_model_ref(model)
            self._model = model
            self._thinking_level = next_thinking
            if self._session_manager is not None and self._durable_session is not None:
                self._session_manager.append_model_change(
                    self._durable_session.id,
                    provider=model.provider,
                    model_id=model.id,
                )
                if next_thinking != old_thinking:
                    self._session_manager.append_thinking_level_change(
                        self._durable_session.id,
                        thinking_level=next_thinking,
                    )
                self._refresh_durable_session_locked()
                self._session_context_messages = self._load_session_context_messages()
            self._rebuild_agent_locked()

    def set_thinking_level(self, level: ThinkingLevel) -> None:
        self._ensure_idle_for_session_switch()
        thinking = validate_thinking_level_for_model(self._model, level)
        with self._lock:
            self._thinking_level = thinking
            if self._session_manager is not None and self._durable_session is not None:
                self._session_manager.append_thinking_level_change(
                    self._durable_session.id,
                    thinking_level=thinking,
                )
                self._refresh_durable_session_locked()
                self._session_context_messages = self._load_session_context_messages()
            self._rebuild_agent_locked()

    def set_cache_retention(self, retention: CacheRetention | None) -> None:
        self._ensure_idle_for_session_switch()
        with self._lock:
            self._cache_retention = retention
            self._rebuild_agent_locked()

    def cycle_model(self) -> str:
        self._ensure_idle_for_session_switch()
        enabled = self.settings_snapshot().settings.model.enabled_models
        candidates = (
            [_normalize_candidate(ref) for ref in enabled]
            if enabled
            else [canonical_model_ref(model) for model in list_models()]
        )
        ordered = _rotate_after(candidates, self._model_ref)
        failures: list[str] = []
        for candidate in ordered:
            try:
                model = resolve_model(candidate)
                self.set_model_ref(
                    canonical_model_ref(model),
                    require_credentials=_requires_credentials(model, True),
                )
                return self._model_ref
            except Exception as exc:
                failures.append(f"{candidate}: {exc}")
        detail = f"; {'; '.join(failures)}" if failures else ""
        raise RuntimeError(f"no configured model is available{detail}")

    def _load_session_context_messages(self) -> list[object]:
        return self._context_messages(self._load_session_context())

    def _load_session_context(self):
        if self._session_manager is None or self._durable_session is None:
            return None
        return self._session_manager.build_session_context(self._durable_session.id)

    def _context_messages(self, context) -> list[object]:
        if context is None:
            return []
        return convert_coding_messages_to_llm(list(context.messages))

    def _apply_session_control_state(self, context) -> None:
        if context is None:
            return
        if context.provider and context.model_id:
            # Sessions store the legacy ``provider/model_id`` pair; resolve via
            # that pair (always accepted) and write canonical ref to runtime
            # state so the TUI footer and downstream writebacks stay on the
            # canonical form.
            self._model = resolve_model(f"{context.provider}/{context.model_id}")
            self._model_ref = canonical_model_ref(self._model)
        if context.thinking_level:
            self._thinking_level = validate_thinking_level_for_model(
                self._model,
                context.thinking_level,
            )

    def _refresh_durable_session_locked(self) -> None:
        refreshed = self._session_manager.repository.get_session(self._durable_session.id)
        if refreshed is not None:
            self._durable_session = refreshed

    def _rebuild_agent_locked(self) -> None:
        self._generation += 1
        self._provider_cache_affinity_id = self._resolve_provider_cache_affinity_id()
        self._agent = self._build_agent(self._generation)
        self._active_future = None
        self._enqueue_queue_update_locked()


def _requires_credentials(model, requested: bool) -> bool:
    return requested and model.provider != "faux" and not model.api.startswith("extension:")


def _rotate_after(candidates: list[str], current: str) -> list[str]:
    if current not in candidates or len(candidates) <= 1:
        return candidates
    start = candidates.index(current) + 1
    return [*candidates[start:], *candidates[:start]]


def _normalize_candidate(ref: str) -> str:
    """Best-effort canonical form for ``enabled_models`` entries.

    Old settings may still list legacy two-segment refs; normalize them so
    rotation matches ``self._model_ref`` (canonical) without dropping refs
    that fail to parse — those will surface in failures during set_model_ref.
    """

    try:
        return canonical_model_ref(ref)
    except Exception:
        return ref


__all__ = ["ModelRuntimeMixin"]

"""``/hotkeys`` — show the default keymap as a SettingsList overlay."""

from __future__ import annotations

from tui.keymap import Action, default_bindings
from tui.overlay import SettingsList, SettingsRow

from .registry import SlashCommandContext

_DEFERRED_ACTIONS: frozenset[Action] = frozenset({Action.PASTE_IMAGE})


def _human_action(action_value: str) -> str:
    return action_value.replace("_", " ")


def handle_hotkeys(ctx: SlashCommandContext) -> None:
    rows = [
        SettingsRow(label=binding.key, value=_human_action(binding.action.value))
        for binding in default_bindings()
        if binding.action not in _DEFERRED_ACTIONS
    ]
    overlay = SettingsList("Hotkeys", rows)
    ctx.controller.open_overlay(overlay)


__all__ = ["handle_hotkeys"]

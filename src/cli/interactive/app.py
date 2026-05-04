"""``InteractiveController`` — agent-aware wrapper around generic ``TUIApp``.

Plan §W4 makes this the single owner of the event plane
(``dispatch_event``) and the control plane (``handle_abort`` /
``inject_user_input`` / ``simulate_resize`` / ``exit``). The playback
harness and (M3+) the real :class:`Agent` go through these methods only;
nobody else may touch :class:`EventRouter` / components / ``TUIApp``
internals.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tui.app import TUIApp
from tui.editor import Editor, EditorState, EditorSubmission
from tui.keymap import Action, Keymap
from tui.overlay import Confirm
from tui.stdin_buffer import KeyEvent, MouseWheelEvent

from .components import MessageListComponent, StatusComponent
from .event_router import EventRouter
from .extension_ui import InteractiveExtensionUIContext
from .runtime import InteractiveAgentRuntime
from .tool_renderer_registry import ToolRendererRegistry

# Lazy import: the slash registry lives in ``cli.slash_commands``; importing
# it eagerly would create a cycle (slash modules import from this file).

_ACTION_NOTICES = {
    Action.AT_TRIGGER: "@-mention autocomplete not implemented in M1; tracked in M5",
    Action.PASTE_IMAGE: "image paste deferred to M2/M5; placeholder only",
}


class InteractiveController:
    """Agent-aware façade. Composes status, message list, editor, router."""

    def __init__(
        self,
        *,
        tui_app: TUIApp,
        playback_dir: Path | None = None,
        runtime: InteractiveAgentRuntime | None = None,
        keymap: Keymap | None = None,
    ) -> None:
        self._app: TUIApp = tui_app
        self._playback_dir: Path | None = playback_dir
        self._runtime: InteractiveAgentRuntime | None = runtime

        keyboard_level = getattr(tui_app.terminal, "keyboard_protocol_level", 1)
        self._keymap: Keymap = keymap or Keymap(keyboard_protocol_level=keyboard_level)
        self._tool_registry = ToolRendererRegistry()
        self._messages = MessageListComponent()
        self._status = StatusComponent()
        self._router = EventRouter(
            message_list=self._messages,
            status=self._status,
            tool_registry=self._tool_registry,
        )
        self._editor = Editor(
            self._keymap,
            on_submit=self._on_editor_submit,
            on_action=self._on_editor_action,
            on_buffer_change=self._on_editor_buffer_change,
        )
        self._submit_handler: Callable[[EditorSubmission], None] | None = None
        self._action_handler: Callable[[Action], None] | None = None
        self._playback_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._playback_thread: threading.Thread | None = None
        self._exit_when_playback_finishes: bool = False
        self._slash_registry: Any | None = None
        self._slash_overlay: Any | None = None
        self._extension_command_names: set[str] = set()

        # Composite root: status on top, then message list, then editor.
        self._root = _RootComponent(self._status, self._messages, self._editor)
        if self._runtime is not None:
            self.set_runtime(self._runtime)

    # ------------------------------------------------------------------ #
    # Bootstrap                                                           #
    # ------------------------------------------------------------------ #

    def bootstrap(self) -> None:
        from cli.slash_commands import SlashCommandRegistry, register_builtin_commands
        from cli.slash_commands.extensions import register_extension_commands

        registry = SlashCommandRegistry()
        play_targets = _discover_play_targets()
        register_builtin_commands(registry, play_targets=play_targets)
        self._extension_command_names = register_extension_commands(registry, self)
        self._slash_registry = registry

        self._app.attach_root(self._root)
        self._app.set_focus(self._editor)
        self._app.set_focus_offset_provider(self._focus_offset_provider)
        self._status.attach_wake_scheduler(self._app.schedule_wake)
        self._app.add_input_hook(self._global_input_hook)
        self._editor.set_state(EditorState.IDLE)
        if self._playback_dir is not None:
            self._editor.set_footer(f"playback: {self._playback_dir.name}")
        elif self._runtime is not None:
            self._editor.set_footer(self._runtime.footer_summary)
        else:
            self._editor.set_footer("no runtime — pass --playback or use /play")

    def run(self) -> None:
        if self._playback_dir is not None:
            # Drive playback from a daemon thread so the main loop can keep
            # rendering frames between sleeps. The thread calls
            # ``controller.exit()`` once the fixture is exhausted (unless an
            # earlier ``inject: quit`` already did so).
            self._exit_when_playback_finishes = True
            started = self._start_playback_thread(self._playback_dir)
            if not started:
                # Fixture failed to load: skip the run loop entirely so
                # ``--playback`` against a bad path doesn't hang. (The
                # status notification was already pushed by the thread
                # starter; in practice we also write the error to stderr
                # so a non-TTY caller sees it.)
                import sys

                print(
                    f"neomagi --playback: failed to load fixture {self._playback_dir}",
                    file=sys.stderr,
                )
                return
        self._app.run()
        if self._playback_thread is not None and self._playback_thread.is_alive():
            # Belt-and-suspenders: a manual /quit while playback is in
            # flight should still wind down the harness cleanly.
            self._playback_thread.join(timeout=2.0)
        if self._runtime is not None:
            self._runtime.shutdown()

    # ------------------------------------------------------------------ #
    # Event plane                                                         #
    # ------------------------------------------------------------------ #

    def dispatch_event(self, event: Any) -> None:
        self._router.route(event)
        self._flush_command_transcript()
        self._app.request_render()

    # ------------------------------------------------------------------ #
    # Control plane                                                       #
    # ------------------------------------------------------------------ #

    def handle_abort(self) -> None:
        if self._runtime is not None and self._runtime.state.is_running:
            self._runtime.abort()
            self._editor.set_state(EditorState.ABORTING)
            self._status.push_notification("aborting…", level="info", ttl_seconds=3.0)
            self._app.request_render()
            return

        active = self._router.active_assistant
        if active is not None:
            active.mark_aborted()
        for tool in list(self._router.active_tools):
            tool.mark_aborted()
        # Intentionally do NOT clear the router's active assistant pointer:
        # any trailing `error`/`done` frame should fold into the same
        # component (preserving partial text) rather than lazy-creating a
        # second placeholder. The router itself clears active when the
        # terminal frame arrives.
        self._editor.set_state(EditorState.IDLE)
        # Use a transient status notification instead of pinning "aborted"
        # into the editor footer: the footer has no auto-revert, so a hard
        # set would leave the user staring at "aborted" forever even after
        # they kept typing or ran another command. Notifications already
        # carry a TTL (Status component fades them after a few seconds).
        self._status.push_notification("aborted", level="info", ttl_seconds=3.0)
        self._flush_command_transcript()
        self._app.request_render()

    def inject_user_input(self, text: str) -> None:
        self._editor.buffer.insert(text)
        self._app.request_render()

    def simulate_resize(self, cols: int, rows: int) -> None:
        self._app.simulate_resize(cols, rows)

    def exit(self) -> None:
        if self._runtime is not None:
            self._runtime.shutdown()
        if self._playback_task is not None and not self._playback_task.done():
            self._playback_task.cancel()
        self._app.exit()

    def open_overlay(self, overlay: Any, *, focus: bool = True) -> None:
        """Push an overlay onto the substrate.

        ``focus=True`` (default) shifts keyboard focus to the overlay — used
        for modal pickers like ``Confirm`` and the Tab-driven slash picker.
        ``focus=False`` opens a non-modal overlay (e.g. the live slash
        autocomplete strip): the overlay paints, but keystrokes keep going
        to whatever component held focus. On close, focus is restored to
        the editor only if the overlay was the focused one."""

        self._app.attach_overlay(overlay)
        focused_before = self._app.focused
        if focus:
            self._app.set_focus(overlay)
        original_close = getattr(overlay, "on_close", None)

        def _restore() -> None:
            if original_close is not None:
                original_close()
            self._app.detach_overlay(overlay)
            if focus and self._app.focused is overlay:
                self._app.set_focus(self._editor)
            elif not focus:
                # Non-modal overlays never moved focus; nothing to restore.
                # If a later interaction did move focus into the overlay
                # (e.g. Tab) and it's still there, return to the original
                # focus owner.
                if self._app.focused is overlay:
                    self._app.set_focus(focused_before or self._editor)

        overlay.on_close = _restore

    # ------------------------------------------------------------------ #
    # Public references for tests / harnesses                              #
    # ------------------------------------------------------------------ #

    @property
    def messages(self) -> MessageListComponent:
        return self._messages

    @property
    def status(self) -> StatusComponent:
        return self._status

    @property
    def editor(self) -> Editor:
        return self._editor

    @property
    def router(self) -> EventRouter:
        return self._router

    @property
    def tool_registry(self) -> ToolRendererRegistry:
        return self._tool_registry

    @property
    def runtime(self) -> InteractiveAgentRuntime | None:
        return self._runtime

    def set_submit_handler(self, handler: Callable[[EditorSubmission], None]) -> None:
        self._submit_handler = handler

    def set_action_handler(self, handler: Callable[[Action], None]) -> None:
        self._action_handler = handler

    def set_runtime(self, runtime: InteractiveAgentRuntime | None) -> None:
        self._runtime = runtime
        if runtime is None:
            self._submit_handler = None
            self._action_handler = None
            return
        runtime.set_event_wake(self._schedule_runtime_drain)
        runtime.bind_extension_ui_context(
            InteractiveExtensionUIContext(status=self._status, editor=self._editor)
        )
        self.set_submit_handler(self._handle_runtime_submit)
        self.set_action_handler(self._handle_runtime_action)

    def reset_session(self) -> None:
        aborted_previous = False
        if self._runtime is not None:
            aborted_previous = self._runtime.state.is_running
            self._runtime.reset()
        self._flush_command_transcript()
        self._messages.clear()
        self._router.clear_active()
        self._status.set_queue([], [])
        if self._app.render_mode == "command":
            self._app.commit_lines(
                ["", "--- [new session] previous runtime transcript cleared ---", ""]
            )
        if aborted_previous:
            self._status.push_notification(
                "new session; previous run aborted",
                level="info",
                ttl_seconds=4.0,
            )
        self._editor.set_state(EditorState.IDLE)
        if self._runtime is not None:
            self._editor.set_footer(self._runtime.footer_summary)
        else:
            self._editor.set_footer("new session (session manager arrives in M6)")
        self._app.request_render()

    def refresh_after_session_switch(
        self,
        boundary: str,
        *,
        summary: str | None = None,
        editor_prefill: str = "",
    ) -> None:
        self._flush_command_transcript()
        self._messages.clear()
        self._router.clear_active()
        self._status.set_queue([], [])
        if self._app.render_mode == "command":
            self._app.commit_lines(["", f"--- [{boundary}] ---", *([summary] if summary else []), ""])
        else:
            self._status.push_notification(summary or boundary, level="info", ttl_seconds=4.0)
        self._editor.buffer.text = editor_prefill
        self._editor.buffer.cursor = len(editor_prefill)
        self._editor._last_seen_text = ""  # noqa: SLF001 - controller owns editor callbacks
        self._editor._notify_buffer_change()  # noqa: SLF001
        self._editor.set_state(EditorState.IDLE)
        if self._runtime is not None:
            self._editor.set_footer(self._runtime.footer_summary)
        self._app.request_render()

    def push_session_message(self, message: str, *, level: str = "info") -> None:
        lines = message.splitlines() or [""]
        if self._app.render_mode == "command":
            self._app.commit_lines(lines)
            first = next((line for line in lines if line.strip()), lines[0])
            notification = lines[0] if len(lines) == 1 else f"{first} (+{len(lines) - 1} lines)"
        else:
            notification = message
        self._status.push_notification(notification, level=level, ttl_seconds=8.0)
        self._app.request_render()

    # ------------------------------------------------------------------ #
    # Internal hooks                                                      #
    # ------------------------------------------------------------------ #

    def _global_input_hook(self, event: Any) -> bool:
        if isinstance(event, MouseWheelEvent):
            if self._app.render_mode != "canvas":
                return False
            amount = 3 if event.direction == "up" else -3
            self._messages.scroll_lines(amount)
            return True
        if isinstance(event, KeyEvent):
            if event.key == "Ctrl+C":
                # Pi convention: Ctrl+C aborts when there is something to
                # abort, otherwise exits the TUI. In raw mode the kernel
                # never sees SIGINT, so this hook is the single source of
                # truth for both behaviors.
                if self._has_active_work():
                    self.handle_abort()
                else:
                    self.exit()
                return True
            if event.key == "Esc Esc":
                # Default doubleEscapeAction = "tree" (behavior matrix § F.1).
                self._dispatch_tree_shortcut()
                return True
        return False

    def _dispatch_tree_shortcut(self) -> None:
        from cli.slash_commands.tree import _tree_lines

        runtime = self._runtime
        if runtime is None:
            self._status.push_notification(
                "tree navigation requires an interactive runtime",
                level="warn",
            )
            return
        tree = runtime.session_tree()
        if not tree:
            self._status.push_notification("current session has no entries", level="info")
            return
        self.push_session_message("\n".join(_tree_lines(tree, stats=runtime.session_stats())[:12]))

    def _has_active_work(self) -> bool:
        return (
            self._router.active_assistant is not None
            or bool(self._router.active_tools)
            or self._editor.state != EditorState.IDLE
        )

    def _focus_offset_provider(self, focused: Any, width: int) -> int | None:
        """Translate a focus on the editor (nested inside ``_RootComponent``)
        into the row offset substrate's cursor placement needs.

        Reads the root's cached ``_last_visible_msg_rows`` from the most
        recent ``render_with_height`` call, so the cursor lands on the
        editor's actual on-screen row even when the message column was
        clipped to fit the terminal height (manual §4.9)."""

        if focused is self._editor:
            return self._root.editor_offset(width)
        return None

    def _on_editor_submit(self, submission: EditorSubmission) -> None:
        # Submit ALWAYS closes the live autocomplete strip (regardless of
        # whether dispatch matches a command).
        self._close_slash_overlay()
        text = submission.text.strip()
        if text.startswith("/") and self._slash_registry is not None:
            name = text[1:].split(maxsplit=1)[0] if text[1:].strip() else ""
            if name and self._slash_registry.get(name) is not None:
                self._slash_registry.parse_and_dispatch(text, self)
                return
            expanded = self._runtime.expand_resource_command(text) if self._runtime is not None else None
            if expanded is not None:
                text = expanded
            else:
                handled = self._slash_registry.parse_and_dispatch(text, self)
                if handled:
                    return
        if self._submit_handler is not None:
            self._submit_handler(
                EditorSubmission(text=text, state_at_submit=submission.state_at_submit)
            )
            return
        self._status.push_notification(
            "M1 mock — no agent runtime; pass --playback or use /play",
            level="warn",
        )

    def _on_editor_action(self, action: Action) -> None:
        if action == Action.ABORT:
            # Esc closes an open autocomplete overlay first; only when none
            # is open does it fall through to the abort path.
            if self._slash_overlay is not None:
                self._close_slash_overlay()
                return
            self.handle_abort()
            return
        if action == Action.QUIT:
            self._open_quit_confirm()
            return
        if action == Action.SLASH_TRIGGER:
            # The editor already inserted '/'; open the live (non-focused)
            # autocomplete strip so the user can keep typing.
            self._ensure_slash_overlay(self._editor.buffer.text)
            return
        if action == Action.AUTOCOMPLETE:
            self._focus_slash_autocomplete()
            return
        if self._handle_scroll_action(action):
            return
        if action in _ACTION_NOTICES:
            self._status.push_notification(_ACTION_NOTICES[action], level="info")
            return
        if self._action_handler is not None:
            self._action_handler(action)

    def _handle_scroll_action(self, action: Action) -> bool:
        if self._app.render_mode == "command" and action in {
            Action.SCROLL_PAGE_UP,
            Action.SCROLL_PAGE_DOWN,
        }:
            return True
        if action == Action.SCROLL_PAGE_UP:
            self._messages.scroll_page_up()
            return True
        if action == Action.SCROLL_PAGE_DOWN:
            self._messages.scroll_page_down()
            return True
        return False

    def _focus_slash_autocomplete(self) -> None:
        # Tab — if a slash overlay is open, move focus into it so the user
        # can pick with arrows + Enter; if not, open one for the current
        # buffer (covers Tab on plain text starting with '/').
        text = self._editor.buffer.text
        if not text.startswith("/"):
            return
        self._ensure_slash_overlay(text)
        if self._slash_overlay is not None:
            self._app.set_focus(self._slash_overlay)
            self._app.request_render()

    def _handle_runtime_submit(self, submission: EditorSubmission) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        text = submission.text
        if not text.strip():
            return
        if submission.state_at_submit == EditorState.ABORTING:
            self._status.push_notification(
                "waiting for abort to finish",
                level="info",
                ttl_seconds=3.0,
            )
            self._editor.set_state(EditorState.ABORTING)
            self._app.request_render()
            return
        try:
            user_bash = _parse_user_bash(text)
            if user_bash is not None:
                if submission.state_at_submit == EditorState.STREAMING:
                    raise RuntimeError("user bash is only available while idle")
                command, exclude_from_context = user_bash
                runtime.run_user_bash(command, exclude_from_context=exclude_from_context)
            elif submission.state_at_submit == EditorState.STREAMING:
                runtime.steer(text)
            else:
                runtime.submit(text)
            self._messages.scroll_to_bottom()
            self._editor.set_state(EditorState.STREAMING)
            self._editor.set_footer(runtime.footer_summary)
        except RuntimeError as exc:
            self._editor.set_state(EditorState.IDLE)
            self._status.push_notification(str(exc), level="error", ttl_seconds=8.0)
        self._app.request_render()

    def _handle_runtime_action(self, action: Action) -> None:
        if action != Action.QUEUE_FOLLOWUP or self._runtime is None:
            return
        if self._editor.state == EditorState.ABORTING:
            self._status.push_notification(
                "waiting for abort to finish",
                level="info",
                ttl_seconds=3.0,
            )
            self._app.request_render()
            return
        text = self._editor.buffer.take()
        if not text.strip():
            return
        if self._editor.state == EditorState.STREAMING:
            self._runtime.follow_up(text)
        else:
            self._runtime.submit(text)
        self._messages.scroll_to_bottom()
        self._editor.set_state(EditorState.STREAMING)
        self._editor.set_footer(self._runtime.footer_summary)
        self._app.request_render()

    def _schedule_runtime_drain(self) -> None:
        self._app.schedule_callback(time.monotonic(), self._drain_runtime_events)

    def _drain_runtime_events(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        for event in runtime.drain_events():
            self.dispatch_event(event)
            if getattr(event, "type", None) == "agent_end" or (
                getattr(event, "type", None) == "message_end"
                and not runtime.state.is_running
            ):
                self._editor.set_state(EditorState.IDLE)
                self._status.set_queue([], [])
                self._editor.set_footer(runtime.footer_summary)

    def _flush_command_transcript(self) -> None:
        if self._app.render_mode != "command":
            return
        rows = self._messages.commit_ready_rows(max(20, self._app.cols))
        if rows:
            self._app.commit_lines(rows)

    def _on_editor_buffer_change(self, text: str) -> None:
        """Live filter: open / refresh / close the autocomplete overlay as
        the editor's buffer changes — but never move focus. The user keeps
        typing in the editor; Tab is the explicit "enter the picker" key."""

        if text.startswith("/"):
            self._ensure_slash_overlay(text)
            return
        # Buffer no longer starts with '/': any open autocomplete is stale.
        self._close_slash_overlay()

    def _ensure_slash_overlay(self, query: str) -> None:
        from tui.autocomplete import slash_completions
        from tui.overlay import Selector, SelectorItem

        if self._slash_registry is None:
            return
        candidates = slash_completions(
            query or "/", self._slash_registry.autocomplete_items()
        )
        if not candidates:
            self._close_slash_overlay()
            return
        items = [
            SelectorItem(label=c.label, detail=c.detail, value=c.label)
            for c in candidates
        ]
        if self._slash_overlay is None:
            selector = Selector(
                "Slash commands  (Tab to focus, Enter to insert, Esc to cancel)",
                items,
                on_select=self._insert_completion,
            )
            self._slash_overlay = selector
            # Non-focused: editor keeps receiving keystrokes.
            self.open_overlay(selector, focus=False)

            # Augment the close hook so we also drop our local reference.
            original_close = selector.on_close

            def _on_close() -> None:
                if original_close is not None:
                    original_close()
                self._slash_overlay = None

            selector.on_close = _on_close
        else:
            # Refresh the existing selector's filtered items in place.
            self._slash_overlay.items = items
            self._slash_overlay.index = min(
                self._slash_overlay.index, max(0, len(items) - 1)
            )
            self._slash_overlay.request_render()

    def _close_slash_overlay(self) -> None:
        overlay = self._slash_overlay
        if overlay is None:
            return
        overlay.close()  # this triggers our augmented on_close → clears the ref

    def _insert_completion(self, item: Any) -> None:
        # Replace the current slash token with the chosen command.
        text = self._editor.buffer.text
        if text.startswith("/"):
            self._editor.buffer.text = ""
            self._editor.buffer.cursor = 0
            self._editor._last_seen_text = ""
        self._editor.buffer.insert(item.value + " ")
        self._editor._notify_buffer_change()
        self._app.set_focus(self._editor)
        self._app.request_render()

    def _open_quit_confirm(self) -> None:
        confirm = Confirm(
            "Quit NeoMAGI?",
            on_choose=lambda yes: self.exit() if yes else None,
        )
        self.open_overlay(confirm)

    def _start_playback(self, fixture: Path) -> None:
        """Synchronous playback entry — used by ``/play`` from inside the
        TUI loop. Each ``dispatch_event`` requests a render which the
        loop will drain on its next tick."""

        from .playback import PlaybackHarness

        try:
            harness = PlaybackHarness(fixture, controller=self)
            harness.play_sync()
        except Exception as exc:  # surface but don't crash the TUI
            self._status.push_notification(
                f"playback failed: {exc}", level="error", ttl_seconds=10.0
            )

    def _start_playback_thread(self, fixture: Path) -> bool:
        """Background driver used by ``--playback``. Returns ``True`` if the
        thread was started, ``False`` if the fixture failed to load (caller
        is responsible for skipping the run loop in that case — calling
        ``self._app.exit()`` here is racy because ``_app.run()`` resets
        ``_running = True`` on entry)."""

        from .playback import PlaybackHarness

        try:
            harness = PlaybackHarness(fixture, controller=self)
        except Exception as exc:
            self._status.push_notification(
                f"playback failed to load: {exc}", level="error", ttl_seconds=10.0
            )
            return False

        def _runner() -> None:
            try:
                harness.play_sync(sleep=True)
            except Exception as exc:
                self._status.push_notification(
                    f"playback failed: {exc}", level="error", ttl_seconds=10.0
                )
            finally:
                if self._exit_when_playback_finishes:
                    # Brief tail so the final frame stays on screen long
                    # enough to be readable before the alt screen tears
                    # down.
                    import time

                    time.sleep(0.3)
                    self._app.exit()

        thread = threading.Thread(
            target=_runner, name="neomagi-playback", daemon=True
        )
        self._playback_thread = thread
        thread.start()
        return True


class _RootComponent:
    """Composite root for status, messages, editor, and notifications."""

    def __init__(
        self,
        status: StatusComponent,
        messages: MessageListComponent,
        editor: Editor,
    ) -> None:
        self._status = status
        self._messages = messages
        self._editor = editor
        self._last_visible_msg_rows: int = 0
        self._last_editor_offset: int = 0

    def render(self, width: int) -> list[str]:
        rows: list[str] = []
        rows.extend(self._status.render(width))
        msg_rows = self._messages.render(width)
        self._last_visible_msg_rows = len(msg_rows)
        self._last_editor_offset = len(rows) + len(msg_rows)
        rows.extend(msg_rows)
        rows.extend(self._editor.render(width))
        return rows

    def render_with_height(self, width: int, height: int) -> list[str]:
        status_rows = self._status.render_status(width)
        notification_rows = self._status.render_notifications(width)
        editor_rows = self._editor.render(width)
        budget = max(
            0,
            height - len(status_rows) - len(editor_rows) - len(notification_rows),
        )
        msg_rows = self._messages.render_tail(width, budget)
        self._last_visible_msg_rows = len(msg_rows)
        self._last_editor_offset = len(status_rows) + len(msg_rows)
        return status_rows + msg_rows + editor_rows + notification_rows

    def render_command_with_height(self, width: int, height: int) -> list[str]:
        status_rows = self._status.render_status(width)
        notification_rows = self._status.render_notifications(width)
        editor_rows = self._editor.render(width)
        budget = max(
            0,
            height - len(status_rows) - len(editor_rows) - len(notification_rows),
        )
        msg_rows = self._messages.render_uncommitted_tail(width, budget)
        self._last_visible_msg_rows = len(msg_rows)
        self._last_editor_offset = len(status_rows) + len(msg_rows)
        return status_rows + msg_rows + editor_rows + notification_rows

    def editor_offset(self, width: int) -> int:
        return self._last_editor_offset

    def attach(self, request_render: Callable[[], None]) -> None:
        self._status.attach(request_render)
        self._messages.attach(request_render)
        self._editor.attach(request_render)

    def detach(self) -> None:
        self._status.detach()
        self._messages.detach()
        self._editor.detach()

    def handle_input(self, event: Any) -> None:  # pragma: no cover — focus goes to editor
        return None

    def cursor_marker(self):  # pragma: no cover — focus goes to editor
        return None


def _discover_play_targets() -> list[str]:
    """Best-effort scan of ``tests/fixtures/pi_compat/`` for fixtures that
    ship an ``events.jsonl`` (the only ones ``/play`` can replay).

    M1 lookup is relative to the current working directory; M6 will move
    the registry into ``cli.core.session_manager`` and treat it as
    config-driven."""

    root = Path("tests/fixtures/pi_compat")
    if not root.is_dir():
        return []
    return sorted(
        sub.name
        for sub in root.iterdir()
        if sub.is_dir() and (sub / "events.jsonl").is_file()
    )


def _parse_user_bash(text: str) -> tuple[str, bool] | None:
    if text.startswith("!!"):
        command = text[2:].strip()
        return (command, True) if command else None
    if text.startswith("!"):
        command = text[1:].strip()
        return (command, False) if command else None
    return None


__all__ = ["InteractiveController"]

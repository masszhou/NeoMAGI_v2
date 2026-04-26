"""Stdin parser (ADR-0015 §影响 `src/tui/stdin_buffer.py`).

Turns the raw byte stream coming back from a raw-mode terminal into typed
events:

- :class:`KeyEvent` — single keystroke (regular, function, modified)
- :class:`PasteEvent` — bracketed paste payload between ``ESC[200~`` and
  ``ESC[201~`` markers
- :class:`ResizeEvent` — fed in by ``TerminalSession`` SIGWINCH handler;
  not parsed from stdin but re-exported here so the upstream keymap sees
  one unified event type.
- :class:`MouseEvent` — placeholder type for M2+; M1 does not consume.

These are *substrate-internal* dataclasses, **not** Pi-compatible protocol
models. They never persist, never enter ``events.jsonl``, never escape the
``tui`` / ``cli.interactive`` boundary as a wire payload (per plan
acceptance §9 exception clause).
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field

# Bracketed-paste markers (DEC).
PASTE_BEGIN = "\x1b[200~"
PASTE_END = "\x1b[201~"


@dataclass(frozen=True)
class KeyEvent:
    key: str
    """Logical key name. Examples: ``"a"``, ``"Enter"``, ``"Shift+Enter"``,
    ``"Alt+Enter"``, ``"Esc"``, ``"Ctrl+C"``, ``"Up"``, ``"PageDown"``,
    ``"F1"``."""

    raw: str = ""
    """Original byte sequence. Useful for debugging only."""

    modifiers: frozenset[str] = field(default_factory=frozenset)
    """Set of modifier names (``"Shift"`` / ``"Ctrl"`` / ``"Alt"``)."""


@dataclass(frozen=True)
class PasteEvent:
    text: str


@dataclass(frozen=True)
class ResizeEvent:
    cols: int
    rows: int


@dataclass(frozen=True)
class MouseEvent:
    """Placeholder for M2+. Not emitted in M1."""

    button: int
    col: int
    row: int
    pressed: bool


Event = KeyEvent | PasteEvent | ResizeEvent | MouseEvent


# Mapping from bare CSI / SS3 final-byte sequences to logical key names.
_CSI_NAMES: dict[str, str] = {
    "A": "Up",
    "B": "Down",
    "C": "Right",
    "D": "Left",
    "F": "End",
    "H": "Home",
    "Z": "Shift+Tab",
}
_TILDE_NAMES: dict[str, str] = {
    "1": "Home",
    "2": "Insert",
    "3": "Delete",
    "4": "End",
    "5": "PageUp",
    "6": "PageDown",
    "7": "Home",
    "8": "End",
    "11": "F1",
    "12": "F2",
    "13": "F3",
    "14": "F4",
    "15": "F5",
    "17": "F6",
    "18": "F7",
    "19": "F8",
    "20": "F9",
    "21": "F10",
    "23": "F11",
    "24": "F12",
}
_SS3_NAMES: dict[str, str] = {
    "P": "F1",
    "Q": "F2",
    "R": "F3",
    "S": "F4",
}

# xterm modifier param: 1 = none, 2 = Shift, 3 = Alt, 4 = Shift+Alt,
# 5 = Ctrl, 6 = Shift+Ctrl, 7 = Alt+Ctrl, 8 = Shift+Alt+Ctrl.
_XTERM_MOD_TABLE: dict[int, frozenset[str]] = {
    1: frozenset(),
    2: frozenset({"Shift"}),
    3: frozenset({"Alt"}),
    4: frozenset({"Shift", "Alt"}),
    5: frozenset({"Ctrl"}),
    6: frozenset({"Shift", "Ctrl"}),
    7: frozenset({"Alt", "Ctrl"}),
    8: frozenset({"Shift", "Alt", "Ctrl"}),
}


def _format_key(name: str, modifiers: frozenset[str]) -> str:
    """Build a human-readable key string like ``"Shift+Enter"``."""

    if not modifiers:
        return name
    parts: list[str] = []
    for mod in ("Ctrl", "Alt", "Shift"):
        if mod in modifiers:
            parts.append(mod)
    parts.append(name)
    return "+".join(parts)


class StdinBuffer:
    """Incremental stdin parser.

    Feed bytes via :meth:`feed`; drain typed events via :meth:`drain`.
    Handles partial ESC / CSI / OSC / APC across multiple reads, bracketed
    paste envelopes, and (best-effort) ``modifyOtherKeys`` / Kitty keyboard
    protocol level-1 sequences.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buffer: str = ""
        self._paste_active: bool = False
        self._paste_acc: list[str] = []
        self._lone_esc_pending: bool = False
        """Set on the drain that first sees buffer == "\\x1b" with no follow-up
        bytes. Cleared as soon as more bytes arrive. If the next drain still
        sees only ``\\x1b``, we emit a synthetic Esc — that gives the terminal
        ~one event-loop tick to deliver the rest of a multi-byte escape
        sequence before we conclude it was a lone keystroke."""

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def feed(self, chunk: bytes) -> None:
        """Append raw bytes; events become available via :meth:`drain`."""

        if not chunk:
            return
        self._buffer += self._decoder.decode(chunk)
        self._lone_esc_pending = False

    def feed_str(self, text: str) -> None:
        """Test-friendly variant that takes already-decoded text."""

        if not text:
            return
        self._buffer += text
        self._lone_esc_pending = False

    def drain(self) -> list[Event]:
        """Return all complete events parsed so far. Partial sequences stay
        in the internal buffer until the next :meth:`feed`."""

        events: list[Event] = []
        i = 0
        buf = self._buffer
        while i < len(buf):
            if self._paste_active:
                end_idx = buf.find(PASTE_END, i)
                if end_idx == -1:
                    self._paste_acc.append(buf[i:])
                    i = len(buf)
                    break
                self._paste_acc.append(buf[i:end_idx])
                events.append(PasteEvent("".join(self._paste_acc)))
                self._paste_acc = []
                self._paste_active = False
                i = end_idx + len(PASTE_END)
                continue

            ch = buf[i]
            if ch == "\x1b":
                parsed = self._parse_escape(buf, i)
                if parsed is None:
                    # Incomplete escape — stop and wait for more input.
                    break
                event, i = parsed
                if event is not None:
                    events.append(event)
                continue

            event, i = self._parse_plain_key(buf, i)
            if event is not None:
                events.append(event)

        self._buffer = buf[i:]

        # Lone-ESC flush: if the buffer still holds nothing but ``\x1b`` and
        # we already saw it on the previous drain (no new bytes arrived
        # in between — :meth:`feed` would have cleared the flag), emit the
        # synthetic Esc keystroke. That covers users pressing Esc once
        # without any follow-up sequence — otherwise we would buffer the
        # ESC forever and the editor's abort path would be unreachable.
        if self._buffer == "\x1b":
            if self._lone_esc_pending:
                events.append(KeyEvent("Esc", raw="\x1b"))
                self._buffer = ""
                self._lone_esc_pending = False
            else:
                self._lone_esc_pending = True
        else:
            self._lone_esc_pending = False
        return events

    # ------------------------------------------------------------------ #
    # Escape parsing                                                      #
    # ------------------------------------------------------------------ #

    def _parse_escape(self, buf: str, start: int) -> tuple[KeyEvent | None, int] | None:
        # Look at the byte after ESC.
        if start + 1 >= len(buf):
            return None

        second = buf[start + 1]

        if second == "[":
            return self._parse_csi(buf, start)
        if second == "O":
            return self._parse_ss3(buf, start)
        if second == "]":
            # OSC: terminated by BEL (\x07) or ST (ESC \).
            return self._parse_osc(buf, start)
        if second == "_" or second == "P":
            # APC / DCS: terminated by ST (ESC \).
            return self._parse_st_terminated(buf, start)
        if second == "\x1b":
            # Double ESC — surface it as a synthetic key so keymap can
            # implement the "double Esc" gesture without re-buffering.
            return KeyEvent("Esc Esc", raw="\x1b\x1b"), start + 2

        # ESC + printable → Alt+<char>
        if second.isprintable() or second == "\r" or second == "\n":
            key = self._name_for_char(second)
            return (
                KeyEvent(
                    _format_key(key, frozenset({"Alt"})),
                    raw=buf[start : start + 2],
                    modifiers=frozenset({"Alt"}),
                ),
                start + 2,
            )

        # Lone ESC — only emit once we are sure no follow-up is coming. We
        # need at least one more byte to disambiguate, which means the loop
        # caller has to leave the lone ESC pending until either: (a) another
        # byte arrives, or (b) a flush is forced. For interactive use we
        # return None to wait; callers force a flush by feeding any byte.
        return KeyEvent("Esc", raw="\x1b"), start + 1

    def _parse_csi(self, buf: str, start: int) -> tuple[KeyEvent | None, int] | None:
        # CSI is ESC [ <params> <intermediate> <final>. Final byte is in
        # 0x40..0x7e. Parameters are digits / ';'. Intermediate is 0x20..0x2f.
        end = start + 2
        while end < len(buf):
            ch = buf[end]
            if "@" <= ch <= "~":
                seq = buf[start : end + 1]
                # Bracketed paste markers are CSI 200~ / 201~ — handle here so
                # the outer loop sees a clean PasteEvent on next drain.
                if seq == PASTE_BEGIN:
                    self._paste_active = True
                    self._paste_acc = []
                    return None, end + 1
                if seq == PASTE_END:
                    # Stray end marker without a begin — drop it.
                    return None, end + 1
                event = self._csi_to_event(buf[start + 2 : end], ch, seq)
                return event, end + 1
            end += 1
        # Truncated CSI — wait for more input.
        return None

    def _csi_to_event(self, params: str, final: str, raw: str) -> KeyEvent | None:
        modifiers: frozenset[str] = frozenset()
        name: str | None = None

        if final == "u":
            # CSI <code> ; <mod> u  — Kitty / xterm modifyOtherKeys=2.
            return self._parse_csi_u(params, raw)

        if final == "~":
            # CSI <code> ; <mod> ~  — function / nav keys.
            parts = params.split(";")
            code = parts[0] if parts else ""
            mod_param = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            name = _TILDE_NAMES.get(code)
            if name is None:
                return None
            modifiers = _XTERM_MOD_TABLE.get(mod_param, frozenset())
            return KeyEvent(_format_key(name, modifiers), raw=raw, modifiers=modifiers)

        if final in _CSI_NAMES:
            name = _CSI_NAMES[final]
            # Form: CSI 1 ; <mod> <final>
            parts = params.split(";") if params else []
            mod_param = (
                int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            )
            modifiers = _XTERM_MOD_TABLE.get(mod_param, frozenset())
            return KeyEvent(_format_key(name, modifiers), raw=raw, modifiers=modifiers)

        # Unknown CSI — drop silently rather than corrupt the stream.
        return None

    def _parse_csi_u(self, params: str, raw: str) -> KeyEvent | None:
        parts = params.split(";")
        if not parts or not parts[0].isdigit():
            return None
        code = int(parts[0])
        mod_param = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        modifiers = _XTERM_MOD_TABLE.get(mod_param, frozenset())
        # 13 = Enter; 27 = Esc; 9 = Tab; 8/127 = Backspace; 32 = Space.
        name_map: dict[int, str] = {
            8: "Backspace",
            9: "Tab",
            13: "Enter",
            27: "Esc",
            32: "Space",
            127: "Backspace",
        }
        name = name_map.get(code)
        if name is None and 32 < code < 127:
            ch = chr(code)
            name = ch
        if name is None:
            return None
        return KeyEvent(_format_key(name, modifiers), raw=raw, modifiers=modifiers)

    def _parse_ss3(self, buf: str, start: int) -> tuple[KeyEvent | None, int] | None:
        # SS3 form: ESC O <final>, optionally with modifier param.
        if start + 2 >= len(buf):
            return None
        third = buf[start + 2]
        if third in _SS3_NAMES:
            return (
                KeyEvent(_SS3_NAMES[third], raw=buf[start : start + 3]),
                start + 3,
            )
        return None, start + 3

    def _parse_osc(self, buf: str, start: int) -> tuple[KeyEvent | None, int] | None:
        # Terminated by BEL or ESC \.
        end = start + 2
        while end < len(buf):
            ch = buf[end]
            if ch == "\x07":
                return None, end + 1
            if ch == "\x1b" and end + 1 < len(buf) and buf[end + 1] == "\\":
                return None, end + 2
            end += 1
        return None

    def _parse_st_terminated(self, buf: str, start: int) -> tuple[KeyEvent | None, int] | None:
        end = start + 2
        while end < len(buf):
            if buf[end] == "\x1b" and end + 1 < len(buf) and buf[end + 1] == "\\":
                return None, end + 2
            end += 1
        return None

    # ------------------------------------------------------------------ #
    # Plain key parsing                                                   #
    # ------------------------------------------------------------------ #

    def _parse_plain_key(self, buf: str, i: int) -> tuple[KeyEvent | None, int]:
        ch = buf[i]
        if ch == "\r" or ch == "\n":
            return KeyEvent("Enter", raw=ch), i + 1
        if ch == "\t":
            return KeyEvent("Tab", raw=ch), i + 1
        if ch == "\x7f" or ch == "\x08":
            return KeyEvent("Backspace", raw=ch), i + 1
        if ch == " ":
            return KeyEvent(" ", raw=ch), i + 1
        if ch < " ":
            # Control character — map to Ctrl+<letter>.
            ctrl_letter = chr(ord("A") + ord(ch) - 1)
            return (
                KeyEvent(
                    _format_key(ctrl_letter, frozenset({"Ctrl"})),
                    raw=ch,
                    modifiers=frozenset({"Ctrl"}),
                ),
                i + 1,
            )
        return KeyEvent(ch, raw=ch), i + 1

    @staticmethod
    def _name_for_char(ch: str) -> str:
        if ch == "\r" or ch == "\n":
            return "Enter"
        if ch == "\t":
            return "Tab"
        return ch


__all__ = [
    "Event",
    "KeyEvent",
    "MouseEvent",
    "PASTE_BEGIN",
    "PASTE_END",
    "PasteEvent",
    "ResizeEvent",
    "StdinBuffer",
]

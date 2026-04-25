"""Context-overflow pattern set + detector.

Direct port of `packages/ai/src/utils/overflow.ts` (pi-mono `97a38bf6`).
Each ``OVERFLOW_PATTERNS`` entry is annotated with the provider that emits the
matched error message; sample texts come from inline comments in the upstream
file. ``NON_OVERFLOW_PATTERNS`` exclude transient throttling errors that would
otherwise hit the generic fallbacks.

Two detection paths (architecture §Overflow and context-window contract):

1. **Error-based**: ``stopReason == "error"`` + ``errorMessage`` matches an
   overflow pattern and *does not* match any non-overflow pattern.
2. **Silent**: ``stopReason == "stop"`` and ``usage.input + usage.cacheRead >
   model.contextWindow`` (z.ai style).
"""

from __future__ import annotations

import re

from .types import AssistantMessage

# --------------------------------------------------------------------------- #
# Pattern sets (preserve the upstream order; tests below pin sample messages)  #
# --------------------------------------------------------------------------- #

OVERFLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"prompt is too long", re.IGNORECASE),  # Anthropic token overflow
    re.compile(r"request_too_large", re.IGNORECASE),  # Anthropic HTTP 413
    re.compile(r"input is too long for requested model", re.IGNORECASE),  # Amazon Bedrock
    re.compile(r"exceeds the context window", re.IGNORECASE),  # OpenAI Completions + Responses
    re.compile(r"input token count.*exceeds the maximum", re.IGNORECASE),  # Google Gemini
    re.compile(r"maximum prompt length is \d+", re.IGNORECASE),  # xAI Grok
    re.compile(r"reduce the length of the messages", re.IGNORECASE),  # Groq
    re.compile(r"maximum context length is \d+ tokens", re.IGNORECASE),  # OpenRouter
    re.compile(r"exceeds the limit of \d+", re.IGNORECASE),  # GitHub Copilot
    re.compile(r"exceeds the available context size", re.IGNORECASE),  # llama.cpp server
    re.compile(r"greater than the context length", re.IGNORECASE),  # LM Studio
    re.compile(r"context window exceeds limit", re.IGNORECASE),  # MiniMax
    re.compile(r"exceeded model token limit", re.IGNORECASE),  # Kimi For Coding
    re.compile(r"too large for model with \d+ maximum context length", re.IGNORECASE),  # Mistral
    re.compile(r"model_context_window_exceeded", re.IGNORECASE),  # z.ai non-standard
    re.compile(r"prompt too long; exceeded (?:max )?context length", re.IGNORECASE),  # Ollama
    re.compile(r"context[_ ]length[_ ]exceeded", re.IGNORECASE),  # generic fallback
    re.compile(r"too many tokens", re.IGNORECASE),  # generic fallback
    re.compile(r"token limit exceeded", re.IGNORECASE),  # generic fallback
    re.compile(r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)", re.IGNORECASE),  # Cerebras
)
"""20 patterns mirroring upstream — counted as 19 + 1 generic Cerebras row in
the W3 plan; the generic ``context[_ ]length[_ ]exceeded`` / ``too many tokens``
/ ``token limit exceeded`` triple is the catch-all fallback row."""

NON_OVERFLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(Throttling error|Service unavailable):", re.IGNORECASE),  # Bedrock formatter
    re.compile(r"rate limit", re.IGNORECASE),  # generic rate limiting
    re.compile(r"too many requests", re.IGNORECASE),  # HTTP 429
)


def is_context_overflow(message: AssistantMessage, context_window: int | None = None) -> bool:
    """Return ``True`` iff the assistant message represents a context overflow.

    Same two-path logic as upstream. ``context_window`` is required to detect
    silent overflow (z.ai style); pass ``None`` to skip the silent branch.
    """

    error_message = message.error_message
    if message.stop_reason == "error" and error_message:
        is_non_overflow = any(p.search(error_message) for p in NON_OVERFLOW_PATTERNS)
        if not is_non_overflow and any(p.search(error_message) for p in OVERFLOW_PATTERNS):
            return True

    if context_window is not None and message.stop_reason == "stop":
        input_tokens = message.usage.input + message.usage.cache_read
        if input_tokens > context_window:
            return True

    return False


def get_overflow_patterns() -> tuple[re.Pattern[str], ...]:
    """Snapshot of OVERFLOW_PATTERNS for tests."""

    return OVERFLOW_PATTERNS


__all__ = [
    "NON_OVERFLOW_PATTERNS",
    "OVERFLOW_PATTERNS",
    "get_overflow_patterns",
    "is_context_overflow",
]

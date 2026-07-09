"""Source-aware in-memory model registry keyed by provider and model id."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import supports_xhigh
from .oauth_github_copilot import COPILOT_HEADERS, GITHUB_COPILOT_INDIVIDUAL_BASE_URL
from .types import Model, ModelCost, ThinkingLevel

BUILTIN_SOURCE = "builtin"
SETTINGS_SOURCE = "settings"
EXTENSION_SOURCE = "extension"
RUNTIME_SOURCE = "runtime"

SOURCE_PRIORITIES: dict[str, int] = {
    BUILTIN_SOURCE: 0,
    f"{SETTINGS_SOURCE}:global": 10,
    f"{SETTINGS_SOURCE}:project": 20,
    EXTENSION_SOURCE: 30,
    RUNTIME_SOURCE: 40,
}

# --- Three-segment model ref: vendor / auth-channel / model-id ---------------
#
# The user-visible ref encodes (vendor, auth-channel) explicitly so OpenAI
# API key lanes and ChatGPT/Codex OAuth lanes are visually distinct. The
# adapter API family stays in ``model.api`` (e.g. ``openai-responses``,
# ``openai-codex-responses``, ``anthropic-messages``).

ALLOWED_AUTH_CHANNELS: frozenset[str] = frozenset({"api", "oauth", "local"})
DEFAULT_AUTH_CHANNEL = "api"

# Map internal provider id -> (vendor, auth-channel). The provider id remains
# the credential boundary used by ``resolve_api_key`` and auth storage; the
# vendor/channel pair is the user-visible decoration.
_BUILTIN_PROVIDER_AUTH: dict[str, tuple[str, str]] = {
    "openai": ("openai", "api"),
    "openai-codex": ("openai", "oauth"),
    "anthropic": ("anthropic", "api"),
    "opencode": ("opencode", "api"),
    "github-copilot": ("github-copilot", "oauth"),
    "faux": ("faux", "local"),
}

# Reverse map for parsing canonical refs back to an internal provider.
_VENDOR_CHANNEL_TO_PROVIDER: dict[tuple[str, str], str] = {
    info: provider for provider, info in _BUILTIN_PROVIDER_AUTH.items()
}

# Names that, when seen in the first segment, force three-segment parsing so
# malformed canonical refs (``openai/keychain/...``) fail with a precise
# auth-channel diagnostic instead of degrading to a generic ``unknown model``.
_RESERVED_FIRST_SEGMENTS: frozenset[str] = frozenset(
    {vendor for vendor, _channel in _BUILTIN_PROVIDER_AUTH.values()}
    | set(_BUILTIN_PROVIDER_AUTH)
)


@dataclass(frozen=True, slots=True)
class ModelRef:
    """Parsed three-segment user-visible model reference."""

    vendor: str
    auth_channel: str
    model_id: str
    provider: str

    @property
    def canonical(self) -> str:
        return f"{self.vendor}/{self.auth_channel}/{self.model_id}"

    @property
    def legacy(self) -> str:
        return f"{self.provider}/{self.model_id}"


@dataclass(frozen=True, slots=True)
class ModelRegistryEntry:
    model: Model
    source: str
    owner: str | None = None
    priority: int = 0


_models: dict[tuple[str, str], list[ModelRegistryEntry]] = defaultdict(list)


def _make_model(
    *,
    id: str,
    name: str,
    api: str,
    provider: str,
    base_url: str,
    reasoning: bool,
    input: list[str],
    cost: dict[str, float],
    context_window: int,
    max_tokens: int,
    headers: dict[str, str] | None = None,
    compat: dict[str, object] | None = None,
) -> Model:
    return Model(
        id=id,
        name=name,
        api=api,
        provider=provider,
        baseUrl=base_url,
        reasoning=reasoning,
        input=input,
        cost=ModelCost(
            input=cost["input"],
            output=cost["output"],
            cacheRead=cost["cacheRead"],
            cacheWrite=cost["cacheWrite"],
        ),
        contextWindow=context_window,
        maxTokens=max_tokens,
        headers=headers,
        compat=compat,
    )


BUILTIN_MODELS: tuple[Model, ...] = (
    _make_model(
        id="claude-opus-4-7",
        name="Claude Opus 4.7",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 5, "output": 25, "cacheRead": 0.5, "cacheWrite": 6.25},
        context_window=1_000_000,
        max_tokens=128_000,
    ),
    _make_model(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 3, "output": 15, "cacheRead": 0.3, "cacheWrite": 3.75},
        context_window=1_000_000,
        max_tokens=64_000,
    ),
    _make_model(
        id="claude-haiku-4-5-20251001",
        name="Claude Haiku 4.5",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 1, "output": 5, "cacheRead": 0.1, "cacheWrite": 1.25},
        context_window=200000,
        max_tokens=64000,
    ),
    _make_model(
        id="gpt-5.4",
        name="GPT-5.4",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 2.5, "output": 15, "cacheRead": 0.25, "cacheWrite": 0},
        context_window=1_000_000,
        max_tokens=128_000,
    ),
    _make_model(
        id="gpt-4o-mini",
        name="GPT-4o mini",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=False,
        input=["text", "image"],
        cost={"input": 0.15, "output": 0.6, "cacheRead": 0.08, "cacheWrite": 0},
        context_window=128000,
        max_tokens=16384,
    ),
    _make_model(
        id="gpt-4o-mini-chat-completions",
        name="GPT-4o mini (Chat Completions)",
        api="openai-completions",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=False,
        input=["text", "image"],
        cost={"input": 0.15, "output": 0.6, "cacheRead": 0.08, "cacheWrite": 0},
        context_window=128000,
        max_tokens=16384,
    ),
    _make_model(
        id="gpt-5.3-codex",
        name="GPT-5.3 Codex",
        api="openai-codex-responses",
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 1.75, "output": 14, "cacheRead": 0.175, "cacheWrite": 0},
        context_window=272000,
        max_tokens=128000,
    ),
    _make_model(
        id="glm-5",
        name="GLM-5",
        api="openai-completions",
        provider="opencode",
        base_url="https://opencode.ai/zen/v1",
        reasoning=True,
        input=["text"],
        cost={"input": 1, "output": 3.2, "cacheRead": 0.2, "cacheWrite": 0},
        context_window=204800,
        max_tokens=131072,
        compat={"sendSessionAffinityHeaders": True},
    ),
    # ── GitHub Copilot models (verified against Business API 2026-06-22) ──────
    # Azure-backed legacy models — no supported_endpoints in /models response
    # but still routable via the proxy.
    _make_model(
        id="gpt-4.1",
        name="GPT-4.1 (Copilot)",
        api="openai-completions",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=False,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=128_000,
        max_tokens=16_384,
        headers=COPILOT_HEADERS,
        compat={
            "supportsStore": False,
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
    ),
    _make_model(
        id="gpt-4o",
        name="GPT-4o (Copilot)",
        api="openai-completions",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=False,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=128_000,
        max_tokens=16_384,
        headers=COPILOT_HEADERS,
        compat={
            "supportsStore": False,
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
    ),
    # OpenAI models — actively served via /responses (and /chat/completions
    # where supported).
    _make_model(
        id="gpt-5-mini",
        name="GPT-5 mini (Copilot)",
        api="openai-responses",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=264_000,
        max_tokens=64_000,
        headers=COPILOT_HEADERS,
    ),
    _make_model(
        id="gpt-5.3-codex",
        name="GPT-5.3 Codex (Copilot)",
        api="openai-responses",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=400_000,
        max_tokens=128_000,
        headers=COPILOT_HEADERS,
    ),
    _make_model(
        id="gpt-5.4",
        name="GPT-5.4 (Copilot)",
        api="openai-responses",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=400_000,
        max_tokens=128_000,
        headers=COPILOT_HEADERS,
    ),
    _make_model(
        id="gpt-5.4-mini",
        name="GPT-5.4 mini (Copilot)",
        api="openai-responses",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=400_000,
        max_tokens=128_000,
        headers=COPILOT_HEADERS,
    ),
    _make_model(
        id="gpt-5.5",
        name="GPT-5.5 (Copilot)",
        api="openai-responses",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=400_000,
        max_tokens=128_000,
        headers=COPILOT_HEADERS,
    ),
    # Google models — served via /chat/completions by the Copilot proxy.
    _make_model(
        id="gemini-3.1-pro-preview",
        name="Gemini 3.1 Pro (Copilot)",
        api="openai-completions",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=264_000,
        max_tokens=64_000,
        headers=COPILOT_HEADERS,
        compat={
            "supportsStore": False,
            "supportsDeveloperRole": False,
        },
    ),
    _make_model(
        id="gemini-3.5-flash",
        name="Gemini 3.5 Flash (Copilot)",
        api="openai-completions",
        provider="github-copilot",
        base_url=GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=264_000,
        max_tokens=64_000,
        headers=COPILOT_HEADERS,
        compat={
            "supportsStore": False,
            "supportsDeveloperRole": False,
        },
    ),
    # NOTE: Anthropic claude-* models are available via the Copilot Business
    # API (/v1/messages + /chat/completions) but require Bearer-auth on the
    # anthropic-messages provider — deferred (see ADR-0028 / commit d66f256).
    _make_model(
        id="faux-1",
        name="Faux Model",
        api="faux",
        provider="faux",
        base_url="http://localhost:0",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=128000,
        max_tokens=16384,
    ),
)


def register_model(
    model: Model,
    *,
    source: str = RUNTIME_SOURCE,
    owner: str | None = None,
    priority: int | None = None,
) -> None:
    if model.context_window <= 0:
        raise ValueError("model.contextWindow must be greater than 0")
    if model.max_tokens <= 0:
        raise ValueError("model.maxTokens must be greater than 0")
    key = (model.provider, model.id)
    resolved_priority = SOURCE_PRIORITIES.get(source, 100 if priority is None else priority)
    if priority is not None:
        resolved_priority = priority
    _models[key] = [
        entry
        for entry in _models[key]
        if not (entry.source == source and entry.owner == owner)
    ]
    _models[key].append(
        ModelRegistryEntry(
            model=model,
            source=source,
            owner=owner,
            priority=resolved_priority,
        )
    )


def get_model(provider: str, model_id: str) -> Model:
    entry = get_model_entry(provider, model_id)
    if entry is None:
        raise KeyError(f"unknown model {provider}/{model_id}")
    return entry.model


def get_model_entry(provider: str, model_id: str) -> ModelRegistryEntry | None:
    entries = _models.get((provider, model_id), [])
    if not entries:
        return None
    return max(enumerate(entries), key=lambda item: (item[1].priority, item[0]))[1]


def list_models(provider: str | None = None) -> list[Model]:
    return [entry.model for entry in list_model_entries(provider)]


def list_model_entries(provider: str | None = None) -> list[ModelRegistryEntry]:
    entries: list[ModelRegistryEntry] = []
    for (entry_provider, _model_id), layers in _models.items():
        if provider is not None and entry_provider != provider:
            continue
        if layers:
            entries.append(
                max(
                    enumerate(layers),
                    key=lambda item: (item[1].priority, item[0]),
                )[1]
            )
    return sorted(entries, key=lambda entry: (entry.model.provider, entry.model.id))


def parse_model_ref(model_ref: str) -> ModelRef:
    """Parse a user-visible model ref into a ``ModelRef``.

    Accepts the canonical three-segment form ``vendor/auth-channel/model-id``
    and the legacy two-segment ``provider/model-id`` for one compatibility
    window. Auth-channel is checked against an exact lowercase allowlist;
    unknown channels and unknown ``(vendor, auth-channel)`` combinations
    fail-fast rather than fall back to vendor-based inference.

    Model ids may contain ``/`` (e.g. OpenAI-compatible custom providers
    using ``org/model``-style ids). Disambiguation: when the second segment
    is in :data:`ALLOWED_AUTH_CHANNELS` and a third segment exists, the ref
    is treated as canonical and any remaining segments are joined back into
    ``model_id``. Otherwise the ref is legacy two-segment with ``model_id``
    consuming everything after the first ``/``.
    """

    if not isinstance(model_ref, str) or not model_ref.strip():
        raise ValueError(_unknown_model_ref_message(model_ref))
    parts = model_ref.split("/")
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError(_unknown_model_ref_message(model_ref))
    forced_three_segment = (
        len(parts) >= 3 and parts[0] in _RESERVED_FIRST_SEGMENTS
    )
    auth_channel_match = (
        len(parts) >= 3 and parts[1] in ALLOWED_AUTH_CHANNELS
    )
    if forced_three_segment or auth_channel_match:
        vendor = parts[0]
        auth_channel = parts[1]
        if auth_channel not in ALLOWED_AUTH_CHANNELS:
            raise ValueError(
                f"unknown auth-channel {auth_channel!r}; expected one of "
                f"{sorted(ALLOWED_AUTH_CHANNELS)}. "
                "Format: vendor/auth-channel/model"
            )
        model_id = "/".join(parts[2:])
        provider = _resolve_provider_for_canonical(vendor, auth_channel, model_ref)
        return ModelRef(
            vendor=vendor,
            auth_channel=auth_channel,
            model_id=model_id,
            provider=provider,
        )
    # Legacy two-segment: provider / model_id (model_id may contain ``/``).
    provider, _sep, model_id = model_ref.partition("/")
    vendor, auth_channel = provider_auth_info(provider)
    return ModelRef(
        vendor=vendor,
        auth_channel=auth_channel,
        model_id=model_id,
        provider=provider,
    )


def provider_auth_info(provider: str) -> tuple[str, str]:
    """Return ``(vendor, auth_channel)`` for an internal provider id.

    Built-in providers use the fixed mapping. Custom providers default to
    ``auth-channel=api``; settings/extension declarations of ``authChannel``
    are not consulted in this round.
    """

    info = _BUILTIN_PROVIDER_AUTH.get(provider)
    if info is not None:
        return info
    return (provider, DEFAULT_AUTH_CHANNEL)


def canonical_model_ref(model_or_ref: Model | ModelRef | str) -> str:
    """Render the canonical ``vendor/auth-channel/model-id`` form."""

    if isinstance(model_or_ref, ModelRef):
        return model_or_ref.canonical
    if isinstance(model_or_ref, Model):
        vendor, auth_channel = provider_auth_info(model_or_ref.provider)
        return f"{vendor}/{auth_channel}/{model_or_ref.id}"
    return parse_model_ref(model_or_ref).canonical


def legacy_model_ref(model_or_ref: Model | ModelRef | str) -> str:
    """Render the legacy two-segment ``provider/model-id`` form.

    Kept for compatibility tests and migration messaging only; new outputs
    should use :func:`canonical_model_ref`.
    """

    if isinstance(model_or_ref, ModelRef):
        return model_or_ref.legacy
    if isinstance(model_or_ref, Model):
        return f"{model_or_ref.provider}/{model_or_ref.id}"
    return parse_model_ref(model_or_ref).legacy


def resolve_model(model_ref: str) -> Model:
    parsed = parse_model_ref(model_ref)
    return get_model(parsed.provider, parsed.model_id)


def _resolve_provider_for_canonical(
    vendor: str,
    auth_channel: str,
    raw_ref: str,
) -> str:
    provider = _VENDOR_CHANNEL_TO_PROVIDER.get((vendor, auth_channel))
    if provider is not None:
        return provider
    # Custom providers may not have a built-in vendor/channel mapping. Allow
    # ``vendor=<custom-provider-id>`` with the default ``api`` channel as long
    # as that provider id is registered AND is not a built-in. Built-ins are
    # exposed via the canonical (vendor, auth-channel) pair only — accepting
    # e.g. ``openai-codex/api/...`` here would silently re-canonicalize to
    # ``openai/oauth/...`` and defeat the user-visible auth-channel guarantee.
    if (
        auth_channel == DEFAULT_AUTH_CHANNEL
        and vendor not in _BUILTIN_PROVIDER_AUTH
        and any(provider_id == vendor for provider_id, _model_id in _models)
    ):
        return vendor
    raise ValueError(
        f"unknown model {raw_ref}: no internal provider for "
        f"vendor={vendor!r} auth-channel={auth_channel!r}. "
        "Format: vendor/auth-channel/model"
    )


def _unknown_model_ref_message(model_ref: object) -> str:
    return (
        f"unknown model {model_ref!r}: model reference must use "
        "vendor/auth-channel/model (legacy provider/model still accepted)"
    )


def validate_thinking_level_for_model(model: Model, level: ThinkingLevel) -> ThinkingLevel:
    if level == "off":
        return level
    if not model.reasoning:
        raise ValueError(f"model {model.provider}/{model.id} does not support thinking")
    if level == "xhigh" and not supports_xhigh(model):
        raise ValueError(f"model {model.provider}/{model.id} does not support thinking level xhigh")
    return level


def clear_models_for_tests() -> None:
    _models.clear()


def unregister_models_by_source(
    source: str,
    *,
    owner: str | None = None,
    provider: str | None = None,
) -> None:
    for key in list(_models):
        key_provider, _model_id = key
        if provider is not None and key_provider != provider:
            continue
        _models[key] = [
            entry
            for entry in _models[key]
            if not (entry.source == source and (owner is None or entry.owner == owner))
        ]
        if not _models[key]:
            del _models[key]


for _model in BUILTIN_MODELS:
    register_model(_model, source=BUILTIN_SOURCE, priority=0)


__all__ = [
    "ALLOWED_AUTH_CHANNELS",
    "BUILTIN_MODELS",
    "BUILTIN_SOURCE",
    "DEFAULT_AUTH_CHANNEL",
    "EXTENSION_SOURCE",
    "ModelRef",
    "ModelRegistryEntry",
    "RUNTIME_SOURCE",
    "SETTINGS_SOURCE",
    "canonical_model_ref",
    "clear_models_for_tests",
    "get_model",
    "get_model_entry",
    "legacy_model_ref",
    "list_model_entries",
    "list_models",
    "parse_model_ref",
    "provider_auth_info",
    "register_model",
    "resolve_model",
    "unregister_models_by_source",
    "validate_thinking_level_for_model",
]

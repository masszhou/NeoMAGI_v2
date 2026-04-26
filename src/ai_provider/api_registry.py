"""Provider API-family registry."""

from __future__ import annotations

from dataclasses import dataclass

from .runtime_types import SimpleStreamFunction, SimpleStreamOptions, StreamFunction, StreamOptions
from .streaming import AssistantMessageEventStream
from .types import Context, Model


@dataclass(slots=True)
class ApiRegistration:
    api_name: str
    stream_fn: StreamFunction
    stream_simple_fn: SimpleStreamFunction | None = None


_apis: dict[str, ApiRegistration] = {}
_builtins_registered = False


def register_api(
    api_name: str,
    stream_fn: StreamFunction,
    stream_simple_fn: SimpleStreamFunction | None = None,
) -> None:
    _apis[api_name] = ApiRegistration(
        api_name=api_name,
        stream_fn=stream_fn,
        stream_simple_fn=stream_simple_fn,
    )


def unregister_api(api_name: str) -> None:
    _apis.pop(api_name, None)


def get_api(api_name: str) -> ApiRegistration:
    _ensure_builtin_apis()
    try:
        return _apis[api_name]
    except KeyError as exc:
        raise KeyError(f"unknown provider API family {api_name!r}") from exc


def stream(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    return get_api(model.api).stream_fn(model, context, options)


def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    registration = get_api(model.api)
    if registration.stream_simple_fn is not None:
        return registration.stream_simple_fn(model, context, options)
    return registration.stream_fn(model, context, options)


def _ensure_builtin_apis() -> None:
    global _builtins_registered
    if _builtins_registered:
        return
    from .providers.anthropic import stream_anthropic_messages
    from .providers.faux import stream_faux
    from .providers.openai_completions import stream_openai_completions
    from .providers.openai_responses import stream_openai_responses

    register_api("anthropic-messages", stream_anthropic_messages)
    register_api("faux", stream_faux)
    register_api("openai-completions", stream_openai_completions)
    register_api("openai-responses", stream_openai_responses)
    _builtins_registered = True


def clear_apis_for_tests() -> None:
    global _builtins_registered
    _apis.clear()
    _builtins_registered = False


__all__ = [
    "ApiRegistration",
    "clear_apis_for_tests",
    "get_api",
    "register_api",
    "stream",
    "stream_simple",
    "unregister_api",
]

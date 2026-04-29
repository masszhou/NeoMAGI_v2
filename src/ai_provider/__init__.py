"""ai_provider — Pi-compatible message / content / stream / model / provider types.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §`ai_provider` Protocol (line 96–280).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/ai/src/types.ts
  - packages/ai/src/stream.ts
  - packages/ai/src/utils/event-stream.ts
  - packages/ai/src/utils/overflow.ts
  - packages/ai/src/providers/{anthropic,openai-responses,openai-completions,amazon-bedrock,faux}.ts
"""

from .api_registry import get_api, register_api, stream, stream_simple, unregister_api
from .model_registry import (
    get_model,
    list_models,
    register_model,
    resolve_model,
    validate_thinking_level_for_model,
)
from .oauth import (
    OAuthCredentials,
    OAuthLoginCallbacks,
    OpenAIOAuthProvider,
    get_oauth_api_key,
    get_oauth_provider,
    list_oauth_providers,
)
from .runtime_types import ProviderResponse, SimpleStreamOptions, StreamOptions
from .streaming import AssistantMessageEventStream, create_assistant_message_event_stream

__all__ = [
    "AssistantMessageEventStream",
    "OAuthCredentials",
    "OAuthLoginCallbacks",
    "OpenAIOAuthProvider",
    "ProviderResponse",
    "SimpleStreamOptions",
    "StreamOptions",
    "create_assistant_message_event_stream",
    "get_api",
    "get_model",
    "get_oauth_api_key",
    "get_oauth_provider",
    "list_models",
    "list_oauth_providers",
    "register_api",
    "register_model",
    "resolve_model",
    "stream",
    "stream_simple",
    "unregister_api",
    "validate_thinking_level_for_model",
]

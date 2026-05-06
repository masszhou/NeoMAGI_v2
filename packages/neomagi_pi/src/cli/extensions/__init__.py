"""cli.extensions — Python ExtensionAPI mirroring Pi extension semantics.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §Extension API (line 709–836).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/coding-agent/src/core/extensions/types.ts
  - packages/coding-agent/src/core/event-bus.ts
  - packages/coding-agent/docs/extensions.md
"""

from .api import ExtensionAPIImpl, RuntimeNotInitializedError, create_extension_api
from .diagnostics import ExtensionDiagnostic
from .event_bus import ExtensionEventBus
from .loader import LoadExtensionsResult, load_extension, load_extension_from_factory, load_extensions
from .runner import ExtensionRunner
from .runtime import ExtensionRuntime, LoadedExtension, RegisteredProvider, create_extension_runtime
from .ui import NoopExtensionUIContext

__all__ = [
    "ExtensionAPIImpl",
    "ExtensionDiagnostic",
    "ExtensionEventBus",
    "ExtensionRunner",
    "ExtensionRuntime",
    "LoadExtensionsResult",
    "LoadedExtension",
    "NoopExtensionUIContext",
    "RegisteredProvider",
    "RuntimeNotInitializedError",
    "create_extension_api",
    "create_extension_runtime",
    "load_extension",
    "load_extension_from_factory",
    "load_extensions",
]

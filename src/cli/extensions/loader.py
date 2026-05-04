"""Python-native extension loader."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from .api import create_extension_api
from .diagnostics import ExtensionDiagnostic
from .event_bus import ExtensionEventBus
from .runtime import ExtensionRuntime, LoadedExtension, create_extension_runtime


@dataclass(frozen=True, slots=True)
class LoadExtensionsResult:
    runtime: ExtensionRuntime
    event_bus: ExtensionEventBus
    diagnostics: tuple[ExtensionDiagnostic, ...]


async def load_extensions(
    paths: list[str | Path],
    *,
    cwd: str | Path,
    event_bus: ExtensionEventBus | None = None,
    runtime: ExtensionRuntime | None = None,
) -> LoadExtensionsResult:
    bus = event_bus or ExtensionEventBus()
    ext_runtime = runtime or create_extension_runtime()
    diagnostics: list[ExtensionDiagnostic] = []
    for path in paths:
        extension = await load_extension(Path(path), cwd=Path(cwd), runtime=ext_runtime, event_bus=bus)
        if extension is not None:
            ext_runtime.register(extension)
    diagnostics.extend(ext_runtime.all_diagnostics())
    diagnostics.extend(bus.diagnostics)
    return LoadExtensionsResult(ext_runtime, bus, tuple(diagnostics))


async def load_extension(
    path: Path,
    *,
    cwd: str | Path,
    runtime: ExtensionRuntime,
    event_bus: ExtensionEventBus,
) -> LoadedExtension | None:
    resolved = _resolve_extension_path(path)
    extension = LoadedExtension(name=_extension_name(path), path=resolved)
    if resolved is None:
        extension.diagnostics.append(
            ExtensionDiagnostic(severity="error", message="extension path does not exist", extension=extension.name, path=str(path))
        )
        return extension
    try:
        module = _load_module(resolved)
    except Exception as exc:
        extension.diagnostics.append(
            ExtensionDiagnostic(severity="error", message=f"failed to import extension: {exc}", extension=extension.name, path=str(resolved))
        )
        return extension
    setup = getattr(module, "setup", None)
    if not callable(setup):
        extension.diagnostics.append(
            ExtensionDiagnostic(severity="error", message="extension missing setup(api)", extension=extension.name, path=str(resolved))
        )
        return extension
    try:
        await _call_setup(setup, extension=extension, runtime=runtime, cwd=str(cwd), event_bus=event_bus)
    except Exception as exc:
        extension.diagnostics.append(
            ExtensionDiagnostic(severity="error", message=f"extension setup failed: {exc}", extension=extension.name, path=str(resolved))
        )
    return extension


async def load_extension_from_factory(
    factory: Callable[[Any], Any],
    *,
    name: str = "factory-extension",
    cwd: str | Path = ".",
    runtime: ExtensionRuntime | None = None,
    event_bus: ExtensionEventBus | None = None,
) -> LoadedExtension:
    ext_runtime = runtime or create_extension_runtime()
    bus = event_bus or ExtensionEventBus()
    extension = LoadedExtension(name=name)
    try:
        await _call_setup(factory, extension=extension, runtime=ext_runtime, cwd=str(cwd), event_bus=bus)
    except Exception as exc:
        extension.diagnostics.append(
            ExtensionDiagnostic(severity="error", message=f"extension setup failed: {exc}", extension=name)
        )
    ext_runtime.register(extension)
    return extension


def _resolve_extension_path(path: Path) -> Path | None:
    candidate = path.expanduser().resolve()
    if candidate.is_dir():
        index = candidate / "index.py"
        return index.resolve() if index.is_file() else None
    if candidate.is_file() and candidate.suffix == ".py":
        return candidate
    return None


def _extension_name(path: Path) -> str:
    if path.suffix == ".py":
        return path.stem
    return path.name


def _load_module(path: Path) -> ModuleType:
    importlib.invalidate_caches()
    module_name = f"_neomagi_ext_{path.stem}_{uuid4().hex}"
    spec = importlib.util.spec_from_loader(module_name, loader=None, origin=str(path))
    if spec is None:
        raise ImportError(f"cannot create module spec for {path}")
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__spec__ = spec
    sys.modules[module_name] = module
    try:
        source = path.read_text(encoding="utf-8")
        exec(compile(source, str(path), "exec"), module.__dict__)
    finally:
        sys.modules.pop(module_name, None)
    return module


async def _call_setup(
    setup: Callable[[Any], Any],
    *,
    extension: LoadedExtension,
    runtime: ExtensionRuntime,
    cwd: str,
    event_bus: ExtensionEventBus,
) -> None:
    api = create_extension_api(extension, runtime, cwd, event_bus)
    result = setup(api)
    if inspect.isawaitable(result):
        await result


__all__ = [
    "LoadExtensionsResult",
    "load_extension",
    "load_extension_from_factory",
    "load_extensions",
]

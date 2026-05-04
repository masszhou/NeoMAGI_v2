from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from cli.extensions.loader import load_extension_from_factory
from cli.extensions.runtime import create_extension_runtime


def test_provider_registration_stub_records_owner_and_diagnostic(tmp_path) -> None:
    def setup(api) -> None:
        api.register_provider(
            "local-openai",
            {
                "baseUrl": "http://127.0.0.1:11434/v1",
                "models": [
                    {
                        "id": "local-1",
                        "name": "Local 1",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0},
                        "contextWindow": 8192,
                        "maxTokens": 2048,
                    }
                ],
            },
        )

    runtime = create_extension_runtime()
    extension = asyncio.run(load_extension_from_factory(setup, name="provider-ext", cwd=tmp_path, runtime=runtime))

    registered = extension.providers["local-openai"]
    assert registered.owner == "provider-ext"
    assert registered.config.models is not None
    assert registered.config.models[0].context_window == 8192
    assert any("not applied in M8" in diagnostic.message for diagnostic in extension.diagnostics)


def test_provider_registration_rejects_invalid_model_limits(tmp_path) -> None:
    def setup(api) -> None:
        with pytest.raises(ValidationError):
            api.register_provider(
                "bad",
                {
                    "models": [
                        {
                            "id": "bad-1",
                            "name": "Bad 1",
                            "reasoning": False,
                            "input": ["text"],
                            "cost": {"input": 0, "output": 0},
                            "contextWindow": 0,
                            "maxTokens": -1,
                        }
                    ]
                },
            )

    runtime = create_extension_runtime()
    extension = asyncio.run(load_extension_from_factory(setup, name="bad-provider", cwd=tmp_path, runtime=runtime))

    assert extension.providers == {}


def test_provider_unregister_removes_extension_owned_record(tmp_path) -> None:
    def setup(api) -> None:
        api.register_provider("temporary", {"baseUrl": "https://example.test", "models": []})
        api.unregister_provider("temporary")

    runtime = create_extension_runtime()
    extension = asyncio.run(load_extension_from_factory(setup, name="provider-ext", cwd=tmp_path, runtime=runtime))

    assert extension.providers == {}
    assert any("unregister is recorded" in diagnostic.message for diagnostic in extension.diagnostics)


def test_shortcut_registration_refuses_core_key_in_m8(tmp_path) -> None:
    def setup(api) -> None:
        api.register_shortcut("Enter", {"description": "submit override"})
        api.register_shortcut("Ctrl+X", {"description": "custom"})

    runtime = create_extension_runtime()
    extension = asyncio.run(load_extension_from_factory(setup, name="shortcut-ext", cwd=tmp_path, runtime=runtime))

    assert "Enter" not in extension.shortcuts
    assert extension.shortcuts["Ctrl+X"].description == "custom"
    assert any("core key" in diagnostic.message for diagnostic in extension.diagnostics)

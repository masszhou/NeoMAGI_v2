from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from ai_provider.model_registry import (
    canonical_model_ref,
    provider_auth_info,
    resolve_model,
    unregister_models_by_source,
)
from cli.core.model_settings import apply_extension_providers
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.extensions.loader import load_extension_from_factory
from cli.extensions.runtime import create_extension_runtime


def test_provider_registration_records_owner_without_m8_stub_diagnostic(tmp_path) -> None:
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
    assert not any("not applied in M8" in diagnostic.message for diagnostic in extension.diagnostics)


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


def test_extension_provider_authchannel_field_does_not_change_credential_channel(
    tmp_path,
) -> None:
    """Extension ``register_provider`` callers may pass a stray ``authChannel``
    field; it must be ignored. The custom provider stays on the ``api`` lane
    and never picks up ``openai-codex`` OAuth credentials.
    """

    def setup(api) -> None:
        api.register_provider(
            "local-openai",
            {
                "baseUrl": "http://127.0.0.1:11434/v1",
                "api": "openai-responses",
                "authChannel": "oauth",
                "models": [
                    {
                        "id": "local-1",
                        "name": "Local 1",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0},
                        "contextWindow": 8192,
                        "maxTokens": 2048,
                        "authChannel": "oauth",
                    }
                ],
            },
        )

    runtime = create_extension_runtime()
    extension = asyncio.run(
        load_extension_from_factory(
            setup,
            name="provider-ext",
            cwd=tmp_path,
            runtime=runtime,
        )
    )
    try:
        diagnostics = apply_extension_providers([extension])
        assert diagnostics == []
        model = resolve_model("local-openai/local-1")
        assert model.provider == "local-openai"
        # The runtime must still treat this as ``api``-channel; otherwise an
        # ``authChannel="oauth"`` extension could quietly hijack the OAuth
        # credential path.
        assert canonical_model_ref(model) == "local-openai/api/local-1"
        assert provider_auth_info(model.provider) == ("local-openai", "api")
    finally:
        unregister_models_by_source("extension")


def test_provider_unregister_removes_extension_owned_record(tmp_path) -> None:
    def setup(api) -> None:
        api.register_provider("temporary", {"baseUrl": "https://example.test", "models": []})
        api.unregister_provider("temporary")

    runtime = create_extension_runtime()
    extension = asyncio.run(load_extension_from_factory(setup, name="provider-ext", cwd=tmp_path, runtime=runtime))

    assert extension.providers == {}
    assert not any("unregister is recorded" in diagnostic.message for diagnostic in extension.diagnostics)


def test_extension_provider_registration_applies_to_live_model_registry(tmp_path) -> None:
    (tmp_path / ".magipi" / "extensions").mkdir(parents=True)
    (tmp_path / ".magipi" / "extensions" / "provider.py").write_text(
        """
def setup(api):
    api.register_provider(
        "local-openai",
        {
            "baseUrl": "http://127.0.0.1:11434/v1",
            "api": "openai-responses",
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
""",
        encoding="utf-8",
    )
    runtime = InteractiveAgentRuntime(cwd=tmp_path, tool_profile="none")
    try:
        model = resolve_model("local-openai/local-1")
        assert model.base_url == "http://127.0.0.1:11434/v1"
        assert model.api == "openai-responses"
    finally:
        runtime.shutdown()
        unregister_models_by_source("extension")


def test_shortcut_registration_refuses_core_key_in_m8(tmp_path) -> None:
    def setup(api) -> None:
        api.register_shortcut("Enter", {"description": "submit override"})
        api.register_shortcut("Ctrl+X", {"description": "custom"})

    runtime = create_extension_runtime()
    extension = asyncio.run(load_extension_from_factory(setup, name="shortcut-ext", cwd=tmp_path, runtime=runtime))

    assert "Enter" not in extension.shortcuts
    assert extension.shortcuts["Ctrl+X"].description == "custom"
    assert any("core key" in diagnostic.message for diagnostic in extension.diagnostics)

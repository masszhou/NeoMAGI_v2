from __future__ import annotations

from policy.redaction import (
    REDACTED_PATH,
    REDACTED_VALUE,
    redact_for_export,
    redact_literal_values,
    redact_secret_keys,
    redacted_command_preview,
)


def test_secret_key_redaction_preserves_safe_env_references() -> None:
    payload = {
        "apiKey": "sk-test-secret",
        "compat": {"apiKeyEnv": "OPENAI_API_KEY", "apiKeyHeader": "X-API-Key"},
        "headers": {"Authorization": "Bearer test-secret-token"},
    }

    redacted, applied = redact_secret_keys(payload)

    assert applied is True
    assert redacted["apiKey"] == REDACTED_VALUE
    assert redacted["headers"]["Authorization"] == REDACTED_VALUE
    assert redacted["compat"]["apiKeyEnv"] == "OPENAI_API_KEY"
    assert redacted["compat"]["apiKeyHeader"] == "X-API-Key"


def test_oauth_access_refresh_short_keys_are_redacted() -> None:
    payload = {
        "oauth": {
            "access": "short-access-token",
            "refresh": "short-refresh-token",
            "expires": 200000,
            "accountId": "acct-123",
        }
    }

    redacted, report = redact_for_export(payload)

    assert redacted["oauth"]["access"] == REDACTED_VALUE
    assert redacted["oauth"]["refresh"] == REDACTED_VALUE
    assert redacted["oauth"]["expires"] == 200000
    assert redacted["oauth"]["accountId"] == "acct-123"
    assert report.counts["secret_like_key"] == 2


def test_export_redaction_masks_env_paths_and_content(tmp_path) -> None:
    payload = {
        "message": {
            "role": "toolResult",
            "content": [{"type": "text", "text": "OPENAI_API_KEY=sk-export-secret"}],
            "details": {"path": ".env", "fullOutputPath": str(tmp_path / "raw.txt")},
        }
    }

    redacted, report = redact_for_export(payload, cwd=tmp_path)

    content = redacted["message"]["content"][0]
    assert content["type"] == "text"
    assert content["text"] == REDACTED_VALUE
    assert redacted["message"]["details"]["path"] == REDACTED_PATH
    assert redacted["message"]["details"]["fullOutputPath"] == REDACTED_PATH
    assert "sensitive_path_content" in report.counts


def test_command_preview_redacts_long_tokens_but_preserves_env_refs() -> None:
    secret = "sk-" + ("A" * 40)

    preview, applied = redacted_command_preview(f"echo {secret} $OPENAI_API_KEY")

    assert applied is True
    assert secret not in preview
    assert "$OPENAI_API_KEY" in preview


def test_literal_value_redaction_masks_known_runtime_secret_values() -> None:
    redacted, applied = redact_literal_values(
        "before FAKE_BRAVE_SECRET_VALUE after",
        ("FAKE_BRAVE_SECRET_VALUE",),
    )

    assert applied is True
    assert "FAKE_BRAVE_SECRET_VALUE" not in redacted
    assert REDACTED_VALUE in redacted


def test_literal_value_redaction_ignores_low_entropy_values() -> None:
    redacted, applied = redact_literal_values(
        "test token key short real-secret-value",
        ("test", "token", "key", "short", "real-secret-value"),
    )

    assert applied is True
    assert "test token key short" in redacted
    assert "real-secret-value" not in redacted

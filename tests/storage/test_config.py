from __future__ import annotations

import pytest

from storage.config import DatabaseConfigError, load_database_config


def test_load_database_config_uses_env_and_defaults_schema() -> None:
    config = load_database_config(
        env={
            "DATABASE_HOST": "db.local",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "neo",
            "DATABASE_PASSWORD": "secret",
            "DATABASE_NAME": "neomagi",
        },
        dotenv_path="/no/such/.env",
    )

    assert config.host == "db.local"
    assert config.port == 5432
    assert config.schema == "neomagi"


def test_load_database_config_reports_missing_keys() -> None:
    with pytest.raises(DatabaseConfigError, match="DATABASE_PASSWORD"):
        load_database_config(
            env={
                "DATABASE_HOST": "db.local",
                "DATABASE_PORT": "5432",
                "DATABASE_USER": "neo",
                "DATABASE_NAME": "neomagi",
            },
            dotenv_path="/no/such/.env",
        )


def test_load_database_config_rejects_invalid_schema() -> None:
    with pytest.raises(DatabaseConfigError, match="DATABASE_SCHEMA"):
        load_database_config(
            env={
                "DATABASE_HOST": "db.local",
                "DATABASE_PORT": "5432",
                "DATABASE_USER": "neo",
                "DATABASE_PASSWORD": "secret",
                "DATABASE_NAME": "neomagi",
                "DATABASE_SCHEMA": "bad-schema",
            },
            dotenv_path="/no/such/.env",
        )

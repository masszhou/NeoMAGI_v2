from __future__ import annotations

import pytest

import storage.config as config_module
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


def test_load_database_config_uses_neomagi_env_file(tmp_path) -> None:
    env_file = tmp_path / "neomagi.env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_HOST=127.0.0.1",
                "DATABASE_PORT=6543",
                "DATABASE_USER=neo",
                "DATABASE_PASSWORD=secret",
                "DATABASE_NAME=neomagi",
                "DATABASE_SCHEMA=custom_schema",
            ]
        ),
        encoding="utf-8",
    )

    config = load_database_config(
        env={"NEOMAGI_ENV_FILE": str(env_file)},
    )

    assert config.host == "127.0.0.1"
    assert config.port == 6543
    assert config.schema == "custom_schema"


def test_load_database_config_env_overrides_neomagi_env_file(tmp_path) -> None:
    env_file = tmp_path / "neomagi.env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_HOST=file-host",
                "DATABASE_PORT=5432",
                "DATABASE_USER=file-user",
                "DATABASE_PASSWORD=file-secret",
                "DATABASE_NAME=file-db",
            ]
        ),
        encoding="utf-8",
    )

    config = load_database_config(
        env={
            "NEOMAGI_ENV_FILE": str(env_file),
            "DATABASE_HOST": "env-host",
            "DATABASE_PORT": "6543",
            "DATABASE_USER": "env-user",
            "DATABASE_PASSWORD": "env-secret",
            "DATABASE_NAME": "env-db",
        },
    )

    assert config.host == "env-host"
    assert config.port == 6543
    assert config.user == "env-user"
    assert config.database == "env-db"


def test_load_database_config_missing_neomagi_env_file_fails_fast(tmp_path) -> None:
    missing = tmp_path / "missing.env"

    with pytest.raises(DatabaseConfigError, match="NEOMAGI_ENV_FILE"):
        load_database_config(env={"NEOMAGI_ENV_FILE": str(missing)})


def test_load_database_config_uses_app_root_dotenv_from_workspace_cwd(
    tmp_path,
    monkeypatch,
) -> None:
    app_env = tmp_path / "app.env"
    app_env.write_text(
        "\n".join(
            [
                "DATABASE_HOST=app-host",
                "DATABASE_PORT=5432",
                "DATABASE_USER=app-user",
                "DATABASE_PASSWORD=app-secret",
                "DATABASE_NAME=app-db",
            ]
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text(
        "\n".join(
            [
                "DATABASE_HOST=workspace-host",
                "DATABASE_PORT=6543",
                "DATABASE_USER=workspace-user",
                "DATABASE_PASSWORD=workspace-secret",
                "DATABASE_NAME=workspace-db",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(config_module, "_app_root_dotenv_path", lambda: app_env)

    config = load_database_config(env={})

    assert config.host == "app-host"
    assert config.port == 5432


def test_app_root_dotenv_path_finds_workspace_root_after_package_move(
    tmp_path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    module_dir = repo / "packages" / "neomagi_pi" / "src" / "storage"
    module_dir.mkdir(parents=True)
    (repo / ".env_template").write_text("DATABASE_HOST=\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "__file__", str(module_dir / "config.py"))

    assert config_module._app_root_dotenv_path() == repo / ".env"


def test_app_root_dotenv_path_falls_back_to_package_dir_outside_repo(
    tmp_path,
    monkeypatch,
) -> None:
    package_dir = tmp_path / "venv" / "site-packages" / "storage"
    package_dir.mkdir(parents=True)
    monkeypatch.setattr(config_module, "__file__", str(package_dir / "config.py"))

    assert config_module._app_root_dotenv_path() == package_dir / ".env"


def test_load_database_config_does_not_read_workspace_dotenv(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text(
        "\n".join(
            [
                "DATABASE_HOST=workspace-host",
                "DATABASE_PORT=5432",
                "DATABASE_USER=workspace-user",
                "DATABASE_PASSWORD=workspace-secret",
                "DATABASE_NAME=workspace-db",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        config_module,
        "_app_root_dotenv_path",
        lambda: tmp_path / "missing-app.env",
    )

    with pytest.raises(DatabaseConfigError, match="missing database configuration"):
        load_database_config(env={})

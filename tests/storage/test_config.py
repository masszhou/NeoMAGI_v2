from __future__ import annotations

import sys

import pytest

import storage.config as config_module
from storage.config import (
    DatabaseConfigError,
    describe_database_config_source,
    load_database_config,
    read_env_template,
    resolve_database_config,
    would_fall_back_to,
)


def _write_env(path, *, host="db.local", port=5432, schema=None) -> None:
    lines = [
        f"DATABASE_HOST={host}",
        f"DATABASE_PORT={port}",
        "DATABASE_USER=neo",
        "DATABASE_PASSWORD=secret",
        "DATABASE_NAME=neomagi",
    ]
    if schema is not None:
        lines.append(f"DATABASE_SCHEMA={schema}")
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def isolate_auto_sources(monkeypatch, tmp_path):
    """Stub auto file sources so host machine state cannot leak in.

    Tests that exercise the full resolver request this fixture; tests that
    target helpers directly skip it.
    """

    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: tmp_path / "iso_user_config" / "secrets" / "database.env",
    )
    monkeypatch.setattr(config_module, "_app_root_dotenv_path", lambda: None)
    return tmp_path


def test_shell_environment_wins_when_complete(isolate_auto_sources) -> None:
    config = load_database_config(
        env={
            "DATABASE_HOST": "db.local",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "neo",
            "DATABASE_PASSWORD": "secret",
            "DATABASE_NAME": "neomagi",
        },
    )

    assert config.host == "db.local"
    assert config.port == 5432
    assert config.schema == "neomagi"
    assert config.sslmode == "prefer"
    assert config.connect_kwargs()["sslmode"] == "prefer"


def test_database_sslmode_env_override(isolate_auto_sources) -> None:
    config = load_database_config(
        env={
            "DATABASE_HOST": "db.local",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "neo",
            "DATABASE_PASSWORD": "secret",
            "DATABASE_NAME": "neomagi",
            "DATABASE_SSLMODE": "verify-full",
        },
    )

    assert config.sslmode == "verify-full"
    assert config.connect_kwargs()["sslmode"] == "verify-full"


def test_database_sslmode_rejects_invalid_value(isolate_auto_sources) -> None:
    with pytest.raises(DatabaseConfigError, match="DATABASE_SSLMODE"):
        load_database_config(
            env={
                "DATABASE_HOST": "db.local",
                "DATABASE_PORT": "5432",
                "DATABASE_USER": "neo",
                "DATABASE_PASSWORD": "secret",
                "DATABASE_NAME": "neomagi",
                "DATABASE_SSLMODE": "maybe",
            },
        )


def test_partial_shell_environment_fails_fast(isolate_auto_sources) -> None:
    with pytest.raises(DatabaseConfigError, match="incomplete DATABASE_"):
        load_database_config(
            env={
                "DATABASE_HOST": "db.local",
                "DATABASE_PORT": "5432",
                "DATABASE_USER": "neo",
                "DATABASE_NAME": "neomagi",
            },
        )


def test_no_source_lists_attempts_in_error(isolate_auto_sources) -> None:
    with pytest.raises(DatabaseConfigError) as excinfo:
        load_database_config(env={})

    msg = str(excinfo.value)
    assert "missing database configuration" in msg
    assert "user config" in msg
    assert "magipi config init" in msg


def test_invalid_schema_is_rejected(isolate_auto_sources) -> None:
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
        )


def test_neomagi_env_file_is_read_when_set(tmp_path, isolate_auto_sources) -> None:
    env_file = tmp_path / "neomagi.env"
    _write_env(env_file, host="127.0.0.1", port=6543, schema="custom_schema")

    config = load_database_config(env={"NEOMAGI_ENV_FILE": str(env_file)})

    assert config.host == "127.0.0.1"
    assert config.port == 6543
    assert config.schema == "custom_schema"


def test_shell_env_overrides_neomagi_env_file(
    tmp_path,
    isolate_auto_sources,
) -> None:
    env_file = tmp_path / "neomagi.env"
    _write_env(env_file, host="file-host")

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
    assert config.user == "env-user"
    assert config.database == "env-db"


def test_partial_shell_env_does_not_silently_mix_with_file(
    tmp_path,
    isolate_auto_sources,
) -> None:
    env_file = tmp_path / "neomagi.env"
    _write_env(env_file, host="file-host")

    with pytest.raises(DatabaseConfigError, match="incomplete DATABASE_"):
        load_database_config(
            env={
                "NEOMAGI_ENV_FILE": str(env_file),
                "DATABASE_HOST": "env-host",
            },
        )


def test_neomagi_env_file_missing_fails_fast(
    tmp_path,
    isolate_auto_sources,
) -> None:
    missing = tmp_path / "missing.env"

    with pytest.raises(DatabaseConfigError, match="NEOMAGI_ENV_FILE"):
        load_database_config(env={"NEOMAGI_ENV_FILE": str(missing)})


def test_neomagi_env_file_incomplete_fails_fast(
    tmp_path,
    isolate_auto_sources,
) -> None:
    env_file = tmp_path / "incomplete.env"
    env_file.write_text("DATABASE_HOST=only-host\n", encoding="utf-8")

    with pytest.raises(DatabaseConfigError, match="missing required keys"):
        load_database_config(env={"NEOMAGI_ENV_FILE": str(env_file)})


def test_explicit_env_file_argument_wins_over_shell_environment(
    tmp_path,
    isolate_auto_sources,
) -> None:
    env_file = tmp_path / "explicit.env"
    _write_env(env_file, host="explicit-host", port=4321)

    config = load_database_config(
        env={
            "DATABASE_HOST": "shell-host",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "shell-user",
            "DATABASE_PASSWORD": "shell-secret",
            "DATABASE_NAME": "shell-db",
        },
        env_file=env_file,
    )

    assert config.host == "explicit-host"
    assert config.port == 4321


def test_explicit_env_file_must_exist(tmp_path, isolate_auto_sources) -> None:
    missing = tmp_path / "no.env"

    with pytest.raises(DatabaseConfigError, match="--env-file"):
        load_database_config(env={}, env_file=missing)


def test_user_config_default_path_on_linux_or_macos(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(config_module.sys, "platform", "linux")

    path = config_module.user_database_env_path({})

    assert path == home / ".config" / "neomagi" / "secrets" / "database.env"


def test_user_config_default_path_on_macos(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(config_module.sys, "platform", "darwin")

    path = config_module.user_database_env_path({})

    assert path == home / ".config" / "neomagi" / "secrets" / "database.env"


def test_user_config_path_honors_xdg_config_home(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config_module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(config_module.sys, "platform", "linux")
    custom = tmp_path / "xdg"

    path = config_module.user_database_env_path({"XDG_CONFIG_HOME": str(custom)})

    assert path == custom / "neomagi" / "secrets" / "database.env"


def test_user_config_path_uses_appdata_on_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config_module.sys, "platform", "win32")
    appdata = tmp_path / "AppData"

    path = config_module.user_database_env_path({"APPDATA": str(appdata)})

    assert path == appdata / "neomagi" / "secrets" / "database.env"


def test_user_config_dotenv_resolves_when_present(
    tmp_path,
    monkeypatch,
) -> None:
    user_path = tmp_path / "userconfig" / "secrets" / "database.env"
    user_path.parent.mkdir(parents=True)
    _write_env(user_path, host="user-host", port=7777)
    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: user_path,
    )

    config = load_database_config(env={})

    assert config.host == "user-host"
    assert config.port == 7777


def test_user_config_dotenv_incomplete_fails_fast(
    tmp_path,
    monkeypatch,
) -> None:
    user_path = tmp_path / "userconfig" / "secrets" / "database.env"
    user_path.parent.mkdir(parents=True)
    user_path.write_text("DATABASE_HOST=only-host\n", encoding="utf-8")
    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: user_path,
    )

    with pytest.raises(DatabaseConfigError, match="missing required keys"):
        load_database_config(env={})


def test_user_config_dotenv_does_not_pollute_neomagi_env_file(
    tmp_path,
    monkeypatch,
) -> None:
    """NEOMAGI_ENV_FILE is explicit; user config must not silently fill gaps."""

    user_path = tmp_path / "userconfig" / "secrets" / "database.env"
    user_path.parent.mkdir(parents=True)
    _write_env(user_path, host="user-host")
    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: user_path,
    )
    pointed = tmp_path / "incomplete.env"
    pointed.write_text("DATABASE_HOST=pointed-host\n", encoding="utf-8")

    with pytest.raises(DatabaseConfigError, match="NEOMAGI_ENV_FILE"):
        load_database_config(env={"NEOMAGI_ENV_FILE": str(pointed)})


def test_app_root_dotenv_path_finds_repo_root(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    module_dir = repo / "packages" / "magipi" / "src" / "storage"
    module_dir.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname='neomagi'\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "__file__", str(module_dir / "config.py"))

    assert config_module._app_root_dotenv_path() == repo / ".env"


def test_app_root_dotenv_path_returns_none_outside_repo(tmp_path, monkeypatch) -> None:
    package_dir = tmp_path / "venv" / "site-packages" / "storage"
    package_dir.mkdir(parents=True)
    monkeypatch.setattr(config_module, "__file__", str(package_dir / "config.py"))

    assert config_module._app_root_dotenv_path() is None


def test_repo_dotenv_used_when_no_user_config(
    tmp_path,
    monkeypatch,
) -> None:
    user_path = tmp_path / "userconfig" / "secrets" / "database.env"  # not present
    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: user_path,
    )
    repo_env = tmp_path / "repo.env"
    _write_env(repo_env, host="repo-host", port=8888)
    monkeypatch.setattr(config_module, "_app_root_dotenv_path", lambda: repo_env)

    config = load_database_config(env={})

    assert config.host == "repo-host"
    assert config.port == 8888


def test_workspace_dotenv_is_never_read(
    tmp_path,
    monkeypatch,
    isolate_auto_sources,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text(
        "DATABASE_HOST=workspace-host\nDATABASE_PORT=5432\n"
        "DATABASE_USER=u\nDATABASE_PASSWORD=p\nDATABASE_NAME=n\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    with pytest.raises(DatabaseConfigError, match="missing database configuration"):
        load_database_config(env={})


def test_resolve_returns_source_for_shell_env(isolate_auto_sources) -> None:
    _, source = resolve_database_config(
        env={
            "DATABASE_HOST": "db.local",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "neo",
            "DATABASE_PASSWORD": "secret",
            "DATABASE_NAME": "neomagi",
        },
    )

    assert source.kind == "env"
    assert source.format() == "source=env"


def test_resolve_returns_source_for_explicit_file(
    tmp_path,
    isolate_auto_sources,
) -> None:
    env_file = tmp_path / "explicit.env"
    _write_env(env_file)

    _, source = resolve_database_config(env={}, env_file=env_file)

    assert source.kind == "file"
    assert source.label == str(env_file)
    assert source.format() == f"source=file:{env_file}"


def test_describe_returns_source_for_user_config(tmp_path, monkeypatch) -> None:
    user_path = tmp_path / "userconfig" / "secrets" / "database.env"
    user_path.parent.mkdir(parents=True)
    _write_env(user_path)
    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: user_path,
    )

    source = describe_database_config_source(env={})

    assert source.kind == "file"
    assert source.label == str(user_path)


def test_would_fall_back_reports_user_config_when_present(
    tmp_path,
    monkeypatch,
) -> None:
    user_path = tmp_path / "userconfig" / "secrets" / "database.env"
    user_path.parent.mkdir(parents=True)
    _write_env(user_path)
    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: user_path,
    )

    fallback = would_fall_back_to(
        env={
            "DATABASE_HOST": "shell",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "u",
            "DATABASE_PASSWORD": "p",
            "DATABASE_NAME": "n",
        },
    )

    assert fallback is not None
    assert str(user_path) in fallback


def test_would_fall_back_reports_neomagi_env_file(
    tmp_path,
    isolate_auto_sources,
) -> None:
    pointed = tmp_path / "explicit.env"
    _write_env(pointed)

    fallback = would_fall_back_to(
        env={
            "DATABASE_HOST": "shell",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "u",
            "DATABASE_PASSWORD": "p",
            "DATABASE_NAME": "n",
            "NEOMAGI_ENV_FILE": str(pointed),
        },
    )

    assert fallback is not None
    assert "NEOMAGI_ENV_FILE" in fallback
    assert "(missing)" not in fallback


def test_would_fall_back_annotates_missing_neomagi_env_file(
    tmp_path,
    isolate_auto_sources,
) -> None:
    fallback = would_fall_back_to(
        env={
            "DATABASE_HOST": "shell",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "u",
            "DATABASE_PASSWORD": "p",
            "DATABASE_NAME": "n",
            "NEOMAGI_ENV_FILE": str(tmp_path / "no-such.env"),
        },
    )

    assert fallback is not None
    assert "NEOMAGI_ENV_FILE" in fallback
    assert "(missing)" in fallback


def test_would_fall_back_annotates_missing_user_config(
    tmp_path,
    isolate_auto_sources,
) -> None:
    fallback = would_fall_back_to(
        env={
            "DATABASE_HOST": "shell",
            "DATABASE_PORT": "5432",
            "DATABASE_USER": "u",
            "DATABASE_PASSWORD": "p",
            "DATABASE_NAME": "n",
        },
    )

    assert fallback is not None
    assert "(missing)" in fallback


def test_read_env_template_has_required_keys() -> None:
    body = read_env_template()
    for key in (
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_NAME",
        "DATABASE_SCHEMA",
    ):
        assert key in body
    assert "change-me" in body


def test_module_has_sys_alias_for_monkeypatching() -> None:
    """The module re-exports ``sys`` so platform tests can monkeypatch it."""

    assert config_module.sys is sys

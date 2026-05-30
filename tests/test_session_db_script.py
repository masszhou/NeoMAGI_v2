from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import scripts.session_db as session_db


@dataclass(frozen=True)
class _FakeConfig:
    schema: str = "neomagi"


class _FakeConnection:
    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _stub_database(monkeypatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def fake_load_database_config(**kwargs: object) -> _FakeConfig:
        seen.update(kwargs)
        return _FakeConfig()

    monkeypatch.setattr(session_db, "load_database_config", fake_load_database_config)
    monkeypatch.setattr(session_db, "connect_database", lambda config: _FakeConnection())
    monkeypatch.setattr(session_db, "ensure_schema", lambda conn, config: None)
    monkeypatch.setattr(session_db, "_print_status", lambda conn, schema, schema_name: None)
    return seen


def test_session_db_uses_normal_config_resolution_by_default(monkeypatch) -> None:
    seen = _stub_database(monkeypatch)

    assert session_db.main(["ensure"]) == 0

    assert seen == {"env_file": None}


def test_session_db_passes_explicit_env_file(monkeypatch, tmp_path: Path) -> None:
    seen = _stub_database(monkeypatch)
    env_file = tmp_path / "database.env"

    assert session_db.main(["--env-file", str(env_file), "status"]) == 0

    assert seen == {"env_file": env_file}

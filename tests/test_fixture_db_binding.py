from types import SimpleNamespace

import pytest

from tgw.development.fixture_db_binding import (
    FixtureDatabaseBindingError, _explicit_dsn, initialize_fixture_database,
)


DSN = "dbname=tgw_lib_dev_state_machine user=tigwadev"


class _Cursor:
    def execute(self, _sql):
        pass

    def fetchone(self):
        return ("tgw_lib_dev_state_machine", "tigwadev", None)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def cursor(self):
        return _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _WrongRoleCursor(_Cursor):
    def fetchone(self):
        return ("tgw_lib_dev_state_machine", "codex", None)


class _WrongRoleConnection(_Connection):
    def cursor(self):
        return _WrongRoleCursor()


def _config(tmp_path, value=DSN):
    path = tmp_path / "tgw-api-config.json"
    path.write_text('{"postgres_dsn": ' + repr(value).replace("'", '"') + "}\n")
    return path


def test_explicit_configured_dsn_is_required(tmp_path):
    with pytest.raises(FixtureDatabaseBindingError, match="config is missing"):
        _explicit_dsn(tmp_path / "absent.json")
    path = tmp_path / "empty.json"
    path.write_text("{}")
    with pytest.raises(FixtureDatabaseBindingError, match="explicit postgres_dsn"):
        _explicit_dsn(path)
    assert _explicit_dsn(_config(tmp_path)) == DSN


def test_configured_dsn_initializes_both_adapters(monkeypatch, tmp_path):
    calls = []
    todo = SimpleNamespace(init=lambda dsn: calls.append(("todo", dsn)))
    queue = SimpleNamespace(init=lambda dsn: calls.append(("queue", dsn)))
    monkeypatch.setattr("tgw.development.fixture_db_binding.pwd.getpwuid", lambda _uid: SimpleNamespace(pw_name="tigwadev"))
    monkeypatch.setattr("tgw.development.fixture_db_binding.importlib.import_module", lambda name: todo if name == "tgw.todo" else queue)
    result = initialize_fixture_database(config_path=_config(tmp_path), connect=lambda _dsn: _Connection())
    assert result["database"] == "tgw_lib_dev_state_machine"
    assert calls == [("todo", DSN), ("queue", DSN)]


def test_wrong_identity_or_target_refuses_before_adapter_initialization(monkeypatch, tmp_path):
    monkeypatch.setattr("tgw.development.fixture_db_binding.pwd.getpwuid", lambda _uid: SimpleNamespace(pw_name="codex"))
    with pytest.raises(FixtureDatabaseBindingError, match="write identity"):
        initialize_fixture_database(config_path=_config(tmp_path), connect=lambda _dsn: pytest.fail("must not connect"))
    monkeypatch.setattr("tgw.development.fixture_db_binding.pwd.getpwuid", lambda _uid: SimpleNamespace(pw_name="tigwadev"))
    with pytest.raises(FixtureDatabaseBindingError, match="configured development target"):
        initialize_fixture_database(config_path=_config(tmp_path, "dbname=state_machine user=tgw"), connect=lambda _dsn: pytest.fail("must not connect"))
    with pytest.raises(FixtureDatabaseBindingError, match="connection is not"):
        initialize_fixture_database(config_path=_config(tmp_path), connect=lambda _dsn: _WrongRoleConnection())

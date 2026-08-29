from types import SimpleNamespace

import pytest

from tgw.development.fixture_db_binding import (
    FixtureDatabaseBindingError,
    _explicit_dsn,
    fixture_worker_config,
    initialize_fixture_database,
)

DSN = "dbname=tgw_lib_dev_state_machine user=tgw_coding"
UNIVERSAL_ROLE = "tgw_coding"


class _CodingGroup:
    gr_name = "tgw-coders"
    gr_gid = 983
    gr_mem = ("db", "codex", "claude", "deepseek")


class _Cursor:
    def __init__(self, columns=("reasoning", "status_note")):
        self.columns = columns
        self.query = ""

    def execute(self, sql, _params=None):
        self.query = sql

    def fetchone(self):
        return ("tgw_lib_dev_state_machine", UNIVERSAL_ROLE, None)

    def fetchall(self):
        return [(column,) for column in self.columns]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self, columns=("reasoning", "status_note")):
        self.columns = columns

    def cursor(self):
        return _Cursor(self.columns)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _WrongRoleCursor(_Cursor):
    def fetchone(self):
        return ("tgw_lib_dev_state_machine", "db", None)


class _WrongRoleConnection(_Connection):
    def cursor(self):
        return _WrongRoleCursor()


def _config(tmp_path, value=DSN):
    path = tmp_path / "tgw-api-config.json"
    path.write_text('{"postgres_dsn": ' + repr(value).replace("'", '"') + "}\n")
    return path


def _member(monkeypatch, actor="codex"):
    monkeypatch.setattr(
        "tgw.development.fixture_db_binding.pwd.getpwuid",
        lambda _uid: SimpleNamespace(pw_name=actor),
    )
    monkeypatch.setattr(
        "tgw.development.fixture_db_binding.grp.getgrnam",
        lambda _name: _CodingGroup(),
    )


def test_explicit_configured_dsn_is_required(tmp_path):
    with pytest.raises(FixtureDatabaseBindingError, match="config is missing"):
        _explicit_dsn(tmp_path / "absent.json")
    path = tmp_path / "empty.json"
    path.write_text("{}")
    with pytest.raises(FixtureDatabaseBindingError, match="explicit postgres_dsn"):
        _explicit_dsn(path)
    assert _explicit_dsn(_config(tmp_path)) == DSN


def test_fixture_worker_config_keeps_the_preflighted_local_dsn(monkeypatch):
    monkeypatch.setenv("TGW_TODO_DSN", DSN)
    config = fixture_worker_config({"commands": {}})
    assert config == {"postgres_dsn": DSN, "coding": {"commands": {}}}
    with pytest.raises(FixtureDatabaseBindingError, match="coding configuration"):
        fixture_worker_config([])
    monkeypatch.delenv("TGW_TODO_DSN")
    with pytest.raises(FixtureDatabaseBindingError, match="preflighted database"):
        fixture_worker_config({"commands": {}})


def test_configured_dsn_initializes_both_adapters(monkeypatch, tmp_path):
    calls = []
    todo = SimpleNamespace(init=lambda dsn: calls.append(("todo", dsn)))
    queue = SimpleNamespace(init=lambda dsn: calls.append(("queue", dsn)))
    _member(monkeypatch, actor="deepseek")
    monkeypatch.setattr(
        "tgw.development.fixture_db_binding.importlib.import_module",
        lambda name: todo if name == "tgw.todo" else queue,
    )
    result = initialize_fixture_database(config_path=_config(tmp_path), connect=lambda _dsn: _Connection())
    assert result["database"] == "tgw_lib_dev_state_machine"
    assert result["role"] == UNIVERSAL_ROLE
    assert calls == [("todo", DSN), ("queue", DSN)]


def test_wrong_identity_or_target_refuses_before_adapter_initialization(monkeypatch, tmp_path):
    _member(monkeypatch, actor="mallory")
    with pytest.raises(FixtureDatabaseBindingError, match="tgw-coders Unix identity"):
        initialize_fixture_database(config_path=_config(tmp_path), connect=lambda _dsn: pytest.fail("must not connect"))
    _member(monkeypatch, actor="codex")
    with pytest.raises(FixtureDatabaseBindingError, match="configured development target"):
        initialize_fixture_database(config_path=_config(tmp_path, "dbname=state_machine user=tgw"), connect=lambda _dsn: pytest.fail("must not connect"))
    with pytest.raises(FixtureDatabaseBindingError, match="does not name the configured"):
        initialize_fixture_database(config_path=_config(tmp_path, "dbname=tgw_lib_dev_state_machine"), connect=lambda _dsn: pytest.fail("must not connect"))
    with pytest.raises(FixtureDatabaseBindingError, match="connection is not"):
        initialize_fixture_database(config_path=_config(tmp_path), connect=lambda _dsn: _WrongRoleConnection())


@pytest.mark.parametrize(
    "columns, missing",
    [
        (("status_note",), "reasoning"),
        (("reasoning",), "status_note"),
    ],
)
def test_fixture_preflight_refuses_missing_todo_schema_columns(monkeypatch, tmp_path, columns, missing):
    _member(monkeypatch, actor="codex")
    monkeypatch.setattr(
        "tgw.development.fixture_db_binding.importlib.import_module",
        lambda _name: pytest.fail("must not import adapters"),
    )
    with pytest.raises(FixtureDatabaseBindingError, match=missing):
        initialize_fixture_database(config_path=_config(tmp_path), connect=lambda _dsn: _Connection(columns))

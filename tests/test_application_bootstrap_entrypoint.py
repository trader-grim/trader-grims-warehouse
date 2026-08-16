import io
from pathlib import Path
from types import SimpleNamespace

import pytest

import tgw.application_bootstrap_entrypoint as entrypoint


def test_w09_entrypoint_is_no_argument_and_uses_only_fixed_config(monkeypatch):
    called = []
    monkeypatch.setattr(entrypoint, "execute_from_fixed_config", lambda: called.append(entrypoint.CONFIG_PATH) or {"outcome": "succeeded"})
    output = io.BytesIO()
    monkeypatch.setattr(entrypoint.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(entrypoint.sys, "argv", ["tgw-w09-application-bootstrap"])
    assert entrypoint.main() == 0
    assert called == [entrypoint.CONFIG_PATH]
    assert output.getvalue() == b'{"outcome":"succeeded"}\n'

    monkeypatch.setattr(entrypoint.sys, "argv", ["tgw-w09-application-bootstrap", "neighbor"])
    with pytest.raises(SystemExit, match="accepts no arguments"):
        entrypoint.main()


def test_w09_entrypoint_is_published_by_candidate_package():
    assert 'tgw-w09-application-bootstrap = "tgw.application_bootstrap_entrypoint:main"' in Path(
        "pyproject.toml",
    ).read_text(encoding="utf-8")

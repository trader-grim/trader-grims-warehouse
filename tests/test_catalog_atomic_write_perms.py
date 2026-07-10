"""Regression tests for tgw.catalog's atomic_write_json/atomic_write_csv mode
handling (session 41). NamedTemporaryFile creates its file at 0600 regardless
of the parent directory's ACL/umask — confirmed live to silently revert
shared catalog files to owner-only on every rebuild. New files must default
to group-writable; rewrites must preserve whatever mode was already there.
"""

from __future__ import annotations

import json
import os

from tgw import catalog


def test_json_new_file_defaults_to_group_writable(tmp_path):
    path = tmp_path / "out.json"
    catalog.atomic_write_json(path, {"a": 1})
    assert (path.stat().st_mode & 0o777) == 0o660
    assert json.loads(path.read_text())["a"] == 1


def test_json_preserves_existing_mode(tmp_path):
    path = tmp_path / "out.json"
    catalog.atomic_write_json(path, {"a": 1})
    os.chmod(path, 0o644)
    catalog.atomic_write_json(path, {"a": 2})
    assert (path.stat().st_mode & 0o777) == 0o644


def test_csv_new_file_defaults_to_group_writable(tmp_path):
    path = tmp_path / "out.csv"
    catalog.atomic_write_csv(path, [{"sku": "tgw1"}], fieldnames=["sku"])
    assert (path.stat().st_mode & 0o777) == 0o660


def test_csv_preserves_existing_mode(tmp_path):
    path = tmp_path / "out.csv"
    catalog.atomic_write_csv(path, [{"sku": "tgw1"}], fieldnames=["sku"])
    os.chmod(path, 0o640)
    catalog.atomic_write_csv(path, [{"sku": "tgw2"}], fieldnames=["sku"])
    assert (path.stat().st_mode & 0o777) == 0o640


def test_json_write_failure_does_not_leak_tmp_file(tmp_path, monkeypatch):
    # Code-review follow-up (audit#1143 #1239): NamedTemporaryFile(delete=False)
    # never auto-cleans on an error mid-write (e.g. a non-serializable value,
    # or ENOSPC) -- without cleanup, a failed write leaks a tmp file into
    # path.parent forever.
    path = tmp_path / "out.json"

    class Unserializable:
        pass

    import pytest

    with pytest.raises(TypeError):
        catalog.atomic_write_json(path, {"bad": Unserializable()})

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []

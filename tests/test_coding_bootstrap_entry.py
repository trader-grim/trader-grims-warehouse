"""`bin/tgw-coding-bootstrap` — the hash-verified privileged Doctor entry point.

LEAF-11-1.DELETE-APPARATUS removed the `coding-bootstrap` Doctor op; the
no-`--repair` form must now run `doctor check`, not the deleted subcommand.
"""
from __future__ import annotations

import argparse
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "bin/tgw-coding-bootstrap"


def _load():
    loader = SourceFileLoader("tgw_coding_bootstrap_entry", str(_SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def entry():
    return _load()


def _ns(**kw) -> argparse.Namespace:
    base = {"commit": "a" * 40, "repair": None, "review_evidence": None, "live_canary": False}
    base.update(kw)
    return argparse.Namespace(**base)


def test_no_repair_runs_doctor_check_not_the_deleted_op(entry) -> None:
    args = entry._doctor_arguments(_ns())
    assert args == ["check", "--full"]
    assert "coding-bootstrap" not in args


def test_repair_still_targets_doctor_repair_with_commit(entry) -> None:
    args = entry._doctor_arguments(_ns(repair="context"))
    assert args == ["repair", "context", "--commit", "a" * 40, "--json"]


def test_commit_is_still_required(entry) -> None:
    with pytest.raises(SystemExit):
        entry.main(["--repair", "context"])

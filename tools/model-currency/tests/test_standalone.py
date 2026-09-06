"""Guard the hard boundary: this package must never depend on ``tgw`` or on
any third-party library. These tests are also expected to run in an
environment where ``tgw`` is not importable at all.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent / "model_currency"

# Everything model_currency is allowed to import (plus its own submodules and
# the standard library).
_STDLIB = set(sys.stdlib_module_names)
_ALLOWED_TOP = _STDLIB | {"model_currency"}


def _module_files():
    return sorted(PKG_ROOT.rglob("*.py"))


def test_only_stdlib_and_self_imports():
    offenders: list[str] = []
    for path in _module_files():
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import within the package
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".", 1)[0]
                if top and top not in _ALLOWED_TOP:
                    offenders.append(f"{path.name}: imports {name!r}")
    assert not offenders, "non-stdlib / non-self imports found:\n" + "\n".join(offenders)


def test_no_tgw_import_statements():
    for path in _module_files():
        text = path.read_text("utf-8")
        assert "import tgw" not in text, path
        assert "from tgw" not in text, path


def test_importing_package_does_not_pull_tgw():
    # model_currency is already imported by the test session; assert the world
    # it dragged in is clean.
    import model_currency  # noqa: F401

    assert "tgw" not in sys.modules


def test_runs_with_tgw_unimportable(monkeypatch):
    """Simulate `tgw` being entirely absent and prove the tool still works."""
    import builtins
    import importlib

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "tgw" or name.startswith("tgw."):
            raise ModuleNotFoundError("No module named 'tgw'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)

    for mod in ("model_currency", "model_currency.cli", "model_currency.catalog",
                "model_currency.filters", "model_currency.ranking", "model_currency.cache",
                "model_currency.fallback", "model_currency.sources.openrouter",
                "model_currency.sources.groq", "model_currency.sources.models_dev"):
        importlib.reload(importlib.import_module(mod))

    from model_currency import fallback
    from model_currency.ranking import rank

    ranked = rank(fallback.load())
    assert ranked and all(m.free for m in ranked)

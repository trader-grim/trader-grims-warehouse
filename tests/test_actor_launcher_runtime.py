import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "tgw_actor_startup.py"
ACTOR_RUNTIME = Path("/opt/TGW/.venvs/controller/bin/python3")


def test_actor_launcher_uses_the_installed_tgw_runtime():
    """The actor accounts' ambient Python lacks the signing dependencies."""
    first_line = LAUNCHER.read_text(encoding="utf-8").splitlines()[0]

    assert first_line == f"#!{ACTOR_RUNTIME}"
    assert ACTOR_RUNTIME.is_absolute()


def _launcher_module():
    specification = importlib.util.spec_from_file_location(
        "tgw_test_actor_launcher", LAUNCHER
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_stable_launcher_bootstraps_the_bound_protected_source(tmp_path):
    source = tmp_path / "retained-source"
    module = source / "src/tgw/actor_startup.py"
    module.parent.mkdir(parents=True)
    module.write_text("BOUND = True\n", encoding="utf-8")
    stable = tmp_path / "stable/bin/tgw-actor"
    stable.parent.mkdir(parents=True)
    stable.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    bindings = tmp_path / "bindings"
    bindings.mkdir()
    binding = bindings / "codex-startup.json"
    binding.write_text(
        json.dumps(
            {
                "schema": "tgw-actor-startup-binding/v3",
                "actor": "codex",
                "context_source_root": str(source),
            }
        ),
        encoding="utf-8",
    )
    for path in (source, source / "src", module.parent, stable.parent, bindings):
        path.chmod(0o555)
    for path in (module, stable, binding):
        path.chmod(0o444)

    launcher = _launcher_module()
    try:
        assert launcher._bootstrap_source_root(
            stable,
            "codex",
            binding_root=bindings,
            trusted_uid=os.geteuid(),
        ) == source
    finally:
        for path in (source, source / "src", module.parent, stable.parent, bindings):
            path.chmod(0o755)
        for path in (module, stable, binding):
            path.chmod(0o644)


def test_stable_launcher_rejects_a_writable_bound_source(tmp_path):
    source = tmp_path / "retained-source"
    module = source / "src/tgw/actor_startup.py"
    module.parent.mkdir(parents=True)
    module.write_text("BOUND = True\n", encoding="utf-8")
    stable = tmp_path / "stable/bin/tgw-actor"
    stable.parent.mkdir(parents=True)
    stable.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    bindings = tmp_path / "bindings"
    bindings.mkdir()
    binding = bindings / "codex-startup.json"
    binding.write_text(
        json.dumps(
            {
                "schema": "tgw-actor-startup-binding/v3",
                "actor": "codex",
                "context_source_root": str(source),
            }
        ),
        encoding="utf-8",
    )
    source.chmod(0o775)
    (source / "src").chmod(0o555)
    module.parent.chmod(0o555)
    module.chmod(0o444)
    binding.chmod(0o444)

    launcher = _launcher_module()
    try:
        with pytest.raises(RuntimeError, match="source is not protected"):
            launcher._bootstrap_source_root(
                stable,
                "codex",
                binding_root=bindings,
                trusted_uid=os.geteuid(),
            )
    finally:
        for path in (source, source / "src", module.parent, stable.parent, bindings):
            path.chmod(0o755)
        for path in (module, stable, binding):
            path.chmod(0o644)

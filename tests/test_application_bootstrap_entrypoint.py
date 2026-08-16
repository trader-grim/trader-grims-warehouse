import hashlib
import io
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import tgw.application_bootstrap_entrypoint as entrypoint


def _binding(path: Path):
    raw = path.read_bytes()
    metadata = path.stat()
    return {
        "path": str(path),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "size": metadata.st_size,
    }


def test_w09_entrypoint_is_no_argument_and_uses_only_fixed_config(monkeypatch):
    called = []
    monkeypatch.setattr(entrypoint, "_isolated_runtime", lambda: True)
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


def test_w09_controller_rejects_direct_nonisolated_module_execution(monkeypatch):
    execute = Mock()
    monkeypatch.setattr(entrypoint, "execute_from_fixed_config", execute)
    monkeypatch.setattr(entrypoint, "_isolated_runtime", lambda: False)
    monkeypatch.setattr(entrypoint.sys, "argv", ["controller.py"])
    with pytest.raises(SystemExit, match="exact isolated launcher"):
        entrypoint.main()
    execute.assert_not_called()


def test_w09_entrypoint_is_published_by_candidate_package():
    assert 'script-files = ["scripts/tgw-w09-application-bootstrap"]' in Path("pyproject.toml").read_text(encoding="utf-8")
    launcher = Path("scripts/tgw-w09-application-bootstrap").read_text(encoding="utf-8")
    assert "from tgw." not in launcher
    assert "import tgw." not in launcher


def test_w09_entrypoint_imports_no_tgw_code_before_runtime_verification():
    source = Path("src/tgw/application_bootstrap_entrypoint.py").read_text(
        encoding="utf-8",
    )
    prefix = source.split("def execute_from_fixed_config", 1)[0]
    assert "from tgw." not in prefix
    assert "import tgw." not in prefix


def test_controller_runtime_is_held_and_rejects_in_place_source_change(
    tmp_path,
    monkeypatch,
):
    launcher = tmp_path / "launcher"
    python = tmp_path / "python"
    controller = tmp_path / "controller.py"
    for path, raw in (
        (launcher, b"launcher"),
        (python, b"python"),
        (controller, b"controller"),
    ):
        path.write_bytes(raw)
        path.chmod(0o500)
    files = [_binding(controller)]
    manifest = {
        "schema": entrypoint.RUNTIME_SCHEMA,
        "files": files,
    }
    manifest["manifest_sha256"] = "sha256:" + hashlib.sha256(entrypoint._canonical(manifest)).hexdigest()
    manifest_path = tmp_path / "runtime.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    manifest_path.chmod(0o400)
    runtime = {
        "launcher": _binding(launcher),
        "python": _binding(python),
        "entrypoint": _binding(controller),
        "manifest": _binding(manifest_path),
    }
    monkeypatch.setattr(entrypoint, "_protected_ancestors", lambda _path: None)
    monkeypatch.setattr(entrypoint, "__file__", str(controller))
    monkeypatch.setattr(entrypoint.sys, "argv", [str(launcher)])

    parsed, artifacts, evidence = entrypoint._hold_controller_runtime(
        runtime,
        require_launcher=True,
    )
    try:
        assert evidence.startswith("w09-controller-runtime:sha256:")
        assert parsed["manifest_sha256"] == manifest["manifest_sha256"]
        os.chmod(controller, 0o700)
        controller.write_bytes(b"neighbor runtime")
        with pytest.raises(OSError, match="runtime artifact changed"):
            entrypoint._revalidate_runtime_artifact(artifacts[-1])
    finally:
        for _path, fd, _raw, _identity in artifacts:
            os.close(fd)


def test_controller_runtime_closes_first_artifact_when_second_hold_fails(
    monkeypatch,
):
    opened: list[int] = []

    def injected_hold(_binding, *, label, max_bytes=64 * 1024 * 1024):
        del label, max_bytes
        if opened:
            raise OSError("injected second hold failure")
        fd = os.open("/dev/null", os.O_RDONLY)
        opened.append(fd)
        return Path("/dev/null"), fd, b"", (0, 0, 0, 0, 0, 0)

    monkeypatch.setattr(entrypoint, "_hold_runtime_artifact", injected_hold)
    runtime = {name: {} for name in ("launcher", "python", "entrypoint", "manifest")}
    with pytest.raises(OSError, match="second hold"):
        entrypoint._hold_controller_runtime(runtime, require_launcher=False)
    with pytest.raises(OSError):
        os.fstat(opened[0])

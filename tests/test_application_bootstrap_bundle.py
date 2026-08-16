import hashlib
import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

import tgw.application_bootstrap_bundle as bundle_module
from tgw.application_bootstrap_bundle import (
    ControllerBundleError,
    _bundle_from_archive,
    _write_once,
    produce_controller_bundle,
)
from tgw.application_deployment_contract import PROJECTION_PATH, ProtectedGitObjectReader

REQUIRED = (
    "application_bootstrap_entrypoint.py",
    "application_deployment_contract.py",
    "application_release_provider.py",
    "bootstrap_authority.py",
    "candidate_receipt_sink.py",
    "deployment_runtime.py",
    "effect_completion_store.py",
    "effect_handlers.py",
)
PROMPTCRAFT_REQUIRED = ("__init__.py", "core.py", "handoff.py")


def _archive(*, unsafe=False, bytecode=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:") as archive:
        entries = {
            PROJECTION_PATH: b'{"projection":"exact"}',
            "src/tgw/w09_controller_launcher.c": b"int main(void) { return 0; }\n",
            **{f"src/tgw/{name}": f"# {name}\n".encode() for name in REQUIRED},
            **{f"agent-services/providers/promptcraft/promptcraft/{name}": f"# {name}\n".encode() for name in PROMPTCRAFT_REQUIRED},
        }
        if unsafe:
            entries["../escape.py"] = b"neighbor"
        if bytecode:
            entries["src/tgw/__pycache__/neighbor.pyc"] = b"bytecode"
        for name, raw in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o444
            archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


def test_controller_bundle_is_deterministic_source_only_and_projection_bound():
    archive = _archive()
    first, projection, launcher_source, launcher_raw = _bundle_from_archive(archive)
    second, repeated_projection, repeated_launcher_source, repeated_launcher_raw = _bundle_from_archive(archive)

    assert first == second
    assert projection == repeated_projection == "sha256:" + hashlib.sha256(b'{"projection":"exact"}').hexdigest()
    assert launcher_source == repeated_launcher_source
    assert launcher_raw == repeated_launcher_raw
    with zipfile.ZipFile(io.BytesIO(first)) as bundle:
        assert "__main__.py" in bundle.namelist()
        assert all(not name.endswith(".pyc") for name in bundle.namelist())
        assert bundle.read("__main__.py").startswith(b"from tgw.application_bootstrap_entrypoint import main")
        assert set(PROMPTCRAFT_REQUIRED).issubset({Path(name).name for name in bundle.namelist() if name.startswith("promptcraft/")})


def test_controller_bundle_rejects_archive_traversal_before_materialization():
    with pytest.raises(ControllerBundleError, match="unsafe entry"):
        _bundle_from_archive(_archive(unsafe=True))


def test_controller_bundle_rejects_preexisting_bytecode():
    with pytest.raises(ControllerBundleError, match="contains bytecode"):
        _bundle_from_archive(_archive(bytecode=True))


def test_write_once_removes_partial_file_after_short_write(tmp_path, monkeypatch):
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(bundle_module.os, "write", lambda _fd, _raw: 0)
    try:
        with pytest.raises(OSError, match="short controller artifact write"):
            _write_once(root_fd, "partial", b"exact", 0o400)
        assert not (tmp_path / "partial").exists()
    finally:
        os.close(root_fd)


def test_producer_closes_output_root_when_source_resolution_fails(
    tmp_path,
    monkeypatch,
):
    output_root = tmp_path / "protected"
    output_root.mkdir(mode=0o700)
    source = object.__new__(ProtectedGitObjectReader)

    def fail_identity(_self, _commit):
        raise OSError("injected Git identity failure")

    opened: list[int] = []
    real_open_root = bundle_module._open_protected_root

    def track_root(root: Path, trusted_uid: int):
        fd, identity = real_open_root(root, trusted_uid)
        opened.append(fd)
        return fd, identity

    monkeypatch.setattr(ProtectedGitObjectReader, "identity", fail_identity)
    monkeypatch.setattr(bundle_module, "_open_protected_root", track_root)
    with pytest.raises(OSError, match="Git identity"):
        produce_controller_bundle(
            source=source,
            commit="a" * 40,
            output_root=output_root,
            application_candidate={
                "commit": "b" * 40,
                "tree": "c" * 40,
                "archive_sha256": "sha256:" + "d" * 64,
                "projection_sha256": "sha256:" + "e" * 64,
            },
            trusted_uid=os.getuid(),
        )
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])

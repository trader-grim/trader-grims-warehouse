import hashlib
import io
import json
import os
import stat
import subprocess
import sys
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
        "dev": metadata.st_dev,
        "ino": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "nlink": metadata.st_nlink,
        "size": metadata.st_size,
    }


def _tree_binding(path: Path):
    metadata = path.stat()
    return {
        "path": str(path),
        "sha256": entrypoint._tree_digest(
            path,
            trusted_uid=metadata.st_uid,
            trusted_gid=metadata.st_gid,
        ),
        "dev": metadata.st_dev,
        "ino": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "nlink": metadata.st_nlink,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _materialized_identity(path: Path):
    metadata = path.stat()
    return [
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
    ]


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
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "tgw-w09-application-bootstrap =" not in project
    assert "script-files" not in project
    launcher = Path("src/tgw/w09_controller_launcher.c").read_text(encoding="utf-8")
    assert "fexecve(python_fd" in launcher
    assert "application-bootstrap-runtime.fds" in launcher


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
    bundle = tmp_path / "controller.pyz"
    for path, raw in (
        (launcher, b"launcher"),
        (python, b"python"),
        (bundle, b"controller bundle"),
    ):
        path.write_bytes(raw)
        path.chmod(0o500)
    launcher_config = tmp_path / "runtime.fds"
    closure = tmp_path / "runtime.closure"
    receipt_path = tmp_path / "runtime.receipt.json"
    runtime_tree = tmp_path / "stdlib"
    runtime_tree.mkdir(mode=0o700)
    (runtime_tree / "module.py").write_bytes(b"VALUE = 1\n")
    (runtime_tree / "module.py").chmod(0o400)
    runtime_tree.chmod(0o500)
    files = [{**_binding(python), "elf": None}]
    trees = [_tree_binding(runtime_tree)]
    closure.write_bytes(entrypoint._preexec_closure(files, trees))
    closure.chmod(0o400)
    launcher_config.write_text(
        "schema=tgw-w09-controller-launch-fds/v1\n"
        f"python={python}\n"
        f"bundle={bundle}\n"
        f"closure={closure}\n"
        f"receipt={receipt_path}\n"
    )
    launcher_config.chmod(0o400)
    manifest = {
        "schema": entrypoint.RUNTIME_SCHEMA,
        "files": files,
        "trees": trees,
        "import_roots": [str(runtime_tree)],
    }
    manifest["manifest_sha256"] = "sha256:" + hashlib.sha256(entrypoint._canonical(manifest)).hexdigest()
    manifest_path = tmp_path / "runtime.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    manifest_path.chmod(0o400)
    materialization_unsigned = {
        "schema": "tgw-w09-controller-runtime-materialization/v1",
        "controller_source_receipt_sha256": "sha256:" + "1" * 64,
        "application_candidate": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "archive_sha256": "sha256:" + "2" * 64,
            "projection_sha256": "sha256:" + "3" * 64,
        },
        "launcher_build_receipt_sha256": "sha256:" + "4" * 64,
        "launcher": _binding(launcher),
        "python": _binding(python),
        "bundle": _binding(bundle),
        "manifest": {
            **manifest,
            "path": str(manifest_path),
            "content_sha256": _binding(manifest_path)["sha256"],
            "identity": _materialized_identity(manifest_path),
        },
        "closure": {
            "path": str(closure),
            "sha256": _binding(closure)["sha256"],
            "identity": _materialized_identity(closure),
        },
        "launcher_config": {
            "path": str(launcher_config),
            "sha256": _binding(launcher_config)["sha256"],
            "identity": _materialized_identity(launcher_config),
        },
    }
    materialization = {
        **materialization_unsigned,
        "receipt_sha256": "sha256:"
        + hashlib.sha256(entrypoint._canonical(materialization_unsigned)).hexdigest(),
    }
    receipt_path.write_text(json.dumps(materialization, sort_keys=True, separators=(",", ":")))
    receipt_path.chmod(0o400)
    runtime = {
        "launcher": _binding(launcher),
        "python": _binding(python),
        "bundle": _binding(bundle),
        "launcher_config": _binding(launcher_config),
        "closure": _binding(closure),
        "manifest": _binding(manifest_path),
        "receipt": _binding(receipt_path),
    }
    monkeypatch.setattr(entrypoint, "_protected_ancestors", lambda *_args: None)
    execution_fd = os.open(bundle, os.O_RDONLY)
    monkeypatch.setattr(entrypoint.sys, "argv", [f"/proc/self/fd/{execution_fd}"])
    parsed, artifacts, held_trees, evidence = entrypoint._hold_controller_runtime(
        runtime,
        require_launcher=False,
    )
    try:
        assert evidence.startswith("w09-controller-runtime:sha256:")
        assert parsed["manifest_sha256"] == manifest["manifest_sha256"]
        os.chmod(python, 0o700)
        python.write_bytes(b"neighbor runtime")
        with pytest.raises(OSError, match="runtime artifact changed"):
            entrypoint._revalidate_runtime_artifact(artifacts[-1])
    finally:
        for _path, fd, _raw, _identity in artifacts:
            os.close(fd)
        for _path, fd, _digest, _identity, _uid, _gid in held_trees:
            os.close(fd)
        os.close(execution_fd)


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
    runtime = {
        name: {}
        for name in (
            "launcher",
            "python",
            "bundle",
            "launcher_config",
            "closure",
            "manifest",
            "receipt",
        )
    }
    with pytest.raises(OSError, match="second hold"):
        entrypoint._hold_controller_runtime(runtime, require_launcher=False)
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_runtime_artifact_cleanup_postchecks_and_closes_each_fd_once(
    tmp_path,
):
    artifact_path = tmp_path / "runtime"
    artifact_path.write_bytes(b"exact")
    fd = os.open(artifact_path, os.O_RDONLY)
    metadata = os.fstat(fd)
    artifact = (
        artifact_path,
        fd,
        b"exact",
        (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_size,
        ),
    )
    errors: list[Exception] = []
    entrypoint._close_runtime_artifacts([artifact], errors)
    assert errors == []
    with pytest.raises(OSError):
        os.fstat(fd)


def test_runtime_artifact_cleanup_closes_fd_after_failed_postcheck(tmp_path):
    artifact_path = tmp_path / "runtime"
    artifact_path.write_bytes(b"exact")
    fd = os.open(artifact_path, os.O_RDONLY)
    metadata = os.fstat(fd)
    artifact = (
        artifact_path,
        fd,
        b"neighbor",
        (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_size,
        ),
    )
    errors: list[Exception] = []
    entrypoint._close_runtime_artifacts([artifact], errors)
    assert len(errors) == 1
    with pytest.raises(OSError):
        os.fstat(fd)


def test_elf_closure_binds_interpreter_and_needed_native_libraries():
    closure = entrypoint._elf_closure(Path("/proc/self/exe").read_bytes())
    assert closure is not None
    assert closure["pt_interp"].startswith("/")
    assert closure["needed"] == sorted(closure["needed"])
    assert closure["needed"]
    assert entrypoint._elf_closure(b"source-only") is None


def test_runtime_tree_rejects_symlink_even_when_target_exists(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"VALUE = 1\n")
    (tree / "escape.py").symlink_to(outside)
    with pytest.raises(ValueError, match="contains a symlink"):
        entrypoint._tree_digest(
            tree,
            trusted_uid=os.getuid(),
            trusted_gid=os.getgid(),
        )


def test_compiled_native_launcher_passes_exact_held_execution_fds(tmp_path):
    launcher = tmp_path / "controller-launcher"
    bundle = tmp_path / "controller.py"
    closure = tmp_path / "runtime.closure"
    binding = tmp_path / "runtime.fds"
    receipt = tmp_path / "runtime.receipt.json"
    python = Path(sys.executable).resolve(strict=True)
    bundle.write_text(
        "import os,sys\n"
        "assert len(sys.argv) == 1 and sys.argv[0].startswith('/proc/self/fd/')\n"
        "for key in ('TGW_W09_LAUNCHER_FD','TGW_W09_PYTHON_FD',"
        "'TGW_W09_BUNDLE_FD','TGW_W09_LAUNCH_BINDING_FD',"
        "'TGW_W09_CLOSURE_FD','TGW_W09_RUNTIME_RECEIPT_FD'):\n"
        " os.fstat(int(os.environ[key]))\n"
        "print('held-launch-ok')\n"
    )
    bundle.chmod(0o400)
    closure.write_bytes(entrypoint._preexec_closure([{**_binding(python), "elf": None}]))
    closure.chmod(0o400)
    receipt.write_bytes(b"{}")
    receipt.chmod(0o400)
    binding.write_text(
        "schema=tgw-w09-controller-launch-fds/v1\n"
        f"python={python}\n"
        f"bundle={bundle}\n"
        f"closure={closure}\n"
        f"receipt={receipt}\n"
    )
    binding.chmod(0o400)
    subprocess.run(
        [
            "cc",
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-DTGW_TRUSTED_UID={os.getuid()}",
            f'-DBINDING_PATH="{binding}"',
            "-o",
            str(launcher),
            "src/tgw/w09_controller_launcher.c",
        ],
        check=True,
        capture_output=True,
    )
    launcher.chmod(0o500)
    result = subprocess.run(
        [launcher],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "held-launch-ok\n"

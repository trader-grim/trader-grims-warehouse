import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tgw import nix_observer_render_helper as helper
from tgw.nix_observer_render_evaluation import OUTPUTS, SCHEMA, canonical


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _source_files() -> dict[str, bytes]:
    return {
        "flake.lock": b'{"nodes":{},"root":"root","version":7}\n',
        "flake.nix": b"{ outputs = _: {}; }\n",
        "nix/nix-input-observer-launcher.nix": b"{ ... }: {}\n",
        "src/native/tgw_nix_input_observer_launcher.c": b"int main(void) { return 0; }\n",
        "src/tgw/nix_input_observation.py": b"# observer source\n",
        "src/tgw/nix_observer_render_evaluation.py": b"# provider source\n",
    }


def _write_source(root: Path, files: dict[str, bytes]) -> None:
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def _tree_hash(root: Path) -> str:
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["/usr/bin/git", "add", "-f", "-A"], cwd=root, check=True)
    return subprocess.run(["/usr/bin/git", "write-tree"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _archive(path: Path, files: dict[str, bytes], *, commit: str, mutation: str | None = None) -> None:
    pax = {} if mutation == "missing-pax" else {"comment": "f" * 40 if mutation == "wrong-pax" else commit}
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT, pax_headers=pax) as archive:
        directories = {
            helper.ARCHIVE_ROOT,
            *(
                helper.ARCHIVE_ROOT + "/" + str(parent)
                for name in files
                for parent in Path(name).parents
                if str(parent) != "."
            ),
        }
        for directory in sorted(directories, key=lambda item: (item.count("/"), item)):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for name, raw in sorted(files.items()):
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/" + name)
            info.size = len(raw)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(raw))
        if mutation == "duplicate":
            raw = files["flake.lock"]
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/flake.lock")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
        elif mutation == "dotdot":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/../escape")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        elif mutation == "dot":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/./alias")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        elif mutation == "double-slash":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "//alias")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        elif mutation == "dotgit":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/.git/config")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        elif mutation == "symlink":
            info = tarfile.TarInfo(helper.ARCHIVE_ROOT + "/link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        elif mutation == "other-root":
            info = tarfile.TarInfo("other-root")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)


def _request(files: dict[str, bytes], archive: Path, *, tree: str) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": SCHEMA,
        "plan_commit": "a" * 40,
        "source_commit": "b" * 40,
        "source_tree": tree,
        "artifact_ref": "artifact:" + _digest(archive.read_bytes()),
        "archive_sha256": _digest(archive.read_bytes()),
        "flake_lock_sha256": _digest(files["flake.lock"]),
        "flake_sha256": _digest(files["flake.nix"]),
        "module_sha256": _digest(files["nix/nix-input-observer-launcher.nix"]),
        "launcher_source_sha256": _digest(files["src/native/tgw_nix_input_observer_launcher.c"]),
        "observer_source_sha256": _digest(files["src/tgw/nix_input_observation.py"]),
        "provider_sha256": _digest(files["src/tgw/nix_observer_render_evaluation.py"]),
        "host_identity_receipt_sha256": "sha256:" + "5" * 64,
        "systemd_analyze_sha256": "sha256:" + "6" * 64,
        "systemd_analyze_version_stdout_sha256": "sha256:" + "7" * 64,
        "target": "nix-input-observer-rendered-artifacts",
        "system": "x86_64-linux",
        "network_policy": "offline-no-substituters",
        "allow_ifd": False,
        "activate": False,
        "profile_write": False,
        "home_db_write": False,
        "expected_outputs": list(OUTPUTS),
        "expected_metadata_status": "NON_DEPLOYABLE_RENDER_FIXTURE",
        "input_closure_manifest": [
            {
                "node": "nixpkgs",
                "rev": "ac62194c3917d5f474c1a844b6fd6da2db95077d",
                "lock_nar_hash": "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w=",
                "store_path": "/nix/store/11111111111111111111111111111111-source",
                "nar_sha256": "sha256:" + "8" * 64,
            }
        ],
        "input_closure_path_count": 1,
        "systemd_analyze_version": "systemd 257 (257.10)",
        "systemd_analyze_version_stdout_bytes": 32,
        "max_duration_seconds": 60,
        "max_output_bytes": 1024 * 1024,
    }
    value["input_closure_manifest_sha256"] = _digest(canonical(value["input_closure_manifest"]))
    value["request_sha256"] = _digest(canonical(value))
    return value


@pytest.fixture
def source_case(tmp_path):
    files = _source_files()
    source = tmp_path / "source"
    source.mkdir()
    _write_source(source, files)
    tree = _tree_hash(source)
    archive = tmp_path / "source.tar"
    _archive(archive, files, commit="b" * 40)
    request = _request(files, archive, tree=tree)
    helper_source = Path(helper.__file__).read_bytes()
    wire = helper.packet(helper_source, request, archive, tool=Path("/usr/bin/git"))
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    return files, archive, request, helper_source, wire, binding


def _run_bootstrap(wire: bytes, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    program = "_TEST_ONLY_GIT_PATH='/usr/bin/git'\n" + helper.BOOTSTRAP
    return subprocess.run([sys.executable, "-I", "-c", program], input=wire, capture_output=True, check=False, env=env)


def test_actual_subprocess_frame_verifies_source_then_holds_before_executor(source_case):
    _, _, request, _, wire, binding = source_case
    completed = _run_bootstrap(wire, env={**os.environ, "TGW_NIX_RENDER_EXECUTOR": "ambient-command"})
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert helper.validate_terminal(receipt, binding=binding, request=request) == receipt
    assert receipt["schema"] == helper.HOLD_SCHEMA
    assert receipt["outcome"] == helper.HOLD_OUTCOME
    assert receipt["request_sha256"] == request["request_sha256"] == binding.request_sha256
    assert receipt["executor"] == {"installed": False, "reachable_from_request": False, "reachable_from_environment": False}
    assert receipt["cleanup"] == "removed"
    assert receipt["effects"] == helper.EFFECTS
    assert set(receipt["verified_source_files"]) == set(helper.SOURCE_DIGEST_PATHS.values())


def test_fixed_prefix_binds_all_exact_lengths_and_hashes(source_case):
    _, archive, request, helper_source, _, binding = source_case
    assert binding.request_sha256 == request["request_sha256"]
    assert binding.helper_sha256 == _digest(helper_source)
    assert binding.tool_sha256 == _digest(Path("/usr/bin/git").read_bytes())
    assert binding.archive_sha256 == request["archive_sha256"]
    assert binding.request_bytes == len(canonical(request))
    assert binding.helper_bytes == len(helper_source)
    assert binding.tool_bytes == Path("/usr/bin/git").stat().st_size
    assert binding.archive_bytes == archive.stat().st_size


@pytest.mark.parametrize("mutation", ["bad-magic", "bad-helper-hash", "bad-archive-hash", "trailing"])
def test_production_bootstrap_emits_bound_phase1_failures(source_case, mutation):
    _, _, _, _, original, binding = source_case
    wire = bytearray(original)
    if mutation == "bad-magic":
        wire[:8] = b"BADMAGIC"
    elif mutation == "bad-helper-hash":
        offset = helper.PREFIX.size + binding.helper_bytes // 2
        wire[offset] ^= 1
    elif mutation == "bad-archive-hash":
        wire[-1] ^= 1
    else:
        wire.extend(b"x")
    completed = _run_bootstrap(bytes(wire))
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert helper.validate_phase1_failure(receipt, binding=binding) == receipt
    assert receipt["cleanup"] == "not-created"


@pytest.mark.parametrize("mutation", ["missing-pax", "wrong-pax", "duplicate", "dotdot", "dot", "double-slash", "dotgit", "symlink", "other-root"])
def test_malformed_archive_fails_closed_after_verified_cleanup(tmp_path, source_case, mutation):
    files, _, request, helper_source, _, _ = source_case
    archive = tmp_path / (mutation + ".tar")
    _archive(archive, files, commit=request["source_commit"], mutation=mutation)
    changed = dict(request)
    changed["archive_sha256"] = _digest(archive.read_bytes())
    changed["artifact_ref"] = "artifact:" + changed["archive_sha256"]
    changed.pop("request_sha256")
    changed["request_sha256"] = _digest(canonical(changed))
    wire = helper.packet(helper_source, changed, archive, tool=Path("/usr/bin/git"))
    completed = _run_bootstrap(wire)
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    assert helper.validate_terminal(receipt, binding=binding, request=changed) == receipt
    assert receipt["schema"] == helper.FAILURE_SCHEMA
    assert receipt["stage"] == "archive"
    assert receipt["outcome"] == "FAILED"
    assert receipt["cleanup"] == "removed"


def test_reconstructed_tree_mismatch_is_a_source_failure(source_case):
    _, archive, request, helper_source, _, _ = source_case
    changed = dict(request)
    changed["source_tree"] = "0" * 40
    changed.pop("request_sha256")
    changed["request_sha256"] = _digest(canonical(changed))
    wire = helper.packet(helper_source, changed, archive, tool=Path("/usr/bin/git"))
    completed = _run_bootstrap(wire)
    receipt = json.loads(completed.stdout)
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    assert helper.validate_terminal(receipt, binding=binding, request=changed) == receipt
    assert completed.returncode == 1
    assert receipt["stage"] == "source"
    assert receipt["diagnostic_code"] == "IDENTITY_MISMATCH"
    assert receipt["cleanup"] == "removed"


@pytest.mark.parametrize("field,path", sorted(helper.SOURCE_DIGEST_PATHS.items()))
def test_each_bound_source_file_digest_is_verified(tmp_path, source_case, field, path):
    original_files, _, original_request, helper_source, _, _ = source_case
    changed_files = dict(original_files)
    changed_files[path] += b"# changed candidate source\n"
    source = tmp_path / "changed-source"
    source.mkdir()
    _write_source(source, changed_files)
    tree = _tree_hash(source)
    archive = tmp_path / "changed-source.tar"
    _archive(archive, changed_files, commit=original_request["source_commit"])
    request = _request(changed_files, archive, tree=tree)
    request[field] = original_request[field]
    request.pop("request_sha256")
    request["request_sha256"] = _digest(canonical(request))
    wire = helper.packet(helper_source, request, archive, tool=Path("/usr/bin/git"))
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    completed = _run_bootstrap(wire)
    receipt = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert helper.validate_terminal(receipt, binding=binding, request=request) == receipt
    assert receipt["stage"] == "source"
    assert receipt["diagnostic_code"] == "IDENTITY_MISMATCH"
    assert receipt["cleanup"] == "removed"


def test_cleanup_failure_overrides_verified_source_and_never_emits_hold(tmp_path, source_case):
    _, archive, request, _, _, binding = source_case
    reconstructed = io.BytesIO(struct.pack("!Q", len(canonical(request))) + canonical(request) + archive.read_bytes())

    def fail_cleanup(_path, **_kwargs):
        raise OSError("injected cleanup failure")

    receipt = helper.execute_packet(
        reconstructed,
        binding=binding,
        git_path=Path("/usr/bin/git"),
        scratch_root=tmp_path / "scratch",
        cleanup_tree=fail_cleanup,
    )
    assert receipt["schema"] == helper.FAILURE_SCHEMA
    assert receipt["outcome"] == "AMBIGUOUS"
    assert receipt["stage"] == "cleanup"
    assert receipt["diagnostic_code"] == "CLEANUP_FAILED"
    assert receipt["original_stage"] == "source-verified"
    assert receipt["cleanup"] == "failed"
    assert helper.validate_terminal(receipt, binding=binding, request=request) == receipt


@pytest.mark.parametrize("state", ["residue", "symlink"])
def test_untrusted_scratch_root_is_refused_before_archive_write(tmp_path, source_case, state):
    _, archive, request, _, _, binding = source_case
    scratch = tmp_path / "scratch"
    if state == "residue":
        scratch.mkdir(mode=0o700)
        (scratch / "unowned-state").write_text("residue")
    else:
        target = tmp_path / "target"
        target.mkdir()
        scratch.symlink_to(target, target_is_directory=True)
    framed = io.BytesIO(struct.pack("!Q", len(canonical(request))) + canonical(request) + archive.read_bytes())
    receipt = helper.execute_packet(framed, binding=binding, git_path=Path("/usr/bin/git"), scratch_root=scratch)
    assert receipt["schema"] == helper.FAILURE_SCHEMA
    assert receipt["stage"] == "scratch"
    assert receipt["diagnostic_code"] == "IDENTITY_MISMATCH"
    assert receipt["cleanup"] == "not-created"
    assert helper.validate_terminal(receipt, binding=binding, request=request) == receipt


def test_test_executor_is_explicit_injection_only_and_runs_after_source_verification(tmp_path, source_case, monkeypatch):
    _, archive, request, _, _, binding = source_case
    framed = struct.pack("!Q", len(canonical(request))) + canonical(request) + archive.read_bytes()
    monkeypatch.setenv("TGW_NIX_RENDER_EXECUTOR", "not-reachable")
    called = []

    receipt = helper.execute_packet(
        io.BytesIO(framed),
        binding=binding,
        git_path=Path("/usr/bin/git"),
        scratch_root=tmp_path / "scratch",
        test_executor=lambda source, bound: called.append((source, bound["request_sha256"])) or "source-marker",
    )
    assert receipt["schema"] == helper.TEST_MARKER_SCHEMA
    assert receipt["outcome"] == "TEST_ONLY_SOURCE_VERIFIED"
    assert receipt["marker"] == "source-marker"
    assert receipt["cleanup"] == "removed"
    assert helper.validate_terminal(receipt, binding=binding, request=request, allow_test_marker=True) == receipt
    assert called and called[0][1] == request["request_sha256"]
    assert not (tmp_path / "scratch").exists()


@pytest.mark.parametrize("bound", ["members", "unpacked"])
def test_archive_member_and_unpacked_bounds_are_enforced(tmp_path, source_case, monkeypatch, bound):
    files, _, request, helper_source, _, _ = source_case
    archive = tmp_path / "bounded.tar"
    _archive(archive, files, commit=request["source_commit"])
    changed = dict(request)
    changed["archive_sha256"] = _digest(archive.read_bytes())
    changed["artifact_ref"] = "artifact:" + changed["archive_sha256"]
    changed.pop("request_sha256")
    changed["request_sha256"] = _digest(canonical(changed))
    wire = helper.packet(helper_source, changed, archive, tool=Path("/usr/bin/git"))
    binding = helper.parse_prefix(wire[: helper.PREFIX.size])
    framed = io.BytesIO(struct.pack("!Q", len(canonical(changed))) + canonical(changed) + archive.read_bytes())
    if bound == "members":
        monkeypatch.setattr(helper, "MAX_ARCHIVE_MEMBERS", 1)
    else:
        monkeypatch.setattr(helper, "MAX_UNPACKED_BYTES", 1)
    receipt = helper.execute_packet(framed, binding=binding, git_path=Path("/usr/bin/git"), scratch_root=tmp_path / "scratch")
    assert receipt["stage"] == "archive"
    assert receipt["diagnostic_code"] == "BOUND_EXCEEDED"
    assert receipt["cleanup"] == "removed"
    assert helper.validate_terminal(receipt, binding=binding, request=changed) == receipt


def test_source_helper_contains_no_nix_or_transport_executor():
    source = Path(helper.__file__).read_text()
    assert "nix eval" not in source
    assert "nix build" not in source
    assert "ssh" not in source.lower()
    assert "TGW_NIX_RENDER_EXECUTOR" not in source
    assert shutil.which("git") is not None

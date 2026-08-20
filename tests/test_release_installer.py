from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tgw.admission_recovery import compile_release_admission
from tgw.release_installer import (
    RECEIPT_SCHEMA,
    ReleaseError,
    _atomic_json,
    current_generation,
    install_runtime_files,
    materialize,
    recover,
    rollback,
    select,
    verify,
)

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
TREE = "c" * 40


def _archive(
    path: Path, files: dict[str, bytes], *, commit: str = COMMIT_A,
    link: tuple[str, str] | None = None,
) -> str:
    with tarfile.open(path, "w:gz", pax_headers={"comment": commit}) as archive:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(body))
        if link:
            info = tarfile.TarInfo(link[0])
            info.type = tarfile.SYMTYPE
            info.linkname = link[1]
            archive.addfile(info)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release(root: Path, tmp_path: Path, generation: str, commit: str, body: bytes) -> None:
    archive = tmp_path / f"{generation}.tar.gz"
    digest = _archive(
        archive, {"src/tgw/example.py": body, "bin/run.sh": b"#!/bin/sh\n"},
        commit=commit,
    )
    materialize(
        root, archive, generation=generation, commit=commit, tree=TREE,
        archive_sha256=digest,
    )


def _selection_evidence(commit: str, tree: str) -> tuple[dict[str, object], dict[str, object]]:
    digest = "sha256:" + "d" * 64
    preflight: dict[str, object] = {
        "schema": "tgw-environment-preflight-receipt/v1", "result": "PASS",
        "catalog_sha256": digest, "actor": "codex", "profile": "development",
        "attempt_id": "test-attempt", "tools": [],
    }
    preflight_hash = "sha256:" + hashlib.sha256(
        json.dumps(preflight, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(),
    ).hexdigest()
    evidence = {"status": "PASS", "candidate_commit": commit, "solution_hash": digest, "receipt_hash": digest}
    admission = compile_release_admission(request={
        "schema": "tgw-w16-release-admission-request/v1", "request_id": "test-admission",
        "candidate": {"commit": commit, "tree": tree},
        "plan": {"commit": commit, "solution_hash": digest},
        "environment": {"catalog_hash": digest, "receipt_hash": preflight_hash},
        "review": dict(evidence), "admission": dict(evidence),
    })
    return admission, preflight


def _selected_root(tmp_path: Path) -> Path:
    root = tmp_path / "tgw"
    _release(root, tmp_path, "release-a", COMMIT_A, b"A\n")
    os.symlink("releases/release-a", root / "current")
    return root


def test_materialize_verify_select_replay_and_rollback(tmp_path: Path) -> None:
    root = _selected_root(tmp_path)
    _release(root, tmp_path, "release-b", COMMIT_B, b"B\n")

    admission, preflight = _selection_evidence(COMMIT_B, TREE)
    receipt = select(root, "release-b", expected_current="release-a", operation_id="deploy-b", admission_receipt=admission, environment_preflight_receipt=preflight)
    assert receipt["state"] == "completed"
    assert receipt["selected_commit"] == COMMIT_B
    assert len(receipt["selected_manifest_sha256"]) == 64
    assert current_generation(root) == "release-b"
    assert select(root, "release-b", expected_current="release-a", operation_id="deploy-b", admission_receipt=admission, environment_preflight_receipt=preflight) == receipt

    rolled_back = rollback(
        root, root / "receipts" / "deploy-b.json",
        expected_current="release-b", operation_id="rollback-b",
    )
    assert rolled_back["rollback_of"] == "deploy-b"
    assert current_generation(root) == "release-a"
    assert (root / "releases" / "release-b").is_dir()


def test_selection_is_compare_and_swap(tmp_path: Path) -> None:
    root = _selected_root(tmp_path)
    _release(root, tmp_path, "release-b", COMMIT_B, b"B\n")
    admission, preflight = _selection_evidence(COMMIT_B, TREE)
    with pytest.raises(ReleaseError, match="current generation changed"):
        select(
            root, "release-b", expected_current="wrong", operation_id="deploy-b",
            admission_receipt=admission, environment_preflight_receipt=preflight,
        )
    assert current_generation(root) == "release-a"
    assert not (root / "operations" / "deploy-b.json").exists()


def test_bootstrap_selection_requires_an_absent_current_generation(tmp_path: Path) -> None:
    root = tmp_path / "tgw"
    _release(root, tmp_path, "bootstrap-a", COMMIT_A, b"A\n")
    admission, preflight = _selection_evidence(COMMIT_A, TREE)
    receipt = select(root, "bootstrap-a", expected_current=None, operation_id="bootstrap-a", admission_receipt=admission, environment_preflight_receipt=preflight)
    assert receipt["previous_generation"] is None
    assert current_generation(root) == "bootstrap-a"
    with pytest.raises(ReleaseError, match="current generation changed"):
        select(root, "bootstrap-a", expected_current=None, operation_id="bootstrap-b", admission_receipt=admission, environment_preflight_receipt=preflight)


def test_direct_select_refuses_without_exact_admission_evidence(tmp_path: Path) -> None:
    root = _selected_root(tmp_path)
    _release(root, tmp_path, "release-b", COMMIT_B, b"B\n")
    with pytest.raises(ReleaseError, match="requires admission"):
        select(root, "release-b", expected_current="release-a", operation_id="direct")
    assert current_generation(root) == "release-a"


def test_public_installer_refuses_missing_or_mismatched_admission(tmp_path: Path) -> None:
    archive = tmp_path / "candidate.tar.gz"
    digest = _archive(archive, {"src/tgw/example.py": b"candidate\n"}, commit=COMMIT_A)
    command = [
        sys.executable, "-m", "tgw.release_installer", "--root", str(tmp_path / "root"), "install",
        "--archive", str(archive), "--generation", "candidate", "--commit", COMMIT_A, "--tree", TREE,
        "--archive-sha256", digest, "--expected-current", "none", "--operation-id", "candidate",
    ]
    result = subprocess.run(command, text=True, capture_output=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")})
    assert result.returncode != 0
    assert not (tmp_path / "root" / "current").exists()


@pytest.mark.parametrize("unsafe", ["../escape", "/absolute"])
def test_materialize_rejects_traversal(tmp_path: Path, unsafe: str) -> None:
    root = tmp_path / "tgw"
    archive = tmp_path / "unsafe.tar.gz"
    digest = _archive(archive, {unsafe: b"bad"})
    with pytest.raises(ReleaseError, match="unsafe archive member"):
        materialize(
            root, archive, generation="bad", commit=COMMIT_A, tree=TREE,
            archive_sha256=digest,
        )
    assert not (tmp_path / "escape").exists()


def test_materialize_rejects_links_and_digest_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar.gz"
    digest = _archive(archive, {"safe": b"ok"}, link=("linked", "safe"))
    with pytest.raises(ReleaseError, match="unsafe archive member"):
        materialize(
            tmp_path / "root", archive, generation="linked", commit=COMMIT_A,
            tree=TREE, archive_sha256=digest,
        )
    with pytest.raises(ReleaseError, match="archive digest mismatch"):
        materialize(
            tmp_path / "other", archive, generation="bad-digest", commit=COMMIT_A,
            tree=TREE, archive_sha256="0" * 64,
        )


def test_materialize_rejects_claimed_commit_not_embedded_by_git_archive(tmp_path: Path) -> None:
    archive = tmp_path / "wrong-commit.tar.gz"
    digest = _archive(archive, {"safe": b"ok"}, commit=COMMIT_A)
    with pytest.raises(ReleaseError, match="Git commit identity mismatch"):
        materialize(
            tmp_path / "root", archive, generation="wrong-commit", commit=COMMIT_B,
            tree=TREE, archive_sha256=digest,
        )


def test_verify_rejects_changed_or_mutable_content(tmp_path: Path) -> None:
    root = _selected_root(tmp_path)
    file_path = root / "releases" / "release-a" / "src" / "tgw" / "example.py"
    os.chmod(file_path, 0o644)
    with pytest.raises(ReleaseError, match="mutable or linked"):
        verify(root, "release-a")
    os.chmod(file_path, 0o644)
    file_path.write_bytes(b"tampered\n")
    os.chmod(file_path, 0o444)
    with pytest.raises(ReleaseError, match="does not match manifest"):
        verify(root, "release-a")


def test_runtime_overlay_restores_preexisting_config_directory_and_rejects_collision(tmp_path: Path) -> None:
    root = tmp_path / "tgw"
    archive = tmp_path / "candidate.tar.gz"
    digest = _archive(
        archive,
        {"config/existing.json": b"{}\n", "src/tgw/example.py": b"candidate\n"},
        commit=COMMIT_B,
    )
    materialize(
        root, archive, generation="release-b", commit=COMMIT_B, tree=TREE,
        archive_sha256=digest,
    )
    result = install_runtime_files(
        root, "release-b", {"config/tgw-api-config.json": b'{"schema":"runtime"}\n'},
    )
    assert result["status"] == "installed"
    assert stat.S_IMODE((root / "releases/release-b/config").stat().st_mode) == 0o555
    assert verify(root, "release-b")["runtime_manifest_sha256"]

    root2 = tmp_path / "collision"
    collision_archive = tmp_path / "collision.tar.gz"
    collision_digest = _archive(
        collision_archive, {"config/tgw-api-config.json": b"source-owned\n"}, commit=COMMIT_B,
    )
    materialize(
        root2, collision_archive, generation="release-b", commit=COMMIT_B, tree=TREE,
        archive_sha256=collision_digest,
    )
    with pytest.raises(FileExistsError):
        install_runtime_files(
            root2, "release-b", {"config/tgw-api-config.json": b"host-owned\n"},
        )
    assert stat.S_IMODE((root2 / "releases/release-b/config").stat().st_mode) == 0o555
    assert verify(root2, "release-b")["status"] == "PASS"


def test_recover_completes_landed_selector_swap(tmp_path: Path) -> None:
    root = _selected_root(tmp_path)
    _release(root, tmp_path, "release-b", COMMIT_B, b"B\n")
    manifest = json.loads(
        (root / "releases" / "release-b" / ".release-manifest.json").read_text(),
    )
    intent = {
        "schema": RECEIPT_SCHEMA,
        "state": "prepared",
        "operation_id": "interrupted",
        "previous_generation": "release-a",
        "selected_generation": "release-b",
        "selected_commit": manifest["commit"],
        "selected_archive_sha256": manifest["archive_sha256"],
        "selected_content_manifest_sha256": manifest["content_manifest_sha256"],
        "selected_manifest_sha256": hashlib.sha256(
            (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        ).hexdigest(),
    }
    _atomic_json(root / "operations" / "interrupted.json", intent, mode=0o600)
    replacement = root / ".current-test"
    os.symlink("releases/release-b", replacement)
    os.replace(replacement, root / "current")

    completed = recover(root)
    assert completed == [{**intent, "state": "completed"}]
    assert json.loads((root / "receipts" / "interrupted.json").read_text()) == completed[0]


def test_recover_rejects_ambiguous_selector(tmp_path: Path) -> None:
    root = _selected_root(tmp_path)
    intent = {
        "schema": RECEIPT_SCHEMA,
        "state": "prepared",
        "operation_id": "ambiguous",
        "previous_generation": "release-x",
        "selected_generation": "release-y",
    }
    _atomic_json(root / "operations" / "ambiguous.json", intent, mode=0o600)
    with pytest.raises(ReleaseError, match="ambiguous selector recovery"):
        recover(root)

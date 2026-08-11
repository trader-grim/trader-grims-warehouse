from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from tgw.release_installer import (
    RECEIPT_SCHEMA,
    ReleaseError,
    _atomic_json,
    current_generation,
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


def _selected_root(tmp_path: Path) -> Path:
    root = tmp_path / "tgw"
    _release(root, tmp_path, "release-a", COMMIT_A, b"A\n")
    os.symlink("releases/release-a", root / "current")
    return root


def test_materialize_verify_select_replay_and_rollback(tmp_path: Path) -> None:
    root = _selected_root(tmp_path)
    _release(root, tmp_path, "release-b", COMMIT_B, b"B\n")

    receipt = select(
        root, "release-b", expected_current="release-a", operation_id="deploy-b",
    )
    assert receipt["state"] == "completed"
    assert receipt["selected_commit"] == COMMIT_B
    assert len(receipt["selected_manifest_sha256"]) == 64
    assert current_generation(root) == "release-b"
    assert select(
        root, "release-b", expected_current="release-a", operation_id="deploy-b",
    ) == receipt

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
    with pytest.raises(ReleaseError, match="current generation changed"):
        select(root, "release-b", expected_current="wrong", operation_id="deploy-b")
    assert current_generation(root) == "release-a"
    assert not (root / "operations" / "deploy-b.json").exists()


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

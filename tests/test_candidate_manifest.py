import json
import subprocess
from pathlib import Path

import pytest

from tgw.candidate_manifest import (
    CandidateManifestError,
    build_candidate_manifest,
    create_test_receipt,
    verify_backup_restore,
)


def _repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("old\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "app.py").write_text("new\n")
    subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
    return repo, base


def _manifest(repo, base, **changes):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    base_tree = subprocess.check_output(["git", "rev-parse", f"{base}^{{tree}}"], cwd=repo, text=True).strip()
    values = dict(
        commit="HEAD",
        base_commit=base,
        predecessor_release={
            "schema": "tgw-release-manifest-v1", "generation": "previous",
            "commit": base, "git_tree": base_tree, "archive_sha256": "a" * 64,
        },
        plan_commit="plan-commit",
        solution_hash="sha256:solution",
        closure_hash="sha256:closure",
        focused_receipt=create_test_receipt(
            scope="focused", command=("pytest", "tests/selected"), source_commit=commit,
            source_tree=tree, returncode=0,
        ),
        full_suite_receipt=create_test_receipt(
            scope="full", command=("pytest", "-q"), source_commit=commit,
            source_tree=tree, returncode=0,
        ),
    )
    values.update(changes)
    return build_candidate_manifest(repo, **values)


def test_manifest_is_reproducible_from_closed_commit_and_ignores_dirty_worktree(tmp_path):
    repo, base = _repo(tmp_path)
    first = _manifest(repo, base)
    (repo / "app.py").write_text("dirty and not candidate\n")
    second = _manifest(repo, base)

    assert first == second
    assert first["candidate_closed"] is True
    assert first["installed"] is False
    assert first["tests"]["full_suite"]["status"] == "PASS"


def test_database_change_requires_verified_backup_restore(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "schema.sql").write_text("CREATE TABLE example(id int);\n")
    subprocess.run(["git", "add", "schema.sql"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "schema"], cwd=repo, check=True)

    with pytest.raises(CandidateManifestError, match="backup/restore"):
        _manifest(repo, base)

    receipt = verify_backup_restore(b"schema-v1", backup=lambda body: b"backup:" + body, restore=lambda body: body.removeprefix(b"backup:"))
    manifest = _manifest(repo, base, migration_receipt=receipt)
    assert manifest["database"]["migration_paths"] == ["schema.sql"]
    assert manifest["database"]["backup_restore"]["verified"] is True


def test_failed_restore_cannot_admit_migration_candidate(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "schema.sql").write_text("ALTER TABLE example ADD COLUMN name text;\n")
    subprocess.run(["git", "add", "schema.sql"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "schema"], cwd=repo, check=True)
    receipt = verify_backup_restore(b"schema", backup=lambda body: body, restore=lambda body: b"wrong")

    with pytest.raises(CandidateManifestError):
        _manifest(repo, base, migration_receipt=receipt)


def test_manifest_hash_covers_plan_test_and_migration_bindings(tmp_path):
    repo, base = _repo(tmp_path)
    first = _manifest(repo, base)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    changed = _manifest(repo, base, focused_receipt=create_test_receipt(
        scope="focused", command=("pytest", "tests/other"), source_commit=commit,
        source_tree=tree, returncode=0,
    ))
    assert first["manifest_hash"] != changed["manifest_hash"]
    json.dumps(first)


def test_candidate_cannot_self_select_or_misbind_predecessor_release(tmp_path):
    repo, base = _repo(tmp_path)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    passing = create_test_receipt(
        scope="focused", command=("pytest",), source_commit=commit, source_tree=tree, returncode=0,
    )
    full = create_test_receipt(
        scope="full", command=("pytest", "-q"), source_commit=commit, source_tree=tree, returncode=0,
    )
    with pytest.raises(CandidateManifestError, match="cannot be the candidate"):
        build_candidate_manifest(
            repo, commit=commit, base_commit=commit,
            predecessor_release={
                "schema": "tgw-release-manifest-v1", "generation": "forged",
                "commit": commit, "git_tree": tree, "archive_sha256": "a" * 64,
            },
            plan_commit="plan", solution_hash="sha256:solution", closure_hash="sha256:closure",
            focused_receipt=passing, full_suite_receipt=full,
        )
    with pytest.raises(CandidateManifestError, match="does not match predecessor"):
        build_candidate_manifest(
            repo, commit=commit, base_commit=base,
            predecessor_release={
                "schema": "tgw-release-manifest-v1", "generation": "forged",
                "commit": "b" * 40, "git_tree": "c" * 40, "archive_sha256": "a" * 64,
            },
            plan_commit="plan", solution_hash="sha256:solution", closure_hash="sha256:closure",
            focused_receipt=passing, full_suite_receipt=full,
        )

import json
import subprocess
from pathlib import Path

import pytest

from tgw.candidate_manifest import (
    CandidateManifestError,
    build_candidate_manifest,
    create_plan_authority_migration_receipt,
    create_test_receipt,
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


def _migration_receipt(repo: Path, base: str, **changes):
    """A structurally complete receipt; the live-cluster script creates real ones."""
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    base_tree = subprocess.check_output(
        ["git", "rev-parse", f"{base}^{{tree}}"], cwd=repo, text=True,
    ).strip()
    source = subprocess.check_output(
        ["git", "show", f"{commit}:src/tgw/plan_authority.sql"], cwd=repo,
    )
    values = dict(
        candidate_commit=commit,
        candidate_tree=tree,
        base_commit=base,
        base_tree=base_tree,
        migration_path="src/tgw/plan_authority.sql",
        migration_source=source,
        postgres_version="PostgreSQL 17.10",
        backup=b"custom-format-pg-dump",
        source_schema=b"v1 schema",
        restored_schema=b"v1 schema",
        source_data=b"v1 data",
        restored_data=b"v1 data",
        migrated_schema=b"v2 schema",
        migrated_data=b"v2 data",
        verified=True,
    )
    values.update(changes)
    return create_plan_authority_migration_receipt(**values)


def _commit_plan_authority_migration(repo: Path, source: str = "ALTER TABLE example ADD COLUMN name text;\n"):
    path = repo / "src/tgw/plan_authority.sql"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    subprocess.run(["git", "add", str(path.relative_to(repo))], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "plan authority migration"], cwd=repo, check=True)


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
    _commit_plan_authority_migration(repo)

    with pytest.raises(CandidateManifestError, match="backup/restore"):
        _manifest(repo, base)

    receipt = _migration_receipt(repo, base)
    manifest = _manifest(repo, base, migration_receipt=receipt)
    assert manifest["database"]["migration_paths"] == ["src/tgw/plan_authority.sql"]
    assert manifest["database"]["backup_restore"]["verified"] is True


def test_failed_restore_cannot_admit_migration_candidate(tmp_path):
    repo, base = _repo(tmp_path)
    _commit_plan_authority_migration(repo)
    receipt = _migration_receipt(repo, base, restored_data=b"wrong", verified=False)

    with pytest.raises(CandidateManifestError, match="not verified"):
        _manifest(repo, base, migration_receipt=receipt)


def test_migration_receipt_cannot_bind_another_commit_tree_or_sql_source(tmp_path):
    repo, base = _repo(tmp_path)
    _commit_plan_authority_migration(repo, "CREATE TABLE proof_one(id int);\n")
    receipt = _migration_receipt(repo, base)
    (repo / "src/tgw/plan_authority.sql").write_text("CREATE TABLE proof_two(id int);\n")
    subprocess.run(["git", "add", "src/tgw/plan_authority.sql"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "different migration"], cwd=repo, check=True)

    with pytest.raises(CandidateManifestError, match="candidate binding mismatch"):
        _manifest(repo, base, migration_receipt=receipt)


def test_only_plan_authority_sql_has_a_receipt_protocol(tmp_path):
    repo, base = _repo(tmp_path)
    _commit_plan_authority_migration(repo)
    (repo / "other.sql").write_text("CREATE TABLE unrelated(id int);\n")
    subprocess.run(["git", "add", "other.sql"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "another migration"], cwd=repo, check=True)

    with pytest.raises(CandidateManifestError, match="separately scoped"):
        _manifest(repo, base, migration_receipt=_migration_receipt(repo, base))


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

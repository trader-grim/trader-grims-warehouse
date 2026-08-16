import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tgw.candidate_manifest import (
    CandidateManifestError,
    build_candidate_manifest,
    create_migration_safety_receipt,
    create_test_output_artifact,
    create_test_receipt,
    load_candidate_test_plan,
)


def _repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("old\n")
    _install_test_contract(repo)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "app.py").write_text("new\n")
    subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
    return repo, base


def _install_test_contract(repo: Path):
    runner = repo / "scripts" / "candidate-test-runner.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("# fixture runner\n")
    runner_hash = "sha256:" + hashlib.sha256(runner.read_bytes()).hexdigest()
    plan = repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({
        "schema": "tgw-candidate-test-plan/v1", "plan_id": "fixture-candidate-tests", "version": 1,
        "runner": {"path": "scripts/candidate-test-runner.py", "sha256": runner_hash, "argv_prefix": ["-m", "pytest"]},
        "scopes": {
            "focused": {"argv": ["-q", "tests/selected"]},
            "full": {"argv": ["-q"]},
        },
    }, sort_keys=True))


def _test_evidence(repo: Path, scope: str, *, returncode: int = 0, stdout: bytes = b""):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    plan = load_candidate_test_plan(repo, source_commit=commit)
    command = plan["commands"][scope]
    output = create_test_output_artifact(
        scope=scope, command=command, source_commit=commit, source_tree=tree,
        stdout=stdout, stderr=b"",
    )
    return create_test_receipt(
        scope=scope, command=command, source_commit=commit, source_tree=tree,
        returncode=returncode, test_plan=plan, output_artifact=output,
    ), output


def _manifest(repo, base, **changes):
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
        focused_receipt=_test_evidence(repo, "focused")[0],
        full_suite_receipt=_test_evidence(repo, "full")[0],
        focused_output_artifact=_test_evidence(repo, "focused")[1],
        full_suite_output_artifact=_test_evidence(repo, "full")[1],
    )
    values.update(changes)
    return build_candidate_manifest(repo, **values)


def _migration_receipt(
    repo: Path,
    base: str,
    *,
    path: str = "src/tgw/plan_authority.sql",
    schema_snapshot_path: str | None = None,
    **changes,
):
    """A structurally complete receipt; the live-cluster script creates real ones."""
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    base_tree = subprocess.check_output(
        ["git", "rev-parse", f"{base}^{{tree}}"], cwd=repo, text=True,
    ).strip()
    source = subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=repo)
    snapshot = (
        subprocess.check_output(["git", "show", f"{commit}:{schema_snapshot_path}"], cwd=repo)
        if schema_snapshot_path is not None else None
    )
    values = dict(
        candidate_commit=commit,
        candidate_tree=tree,
        base_commit=base,
        base_tree=base_tree,
        migration_path=path,
        migration_source=source,
        schema_snapshot_path=schema_snapshot_path,
        schema_snapshot_source=snapshot,
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
    return create_migration_safety_receipt(**values)


def _commit_plan_authority_migration(repo: Path, source: str = "ALTER TABLE example ADD COLUMN name text;\n"):
    path = repo / "src/tgw/plan_authority.sql"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    subprocess.run(["git", "add", str(path.relative_to(repo))], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "plan authority migration"], cwd=repo, check=True)


def _commit_queue_migration_and_snapshot(repo: Path):
    snapshot = repo / "src/tgw/queue/live_schema.sql"
    migration = repo / "src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    migration.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text("-- pg_dump snapshot; never an executable migration\n")
    migration.write_text("CREATE OR REPLACE FUNCTION queue_proof() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;\n")
    subprocess.run(["git", "add", str(snapshot.relative_to(repo)), str(migration.relative_to(repo))], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "queue lease migration and snapshot"], cwd=repo, check=True)


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
    assert manifest["database"]["backup_restore"][0]["verified"] is True


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


def test_every_changed_executable_sql_path_requires_its_own_receipt(tmp_path):
    repo, base = _repo(tmp_path)
    _commit_plan_authority_migration(repo)
    (repo / "other.sql").write_text("CREATE TABLE unrelated(id int);\n")
    subprocess.run(["git", "add", "other.sql"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "another migration"], cwd=repo, check=True)

    with pytest.raises(CandidateManifestError, match="separately scoped"):
        _manifest(repo, base, migration_receipt=_migration_receipt(repo, base))


def test_queue_snapshot_is_bound_to_a_separate_explicit_queue_migration(tmp_path):
    repo, base = _repo(tmp_path)
    _commit_queue_migration_and_snapshot(repo)
    receipt = _migration_receipt(
        repo,
        base,
        path="src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql",
        schema_snapshot_path="src/tgw/queue/live_schema.sql",
    )

    manifest = _manifest(repo, base, migration_receipts=(receipt,))

    assert manifest["database"]["migration_paths"] == [
        "src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql",
    ]
    assert manifest["database"]["schema_snapshot_paths"] == ["src/tgw/queue/live_schema.sql"]
    assert manifest["database"]["backup_restore"][0]["schema_snapshot_path"] == (
        "src/tgw/queue/live_schema.sql"
    )


def test_queue_snapshot_cannot_be_misrepresented_as_an_executable_migration(tmp_path):
    repo, base = _repo(tmp_path)
    _commit_queue_migration_and_snapshot(repo)
    receipt = _migration_receipt(repo, base, path="src/tgw/queue/live_schema.sql")

    with pytest.raises(CandidateManifestError, match="snapshots cannot be used"):
        _manifest(repo, base, migration_receipts=(receipt,))


def test_changed_schema_snapshot_cannot_be_left_unbound(tmp_path):
    repo, base = _repo(tmp_path)
    _commit_queue_migration_and_snapshot(repo)
    receipt = _migration_receipt(
        repo, base,
        path="src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql",
    )

    with pytest.raises(CandidateManifestError, match="separately scoped"):
        _manifest(repo, base, migration_receipts=(receipt,))


def test_manifest_covers_authority_migration_and_queue_snapshot_as_separate_proofs(tmp_path):
    repo, base = _repo(tmp_path)
    _commit_plan_authority_migration(repo)
    _commit_queue_migration_and_snapshot(repo)
    authority = _migration_receipt(repo, base)
    queue = _migration_receipt(
        repo,
        base,
        path="src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql",
        schema_snapshot_path="src/tgw/queue/live_schema.sql",
    )

    manifest = _manifest(repo, base, migration_receipts=(authority, queue))

    assert manifest["database"]["changed_sql_paths"] == [
        "src/tgw/plan_authority.sql",
        "src/tgw/queue/live_schema.sql",
        "src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql",
    ]
    assert manifest["database"]["migration_paths"] == [
        "src/tgw/plan_authority.sql",
        "src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql",
    ]
    assert len(manifest["database"]["backup_restore"]) == 2


def test_manifest_hash_covers_plan_test_and_migration_bindings(tmp_path):
    repo, base = _repo(tmp_path)
    first = _manifest(repo, base)
    changed_receipt, changed_output = _test_evidence(repo, "focused", stdout=b"different output")
    changed = _manifest(
        repo, base,
        focused_receipt=changed_receipt,
        focused_output_artifact=changed_output,
    )
    assert first["manifest_hash"] != changed["manifest_hash"]
    json.dumps(first)


def test_candidate_cannot_self_select_or_misbind_predecessor_release(tmp_path):
    repo, base = _repo(tmp_path)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    passing, passing_output = _test_evidence(repo, "focused")
    full, full_output = _test_evidence(repo, "full")
    with pytest.raises(CandidateManifestError, match="cannot be the candidate"):
        build_candidate_manifest(
            repo, commit=commit, base_commit=commit,
            predecessor_release={
                "schema": "tgw-release-manifest-v1", "generation": "forged",
                "commit": commit, "git_tree": tree, "archive_sha256": "a" * 64,
            },
            plan_commit="plan", solution_hash="sha256:solution", closure_hash="sha256:closure",
            focused_receipt=passing, full_suite_receipt=full,
            focused_output_artifact=passing_output, full_suite_output_artifact=full_output,
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
            focused_output_artifact=passing_output, full_suite_output_artifact=full_output,
        )

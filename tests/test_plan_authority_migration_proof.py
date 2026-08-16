"""Opt-in live proof for the isolated PostgreSQL 17 PlanAuthority migration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tgw.candidate_manifest import (
    build_candidate_manifest,
    create_test_receipt,
    verify_plan_authority_migration_receipt,
)

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.mark.skipif(
    os.environ.get("TGW_TEST_PLAN_AUTHORITY_MIGRATION_PROOF") != "1",
    reason="requires an isolated local PostgreSQL 17 cluster",
)
def test_real_postgresql17_backup_upgrade_restore_produces_bound_receipt(tmp_path: Path):
    """Exercise initdb, pg_dump/restore, SQL upgrade, and receipt verification."""
    repo = tmp_path / "candidate"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Migration proof"], cwd=repo, check=True)
    migration = repo / "src/tgw/plan_authority.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("-- predecessor deliberately has no PlanAuthority migration\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "v1 release"], cwd=repo, check=True)
    base = _git(repo, "rev-parse", "HEAD")
    migration.write_bytes((ROOT / "src/tgw/plan_authority.sql").read_bytes())
    subprocess.run(["git", "add", "src/tgw/plan_authority.sql"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "upgrade PlanAuthority"], cwd=repo, check=True)
    candidate = _git(repo, "rev-parse", "HEAD")
    candidate_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    base_tree = _git(repo, "rev-parse", f"{base}^{{tree}}")
    receipt_path = tmp_path / "migration-receipt.json"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts/prove_plan_authority_migration.py"),
            "--repo", str(repo), "--commit", candidate, "--base-commit", base,
            "--output", str(receipt_path),
        ],
        cwd=ROOT, env=environment, text=True, capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(receipt_path.read_text())
    verified = verify_plan_authority_migration_receipt(
        receipt,
        candidate_commit=candidate,
        candidate_tree=candidate_tree,
        base_commit=base,
        base_tree=base_tree,
        migration_paths=("src/tgw/plan_authority.sql",),
        migration_source=migration.read_bytes(),
    )
    assert verified.verified is True
    assert verified.postgres_version.startswith("PostgreSQL 17.")
    assert verified.source_schema_sha256 == verified.restored_schema_sha256
    assert verified.source_data_sha256 == verified.restored_data_sha256
    manifest = build_candidate_manifest(
        repo,
        commit=candidate,
        base_commit=base,
        predecessor_release={
            "schema": "tgw-release-manifest-v1", "generation": "v1",
            "commit": base, "git_tree": base_tree, "archive_sha256": "a" * 64,
        },
        plan_commit="plan-proof",
        solution_hash="sha256:solution-proof",
        closure_hash="sha256:closure-proof",
        focused_receipt=create_test_receipt(
            scope="focused", command=("pytest", "tests/migration"),
            source_commit=candidate, source_tree=candidate_tree, returncode=0,
        ),
        full_suite_receipt=create_test_receipt(
            scope="full", command=("pytest", "-q"),
            source_commit=candidate, source_tree=candidate_tree, returncode=0,
        ),
        migration_receipt=receipt,
    )
    assert manifest["database"]["backup_restore"]["receipt_hash"] == verified.receipt_hash

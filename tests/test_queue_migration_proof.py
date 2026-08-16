"""Opt-in live proof for the isolated PostgreSQL 17 queue lease migration."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tgw.candidate_manifest import (
    build_candidate_manifest,
    create_test_output_artifact,
    create_test_receipt,
    load_candidate_test_plan,
    verify_migration_safety_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
BASE = "c63026fbe20da8343b075067d1bbf1c8a2942ffa"
MIGRATION_PATH = "src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql"
SNAPSHOT_PATH = "src/tgw/queue/live_schema.sql"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _install_test_contract(repo: Path):
    runner = repo / "scripts" / "candidate-test-runner.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("# queue-migration-proof fixture runner\n")
    plan = repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({
        "schema": "tgw-candidate-test-plan/v1", "plan_id": "queue-migration-proof", "version": 1,
        "runner": {
            "path": "scripts/candidate-test-runner.py",
            "sha256": "sha256:" + hashlib.sha256(runner.read_bytes()).hexdigest(),
            "argv_prefix": ["-m", "pytest"],
        },
        "scopes": {"focused": {"argv": ["-q", "tests/migration"]}, "full": {"argv": ["-q"]}},
    }, sort_keys=True))


@pytest.mark.skipif(
    os.environ.get("TGW_TEST_QUEUE_MIGRATION_PROOF") != "1",
    reason="requires an isolated local PostgreSQL 17 cluster",
)
def test_real_postgresql17_queue_backup_restore_and_explicit_migration_produce_bound_receipt(tmp_path: Path):
    """The proof never replays the queue snapshot as an installed migration."""
    repo = tmp_path / "candidate"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Queue migration proof"], cwd=repo, check=True)
    snapshot = repo / SNAPSHOT_PATH
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(subprocess.check_output([
        "git", "show", f"{BASE}:{SNAPSHOT_PATH}",
    ], cwd=ROOT))
    _install_test_contract(repo)
    subprocess.run(["git", "add", SNAPSHOT_PATH], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "queue predecessor release"], cwd=repo, check=True)
    base = _git(repo, "rev-parse", "HEAD")

    snapshot.write_bytes((ROOT / SNAPSHOT_PATH).read_bytes())
    migration = repo / MIGRATION_PATH
    migration.parent.mkdir(parents=True)
    migration.write_bytes((ROOT / MIGRATION_PATH).read_bytes())
    subprocess.run(["git", "add", SNAPSHOT_PATH, MIGRATION_PATH], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fence terminal queue leases"], cwd=repo, check=True)
    candidate = _git(repo, "rev-parse", "HEAD")
    candidate_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    base_tree = _git(repo, "rev-parse", f"{base}^{{tree}}")
    receipt_path = tmp_path / "queue-migration-receipt.json"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts/prove_queue_terminal_lease_expiry_migration.py"),
            "--repo", str(repo), "--commit", candidate, "--base-commit", base,
            "--output", str(receipt_path),
        ],
        cwd=ROOT, env=environment, text=True, capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(receipt_path.read_text())
    verified = verify_migration_safety_receipt(
        receipt,
        candidate_commit=candidate,
        candidate_tree=candidate_tree,
        base_commit=base,
        base_tree=base_tree,
        migration_paths=(MIGRATION_PATH,),
        migration_source=migration.read_bytes(),
        schema_snapshot_source=snapshot.read_bytes(),
    )
    assert verified.verified is True
    assert verified.schema_snapshot_path == SNAPSHOT_PATH
    assert verified.source_schema_sha256 == verified.restored_schema_sha256
    assert verified.source_data_sha256 == verified.restored_data_sha256
    test_plan = load_candidate_test_plan(repo, source_commit=candidate)
    focused_command = test_plan["commands"]["focused"]
    full_command = test_plan["commands"]["full"]
    focused_output = create_test_output_artifact(
        scope="focused", command=focused_command, source_commit=candidate, source_tree=candidate_tree,
        stdout=b"migration focused test passed", stderr=b"",
    )
    full_output = create_test_output_artifact(
        scope="full", command=full_command, source_commit=candidate, source_tree=candidate_tree,
        stdout=b"migration full test passed", stderr=b"",
    )
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
            scope="focused", command=focused_command, source_commit=candidate,
            source_tree=candidate_tree, returncode=0, test_plan=test_plan, output_artifact=focused_output,
        ),
        full_suite_receipt=create_test_receipt(
            scope="full", command=full_command, source_commit=candidate,
            source_tree=candidate_tree, returncode=0, test_plan=test_plan, output_artifact=full_output,
        ),
        focused_output_artifact=focused_output,
        full_suite_output_artifact=full_output,
        migration_receipts=(receipt,),
    )
    assert manifest["database"]["backup_restore"][0]["receipt_hash"] == verified.receipt_hash

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tgw.governed_execution_receipt import (
    GovernedExecutionReceiptError,
    create_candidate_governed_execution_receipt,
    verify_candidate_governed_execution_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def canonical_hash(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def candidate_repo(tmp_path):
    repo = tmp_path / "candidate"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("answer = 42\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    return repo, commit, tree


def card(tree):
    def binding(ref, content):
        return {"ref": ref, "hash": "sha256:" + hashlib.sha256(content.encode()).hexdigest()}

    unsigned = {
        "schema": "tgw-execution-card/v1",
        "card_id": "candidate-card",
        "solution_id": "sha256:solution",
        "role": "implementation",
        "selected_provider": "candidate-runner",
        "plan_commit": PLAN_COMMIT,
        "bindings": {
            "plan_input": binding("plan:input", "Plan input"),
            "plan_commit": binding("plan:commit", PLAN_COMMIT),
            "plan_graph": binding("plan:graph", "Plan Graph"),
            "codegraph_snapshot": binding("codegraph:snapshot", "CodeGraph"),
            "source_tree": binding(f"git:tree:{tree}", "candidate source archive"),
            "execution_environment": binding("environment:manifest", "environment"),
            "authority_conditions": binding("authority:conditions", "authority"),
            "receipt_sink": binding("receipt:sink", "sink"),
        },
        "authority": ["local source and tests only"],
        "exclusions": ["no deployment"],
        "acceptance": ["role receipt passes"],
        "receiver_profile": {"id": "codex", "version": 1},
        "lease": {"id": "lease", "expires_at": "2027-08-11T23:00:00Z", "stop_policy": "hold"},
    }
    return {**unsigned, "card_hash": canonical_hash(unsigned)}


def resource_receipt(card_value):
    unsigned = {
        "schema": "tgw-execution-resource-receipt/v1",
        "card_hash": card_value["card_hash"],
        "plan_commit": PLAN_COMMIT,
        "resources": {name: value for name, value in sorted(card_value["bindings"].items())},
    }
    return {**unsigned, "receipt_hash": canonical_hash(unsigned)}


def role_receipt(card_value, resource_value):
    unsigned = {
        "schema": "tgw-governed-coding-receipt/v1",
        "status": "PASS",
        "role": "implementation",
        "selected_provider": "candidate-runner",
        "execution_identity": "candidate-context:1",
        "card_hash": card_value["card_hash"],
        "promptcraft_receipt_hash": "sha256:" + "a" * 64,
        "resource_receipt_hash": resource_value["receipt_hash"],
        "outcome": "satisfied",
        "established_conditions": ["implemented"],
        "artifacts": [],
    }
    return {**unsigned, "receipt_hash": canonical_hash(unsigned)}


def test_candidate_receipt_binds_card_resources_and_role_to_exact_git_identity(tmp_path):
    _repo, commit, tree = candidate_repo(tmp_path)
    card_value = card(tree)
    resources = resource_receipt(card_value)
    role = role_receipt(card_value, resources)

    receipt = create_candidate_governed_execution_receipt(
        card=card_value,
        resource_receipt=resources,
        role_receipt=role,
        source_commit=commit,
        source_tree=tree,
        plan_commit=PLAN_COMMIT,
    )

    assert verify_candidate_governed_execution_receipt(
        receipt, source_commit=commit, source_tree=tree, plan_commit=PLAN_COMMIT
    ) == receipt
    assert receipt["role_receipt_hash"] == role["receipt_hash"]


def test_candidate_binding_refuses_a_card_for_another_source_tree(tmp_path):
    _repo, commit, tree = candidate_repo(tmp_path)
    card_value = card(tree)
    card_value["bindings"]["source_tree"]["ref"] = "git:tree:" + "0" * 40
    unsigned = dict(card_value)
    unsigned.pop("card_hash")
    card_value["card_hash"] = canonical_hash(unsigned)
    resources = resource_receipt(card_value)

    with pytest.raises(GovernedExecutionReceiptError, match="source tree does not match"):
        create_candidate_governed_execution_receipt(
            card=card_value,
            resource_receipt=resources,
            role_receipt=role_receipt(card_value, resources),
            source_commit=commit,
            source_tree=tree,
            plan_commit=PLAN_COMMIT,
        )


def test_candidate_receipt_script_resolves_the_requested_closed_candidate(tmp_path):
    repo, commit, tree = candidate_repo(tmp_path)
    card_value = card(tree)
    resources = resource_receipt(card_value)
    role = role_receipt(card_value, resources)
    card_path = tmp_path / "card.json"
    resources_path = tmp_path / "resources.json"
    role_path = tmp_path / "role.json"
    card_path.write_text(json.dumps(card_value))
    resources_path.write_text(json.dumps(resources))
    role_path.write_text(json.dumps(role))

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/bind_governed_execution_receipt.py"),
            "--repo", str(repo),
            "--candidate", "HEAD",
            "--plan-commit", PLAN_COMMIT,
            "--card", str(card_path),
            "--resource-receipt", str(resources_path),
            "--role-receipt", str(role_path),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["source_commit"] == commit
    assert receipt["source_tree"] == tree

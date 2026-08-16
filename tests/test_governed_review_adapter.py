import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tgw.governed_review_adapter import (
    IDENTITY_SCHEMA,
    ReviewRunnerError,
    run_governed_review,
    snapshot_hash,
    validate_execution,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-services/providers/promptcraft"))
from promptcraft.handoff import ExecutionCard, craft_handoff  # noqa: E402

PLAN = "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99"
COMMIT = "1c84c720cdd978160e282ca91d0eb439a7d86d11"
TREE = "a3f5dead560681b1227d243d7f87854ea2563dcd"


def _hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _file_hash(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _command_identity(path):
    value = path.lstat()
    return {
        "device": value.st_dev, "inode": value.st_ino, "uid": value.st_uid,
        "gid": value.st_gid, "mode": value.st_mode & 0o7777, "nlink": value.st_nlink,
        "size": value.st_size, "mtime_ns": value.st_mtime_ns,
        "is_symlink": path.is_symlink(),
        "link_target": os.readlink(path) if path.is_symlink() else None,
        "resolved_path": str(path.resolve()),
    }


def _binding(name):
    return {"ref": f"test:{name}", "hash": _hash(name)}


def _handoff(source, *, provider="claude"):
    service = {
        "schema": "tgw-registered-resource-service/v2", "id": "review-resources",
        "client_id": "review-client", "endpoint": "https://resources.invalid",
        "credential_env": None, "timeout_seconds": 5,
    }
    service_hash = _hash(service)
    card = ExecutionCard.create({
        "card_id": "candidate-review", "solution_id": _hash("solution"),
        "role": "independent-review", "selected_provider": provider,
        "plan_commit": PLAN,
        "resource_service": {
            "id": service["id"], "client_id": service["client_id"],
            "descriptor_hash": service_hash, "catalog_ref": "catalog:test",
            "catalog_hash": _hash("catalog"),
        },
        "bindings": {
            "plan_input": _binding("plan-input"), "plan_commit": _binding("plan-commit"),
            "plan_graph": _binding("plan-graph"), "codegraph_snapshot": _binding("codegraph"),
            "source_tree": {"ref": source.resolve().as_uri(), "hash": snapshot_hash(source)},
            "execution_environment": _binding("environment"),
            "authority_conditions": _binding("authority"),
            "candidate_evidence": _binding("candidate"), "receipt_sink": _binding("sink"),
        },
        "authority": ["read-only review"], "exclusions": ["mutation", "deployment"],
        "acceptance": ["tgw-code-review/v1"],
        "receiver_profile": {"id": "claude-code", "version": 1},
        "lease": {"id": "review-lease", "expires_at": "2027-01-01T00:00:00Z", "stop_policy": "hold"},
    })
    unsigned = {
        "schema": "tgw-execution-resource-receipt/v1", "card_hash": card.hash,
        "plan_commit": PLAN,
        "resources": {name: value for name, value in sorted(card.value["bindings"].items())},
    }
    receipt = {**unsigned, "receipt_hash": _hash(unsigned)}
    return craft_handoff(
        {"card": card.value, "resource_receipt": receipt, "resource_service": service},
        receiver_identity=f"{provider}:tgw-review",
    )


def _fixture(tmp_path, *, malformed=False, failed=False, mutate=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "snapshot"
    source.mkdir()
    (source / "app.py").write_text("answer = 42\n")
    expected = snapshot_hash(source)
    (source / "app.py").chmod(0o444)
    source.chmod(0o555)
    review = {
        "schema": "tgw-code-review/v1", "verdict": "FAIL" if failed else "PASS",
        "snapshot_hash": expected, "summary": "reviewed exact source",
        "findings": ([{"severity": "high", "path": "app.py", "line": 1, "message": "bad"}] if failed else []),
    }
    if malformed:
        payload = "not-json"
    else:
        payload = json.dumps({"is_error": False, "result": json.dumps(review)})
    executable = tmp_path / "provider"
    mutation = "p=Path(sys.argv[-1])/'app.py'; p.chmod(0o644); p.write_text('changed\\n')" if mutate else ""
    executable.write_text(
        "#!/usr/bin/python3\nimport sys\nfrom pathlib import Path\n"
        + mutation + "\n"
        + f"print({payload!r})\n"
    )
    executable.chmod(0o555)
    skill = tmp_path / "SKILL.md"
    skill.write_text("provider-neutral tgw-review\n")
    skill.chmod(0o444)
    mcp = tmp_path / "mcp.json"
    mcp.write_text('{"tgw-context":"healthy"}\n')
    mcp.chmod(0o444)
    environment = {"HOME": str(tmp_path), "PATH": "/usr/bin", "USER": "claude", "LOGNAME": "claude", "LANG": "C"}
    identity = {
        "schema": IDENTITY_SCHEMA, "provider": "claude", "authenticated": True,
        "account_identity": _hash("claude-account"), "version": "2.1.223",
        "skill": "tgw-review", "configured_command_path": str(executable),
        "configured_command_identity": _command_identity(executable),
        "resolved_executable_path": str(executable),
        "executable_sha256": _file_hash(executable), "skill_path": str(skill),
        "skill_sha256": _file_hash(skill), "mcp_config_path": str(mcp),
        "mcp_config_sha256": _file_hash(mcp), "trusted_uid": os.getuid(),
        "trusted_gid": os.getgid(), "environment_sha256": _hash(environment),
    }
    return source, executable, identity, environment


def _run(source, executable, identity, environment, *, handoff=None, provider="claude"):
    selected_handoff = handoff or _handoff(source, provider=provider)
    def publish(execution):
        return {
            "schema": "tgw-governed-review-publication/v1",
            "sink_ref": selected_handoff["card"]["bindings"]["receipt_sink"]["ref"],
            "execution_hash": execution["execution_hash"],
            "artifact_ref": "candidate:review-execution",
            "artifact_hash": _hash(execution),
        }
    return run_governed_review(
        selected_handoff, snapshot=source,
        source_commit=COMMIT, source_tree=TREE, plan_commit=PLAN, provider=provider,
        provider_identity=identity,
        provider_argv=[str(executable), "{prompt}", "{snapshot}"],
        environment=environment, trusted_uid=os.getuid(), trusted_gid=os.getgid(),
        publish_execution=publish,
        timeout_seconds=5, now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )


def test_provider_neutral_governed_review_captures_exact_execution(tmp_path):
    values = _fixture(tmp_path)
    execution = _run(*values)
    assert validate_execution(execution)["review"]["verdict"] == "PASS"
    assert execution["provider"] == "claude"


def test_stale_context_and_source_xy_hold(tmp_path):
    values = _fixture(tmp_path)
    original_handoff = _handoff(values[0])
    handoff = json.loads(json.dumps(original_handoff))
    handoff["card"]["plan_commit"] = "0" * 40
    with pytest.raises(ReviewRunnerError):
        _run(*values, handoff=handoff)
    values[0].chmod(0o755)
    (values[0] / "app.py").chmod(0o644)
    (values[0] / "app.py").write_text("exchanged\n")
    (values[0] / "app.py").chmod(0o444)
    values[0].chmod(0o555)
    with pytest.raises(ReviewRunnerError, match="source X"):
        _run(*values, handoff=original_handoff)


def test_provider_identity_and_malformed_output_hold(tmp_path):
    values = list(_fixture(tmp_path))
    values[2] = {**values[2], "provider": "codex"}
    with pytest.raises(ReviewRunnerError, match="account or skill"):
        _run(*values)
    values = _fixture(tmp_path / "malformed", malformed=True)
    with pytest.raises(ReviewRunnerError, match="malformed"):
        _run(*values)


def test_source_mutation_holds_and_failed_verdict_is_retained(tmp_path):
    values = _fixture(tmp_path / "mutation", mutate=True)
    with pytest.raises(ReviewRunnerError, match="snapshot|source X|changed"):
        _run(*values)
    values = _fixture(tmp_path / "failed", failed=True)
    assert _run(*values)["review"]["verdict"] == "FAIL"

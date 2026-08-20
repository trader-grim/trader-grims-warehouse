import base64
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import tgw.governed_review_adapter as governed_adapter
from tgw.candidate_receipt_sink import (
    CandidateReceiptSinkError,
    validate_governed_execution_bundle,
    validate_independent_review_evidence_bundle,
    verify_independent_review_evidence_bundle,
)
from tgw.execution_resources import (
    RESOURCE_SERVICE_CAPABILITIES,
    issue_harness_retrieval_attestation,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
)
from tgw.governed_execution_receipt import (
    verify_candidate_governed_execution_receipt,
)
from tgw.governed_review_adapter import (
    IDENTITY_SCHEMA,
    ReviewRunnerError,
    _identity,
    _walk_held_tree,
    execute_request,
    run_governed_review,
    snapshot_hash,
    validate_execution,
    validate_execution_handoff_binding,
)
from tgw.review_snapshot import snapshot_hash as portable_snapshot_hash
from tgw.review_snapshot import snapshot_preimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-services/providers/promptcraft"))
from promptcraft.handoff import ExecutionCard, craft_handoff  # noqa: E402

PLAN = "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99"
COMMIT = "1c84c720cdd978160e282ca91d0eb439a7d86d11"
TREE = "a3f5dead560681b1227d243d7f87854ea2563dcd"
_CONTEXT_SIGNING_SEED = bytes.fromhex("42" * 32)


def _hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_governed_snapshot_hash_matches_canonical_unambiguous_framing(tmp_path):
    source = tmp_path / "snapshot"
    source.mkdir(mode=0o755)
    (source / "a").write_bytes(b"X\0b\0Y")
    (source / "a").chmod(0o644)

    assert snapshot_hash(source) == portable_snapshot_hash(source)

    other = tmp_path / "other"
    other.mkdir(mode=0o755)
    (other / "a").write_bytes(b"X")
    (other / "a").chmod(0o644)
    (other / "b").write_bytes(b"Y")
    (other / "b").chmod(0o644)
    assert snapshot_hash(source) != snapshot_hash(other)


def _public_key(private_key):
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


def _context_signing_key():
    return Ed25519PrivateKey.from_private_bytes(_CONTEXT_SIGNING_SEED)


def _resource_service_catalog():
    private_key = _context_signing_key()
    service = {
        "schema": "tgw-registered-resource-service/v2",
        "id": "review-resources", "client_id": "review-client",
        "endpoint": "http://127.0.0.1:18788",
        "credential_env": "TGW_TEST_CONTEXT_CREDENTIAL", "timeout_seconds": 5,
    }
    catalog = {
        "schema": "tgw-registered-resource-service-catalog/v3",
        "catalog_ref": "catalog:test", "plan_commit": PLAN,
        "services": [{
            "id": service["id"], "client_id": service["client_id"],
            "descriptor_hash": resource_service_descriptor_hash(service),
            "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
            "attestation_key_id": "context-test-key",
            "attestation_public_key": _public_key(private_key),
        }],
    }
    return service, catalog


def _signed_receipt(unsigned, private_key):
    receipt_hash = _hash(unsigned)
    signature = private_key.sign(
        json.dumps(
            {**unsigned, "receipt_hash": receipt_hash},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    )
    return {
        **unsigned, "receipt_hash": receipt_hash,
        "signature": base64.b64encode(signature).decode(),
    }


def _context_attestation(identity, private_key, handoff, run_id, challenge):
    card = handoff["card"]
    service = identity["context_bundle_service"]
    attestation = issue_harness_retrieval_attestation(
        {
            "schema": "tgw-registered-resource-retrieval-attestation/v3",
            "service_id": service["service_id"], "client_id": service["client_id"],
            "run_id": run_id, "card_hash": card["card_hash"],
            "role": "independent-review",
            "execution_identity": (
                f"governed-review:{challenge}:"
                f"uid={identity['sandbox_identity']['uid']}:"
                f"gid={identity['sandbox_identity']['gid']}"
            ),
            "handoff_hash": handoff["handoff_hash"],
            "resource_receipt_hash": handoff["resource_receipt"]["receipt_hash"],
            "resources": {
                name: card["bindings"][name] for name in sorted(card["bindings"])
            },
            "attestation_key_id": service["attestation_key_id"],
        },
        signing_private_key=private_key,
    )
    return attestation


def _context_service_bundle(
    attestation, *, challenge, skill_contract_hash, resource_contents,
):
    resources = {
        name: {
            **binding,
            "content_sha256": "sha256:" + hashlib.sha256(
                resource_contents[name]
            ).hexdigest(),
            "content_base64": base64.b64encode(resource_contents[name]).decode(),
        }
        for name, binding in attestation["resources"].items()
    }
    unsigned = {
        "schema": "tgw-context-review-resource-bundle/v1",
        "client_id": "review-client", "challenge": challenge,
        "skill_contract_hash": skill_contract_hash,
        "retrieval_attestation": attestation, "resources": resources,
    }
    return {**unsigned, "bundle_hash": _hash(unsigned)}


def _resource_contents(handoff, source, sink_descriptor):
    contents = {
        "plan_input": b"resource:plan-input",
        "plan_commit": b"resource:plan-commit",
        "plan_graph": b"resource:plan-graph",
        "codegraph_snapshot": b"resource:codegraph",
        "authority_conditions": b"resource:authority",
        "candidate_evidence": b"resource:candidate",
    }
    contents["source_tree"] = snapshot_preimage(source)
    environment_ref = handoff["card"]["bindings"]["execution_environment"]["ref"]
    if environment_ref.startswith("file://"):
        contents["execution_environment"] = Path(
            environment_ref.removeprefix("file://")
        ).read_bytes()
    contents["receipt_sink"] = json.dumps(
        {name: value for name, value in sink_descriptor.items() if name != "descriptor_hash"},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return contents


def _context_grant(identity, handoff, skill_contract_hash, *, now=None):
    now = now or datetime.now(timezone.utc)
    issued = now - timedelta(seconds=1)
    request = {
        "schema": "tgw-context-review-broker-request/v2",
        "client_id": identity["context_bundle_service"]["client_id"],
        "challenge": "c" * 64,
        "skill_contract_hash": skill_contract_hash,
        "card_hash": handoff["card"]["card_hash"], "role": "independent-review",
        "execution_identity": (
            f"governed-review:{'c' * 64}:"
            f"uid={identity['sandbox_identity']['uid']}:"
            f"gid={identity['sandbox_identity']['gid']}"
        ),
        "handoff_hash": handoff["handoff_hash"],
        "resource_receipt_hash": handoff["resource_receipt"]["receipt_hash"],
        "resource_service_catalog_ref": identity[
            "context_bundle_service"
        ]["resource_service_catalog_ref"],
        "resource_service_catalog_hash": identity[
            "context_bundle_service"
        ]["resource_service_catalog_hash"],
        "resources": {
            name: handoff["card"]["bindings"][name]
            for name in sorted(handoff["card"]["bindings"])
        },
        "issued_at": issued.isoformat(), "not_before": issued.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }
    return {
        "schema": "tgw-governed-review-context-grant/v1",
        "request": request, "request_hash": _hash(request),
    }


def _fixture_skill_contract_hash(identity):
    skill_path = Path(identity["artifacts"]["skill_contract"]["resolved_path"])
    skill_raw = (skill_path / "SKILL.md").read_bytes()
    rendered = (
        f"--- SKILL.md (sha256:{hashlib.sha256(skill_raw).hexdigest()}) ---\n"
        + skill_raw.decode()
    )
    return "sha256:" + hashlib.sha256(rendered.encode()).hexdigest()


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


def _policy(path):
    value = path.stat()
    return {"uid": value.st_uid, "gid": value.st_gid, "forbidden_mode": 0o022}


def _file_artifact(path):
    resolved = path.resolve()
    return {
        "kind": "file", "configured_path": str(path),
        "configured_identity": _command_identity(path), "resolved_path": str(resolved),
        "resolved_identity": _identity(resolved), "content_sha256": _file_hash(resolved),
        "policy": _policy(resolved),
    }


def _secret_artifact(path):
    value = _file_artifact(path)
    value.pop("content_sha256")
    value["kind"] = "secret-file"
    value["secret_ref"] = "secret:test-provider-credential"
    return value


def _tree_artifact(path):
    resolved = path.resolve()
    descriptor = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY)
    try:
        manifest, _ = _walk_held_tree(
            descriptor, policy=_policy(resolved), label="test skill",
            max_file_bytes=1024 * 1024,
        )
    finally:
        os.close(descriptor)
    return {
        "kind": "tree", "configured_path": str(path),
        "configured_identity": _command_identity(path), "resolved_path": str(resolved),
        "resolved_identity": _identity(resolved), "manifest": manifest,
        "manifest_hash": _hash(manifest), "policy": _policy(resolved),
    }


def _binding(name):
    return {
        "ref": f"test:{name}",
        "hash": "sha256:" + hashlib.sha256(f"resource:{name}".encode()).hexdigest(),
    }


def _sink_descriptor():
    unsigned = {
        "schema": "tgw-governed-review-evidence-sink-client/v1",
        "sink_ref": "test:governed-review-x",
        "endpoint": "https://evidence.invalid",
        "credential_env": "TGW_TEST_REVIEW_X_CREDENTIAL",
        "timeout_seconds": 5,
    }
    return {**unsigned, "descriptor_hash": _hash(unsigned)}


def _handoff(source, *, provider="claude", sink_descriptor=None):
    sink_descriptor = sink_descriptor or _sink_descriptor()
    service, catalog = _resource_service_catalog()
    service_hash = resource_service_descriptor_hash(service)
    environment_path = source.parent / "execution-environment.json"
    environment_binding = (
        {"ref": environment_path.resolve().as_uri(), "hash": _file_hash(environment_path)}
        if environment_path.is_file() else _binding("environment")
    )
    card = ExecutionCard.create({
        "card_id": "candidate-review", "solution_id": _hash("solution"),
        "role": "independent-review", "selected_provider": provider,
        "plan_commit": PLAN,
        "resource_service": {
            "id": service["id"], "client_id": service["client_id"],
            "descriptor_hash": service_hash, "catalog_ref": "catalog:test",
            "catalog_hash": resource_service_catalog_hash(catalog),
        },
        "bindings": {
            "plan_input": _binding("plan-input"), "plan_commit": _binding("plan-commit"),
            "plan_graph": _binding("plan-graph"), "codegraph_snapshot": _binding("codegraph"),
            "source_tree": {"ref": f"git:tree:{TREE}", "hash": snapshot_hash(source)},
            "execution_environment": environment_binding,
            "authority_conditions": _binding("authority"),
            "candidate_evidence": _binding("candidate"),
            "receipt_sink": {
                "ref": sink_descriptor["sink_ref"],
                "hash": sink_descriptor["descriptor_hash"],
            },
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


def _review_packet(source, *, provider="claude"):
    unsigned = {
        "schema": "tgw-integrated-candidate-review-packet/v1",
        "status": "EXECUTABLE", "candidate_manifest_hash": _hash("candidate-manifest"),
        "candidate_source": {
            "commit": COMMIT, "tree": TREE, "archive_sha256": _hash("archive"),
        },
        "plan": {
            "commit": PLAN, "solution_hash": _hash("solution"),
            "closure_hash": _hash("closure"),
        },
        "snapshot": {"ref": source.resolve().as_uri(), "hash": snapshot_hash(source)},
        "required_dimensions": ["semantic", "security"],
        "review_contract": {
            "schema": "tgw-integrated-candidate-review-result/v2",
            "pass_requires": "both dimensions PASS with zero findings",
            "source_mutation": "forbidden", "authority_broadening": "forbidden",
        },
        "selected_provider": provider,
        "receiver_profile": {"id": "test-provider", "version": 1},
        "runner_argv": ["governed-review"], "hold": None,
    }
    return {**unsigned, "packet_hash": _hash(unsigned)}


def _fixture(
    tmp_path, *, malformed=False, failed=False, mutate=False, detach=False,
    consume_context=True,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "snapshot"
    source.mkdir()
    (source / "app.py").write_text("answer = 42\n")
    (source / "app.py").chmod(0o444)
    source.chmod(0o555)
    expected = snapshot_hash(source)
    review = {
        "schema": "tgw-code-review/v1", "verdict": "FAIL" if failed else "PASS",
        "snapshot_hash": expected, "summary": "reviewed exact source",
        "findings": ([{"severity": "high", "path": "app.py", "line": 1, "message": "bad"}] if failed else []),
    }
    if malformed:
        payload = "not-json"
    else:
        payload_value = {"is_error": False, "result": json.dumps(review)}
        if consume_context:
            payload_value["context_run_id"] = "context-run"
        payload = json.dumps(payload_value)
    executable = tmp_path / "provider"
    source_code = tmp_path / "provider.c"
    mutation_code = (
        'if (chmod("/tmp/workspace/app.py", 0644) != 0) return 3;'
        if mutate else ""
    )
    detach_code = (
        "if (fork() == 0) { setsid(); close(1); close(2); sleep(30); _exit(0); }"
        if detach else ""
    )
    source_code.write_text(
        "#include <stdio.h>\n#include <string.h>\n#include <sys/stat.h>\n"
        "#include <unistd.h>\nint main(int argc, char **argv) {"
        + mutation_code + detach_code
        + f"puts({json.dumps(payload)}); return 0; }}\n"
    )
    subprocess.run(["cc", "-static", "-o", executable, source_code], check=True)
    executable.chmod(0o555)
    skill = tmp_path / "tgw-review"
    skill.mkdir()
    (skill / "SKILL.md").write_text("provider-neutral tgw-review\n")
    (skill / "SKILL.md").chmod(0o444)
    skill.chmod(0o555)
    skill_artifact = _tree_artifact(skill)
    context_private_key = _context_signing_key()
    mcp = tmp_path / "mcp.json"
    mcp_endpoint = "http://127.0.0.1:18766/sse"
    broker_endpoint = "http://127.0.0.1:18788"
    mcp.write_text(json.dumps({
        "mcpServers": {"tgw-context": {
            "type": "sse", "url": mcp_endpoint,
        }},
    }))
    mcp.chmod(0o444)
    credential = tmp_path / "credential.json"
    credential.write_text('{"subscription":"test"}\n')
    credential.chmod(0o400)
    runtime = tmp_path / "runtime"
    for relative in ("usr", "lib", "lib64", "etc/ssl"):
        (runtime / relative).mkdir(parents=True, exist_ok=True)
    for relative in ("etc/resolv.conf", "etc/nsswitch.conf", "etc/hosts"):
        (runtime / relative).write_text("test\n")
        (runtime / relative).chmod(0o444)
    for directory in sorted((item for item in runtime.rglob("*") if item.is_dir()), reverse=True):
        directory.chmod(0o555)
    runtime.chmod(0o555)
    environment = {
        "HOME": "/home/reviewer", "PATH": "/usr/bin", "USER": "claude",
        "LOGNAME": "claude", "LANG": "C",
    }
    required_mcp_tool = "mcp__tgw-context__tgw_context_bundle"
    tool_argument = f"Read,Glob,Grep,Skill,{required_mcp_tool}"
    denied_argument = "Bash,Edit,Write,NotebookEdit"
    provider_argv = [
        str(executable), "-p", "{prompt}", "--tools", tool_argument,
        "--disallowedTools", denied_argument, "--setting-sources", "",
        "--mcp-config", "{mcp_config}", "--strict-mcp-config",
        "--add-dir", "{snapshot}", "--output-format", "json",
    ]
    account_identity = _hash("claude-account")
    resource_service, resource_catalog = _resource_service_catalog()
    context_service_unsigned = {
        "schema": "tgw-context-bundle-service/v1",
        "endpoint": resource_service["endpoint"],
        "credential_env": "TGW_TEST_CONTEXT_CREDENTIAL",
        "timeout_seconds": 5,
        "service_id": "review-resources", "client_id": "review-client",
        "broker_endpoint": broker_endpoint,
        "context_service_endpoint": mcp_endpoint,
        "resource_service_descriptor_hash": _hash(resource_service),
        "resource_service_catalog_ref": "catalog:test",
        "resource_service_catalog_hash": resource_service_catalog_hash(
            resource_catalog
        ),
        "attestation_key_id": "context-test-key",
        "attestation_public_key": _public_key(context_private_key),
    }
    context_service = {
        **context_service_unsigned,
        "descriptor_hash": _hash(context_service_unsigned),
    }
    network_unsigned = {
        "schema": "tgw-governed-review-network-environment/v1",
        "mode": "shared-host-network",
        "observed_endpoints": sorted(["https://api.anthropic.com", mcp_endpoint]),
        "endpoint_confinement": False,
    }
    network_hash = _hash(network_unsigned)
    network_environment = {**network_unsigned, "policy_hash": network_hash}
    execution_environment = tmp_path / "execution-environment.json"
    execution_environment.write_text(json.dumps({
        "schema": "tgw-governed-review-environment-authority/v1",
        "provider": "claude", "runtime_uid": os.getuid(), "runtime_gid": os.getgid(),
        "network_environment_hash": network_hash,
        "network_mode": "shared-host-network",
    }, sort_keys=True, separators=(",", ":")))
    execution_environment.chmod(0o444)
    health_now = datetime.now(timezone.utc)
    health_unsigned = {
        "schema": "tgw-governed-review-provider-health/v1", "provider": "claude",
        "account_identity": account_identity, "observed_at": health_now.isoformat(),
        "expires_at": (health_now + timedelta(minutes=10)).isoformat(), "status": "AUTHENTICATED",
    }
    provenance_unsigned = {
        "schema": "tgw-review-skill-projection-receipt/v1",
        "source_ref": "test:canonical-tgw-review",
        "source_manifest_hash": skill_artifact["manifest_hash"],
        "projection_manifest_hash": skill_artifact["manifest_hash"],
    }
    identity = {
        "schema": IDENTITY_SCHEMA, "provider": "claude",
        "account_identity": account_identity, "version": "2.1.223", "skill": "tgw-review",
        "artifacts": {
            "sandbox": _file_artifact(Path("/usr/bin/bwrap")),
            "runtime": _tree_artifact(runtime),
            "executable": _file_artifact(executable), "skill_contract": skill_artifact,
            "mcp_config": _file_artifact(mcp), "credential": _secret_artifact(credential),
            "execution_environment": _file_artifact(execution_environment),
        },
        "skill_source_provenance": {
            **provenance_unsigned,
            "projection_receipt_hash": _hash(provenance_unsigned),
        },
        "sandbox_layout": {
            "home": "/home/reviewer",
            "skill_mount": "/home/reviewer/.claude/skills/tgw-review",
            "credential_mount": "/home/reviewer/.claude/.credentials.json",
            "workspace": "/tmp/workspace",
        },
        "context_bundle_service": context_service,
        "sandbox_identity": {"uid": os.getuid(), "gid": os.getgid()},
        "environment_sha256": _hash(environment), "argv_template": provider_argv,
        "argv_template_hash": _hash(provider_argv),
        "command_policy": {
            "tool_policy": [
                "Read", "Glob", "Grep", "Skill", required_mcp_tool,
            ],
            "read_only": True,
            "settings_sources_disabled": True, "held_mcp_config": True,
            "held_skill_contract": True, "sandbox_profile_hash": _hash([
                "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
                "--unshare-cgroup",
                "--die-with-parent", "--new-session", "--tmpfs", "/", "--proc", "/proc",
                "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home",
            ]),
            "pid_namespace": True, "root_read_only": True,
            "mcp_endpoints": [mcp_endpoint],
            "context_bindings": {
                "plan_input": _binding("plan-input"),
                "plan_commit": _binding("plan-commit"),
                "plan_graph": _binding("plan-graph"),
                "codegraph_snapshot": _binding("codegraph"),
                "source_tree": {"ref": f"git:tree:{TREE}", "hash": expected},
                "execution_environment": {
                    "ref": execution_environment.resolve().as_uri(),
                    "hash": _file_hash(execution_environment),
                },
            },
            "argv_policy_fragments": [
                ["--tools", tool_argument],
                ["--disallowedTools", denied_argument],
                ["--setting-sources", ""],
                ["--mcp-config", "{mcp_config}", "--strict-mcp-config"],
            ],
            "forbidden_argv_tokens": ["--dangerously-skip-permissions"],
            "required_mcp_tools": [required_mcp_tool],
        },
        "network_environment": network_environment,
        "health": {**health_unsigned, "evidence_hash": _hash(health_unsigned)},
    }
    return source, executable, identity, environment, context_private_key


def _run(
    source, executable, identity, environment, context_private_key, *,
    handoff=None, provider="claude", sink_descriptor=None,
    context_bundle_mutator=None, context_service_available=True,
    context_grant=None, resource_bundle_mutator=None,
):
    sink_descriptor = sink_descriptor or _sink_descriptor()
    selected_handoff = handoff or _handoff(
        source, provider=provider, sink_descriptor=sink_descriptor,
    )
    skill_contract_hash = _fixture_skill_contract_hash(identity)
    grant = context_grant or _context_grant(
        identity, selected_handoff, skill_contract_hash,
    )
    resource_contents = _resource_contents(
        selected_handoff, source, sink_descriptor,
    )
    retained = {}

    def publish(execution):
        retained["execution"] = execution
        return {
            "schema": "tgw-governed-review-publication/v1",
            "sink_ref": selected_handoff["card"]["bindings"]["receipt_sink"]["ref"],
            "execution_hash": execution["execution_hash"],
            "artifact_ref": "candidate:review-execution",
            "artifact_hash": _hash(execution),
        }

    def read_context(challenge):
        if not context_service_available:
            raise ReviewRunnerError("governed review context challenge is unavailable")
        run_id = "context-run"
        attestation = _context_attestation(
            identity, context_private_key, selected_handoff, run_id, challenge,
        )
        selected = (
            context_bundle_mutator(attestation, run_id, challenge)
            if context_bundle_mutator else attestation
        )
        bundle = _context_service_bundle(
            selected, challenge=challenge,
            skill_contract_hash=skill_contract_hash,
            resource_contents=resource_contents,
        )
        return resource_bundle_mutator(bundle) if resource_bundle_mutator else bundle

    return run_governed_review(
        selected_handoff, snapshot=source,
        source_commit=COMMIT, source_tree=TREE, plan_commit=PLAN, provider=provider,
        provider_identity=identity,
        provider_argv=identity["argv_template"],
        environment=environment, trusted_uid=os.getuid(), trusted_gid=os.getgid(),
        evidence_sink_descriptor=sink_descriptor,
        publish_execution=publish,
        read_execution=lambda _publication: retained["execution"],
        read_context_bundle=read_context,
        context_grant=grant,
        timeout_seconds=5,
        environment_preflight_receipt={
            "schema": "tgw-environment-preflight-receipt/v1", "result": "PASS",
            "catalog_sha256": selected_handoff["card"]["bindings"]["execution_environment"]["hash"],
            "actor": "claude", "profile": "development", "attempt_id": "unit", "tools": [],
        },
    )


def test_provider_neutral_governed_review_captures_exact_execution(tmp_path):
    values = _fixture(tmp_path)
    execution = _run(*values)
    assert validate_execution(execution)["review"]["verdict"] == "PASS"
    assert execution["provider"] == "claude"
    assert execution["environment_preflight_receipt"] == {
        "schema": "tgw-environment-preflight-receipt/v1", "result": "PASS",
        "catalog_sha256": execution["bindings"]["execution_environment"]["hash"],
        "actor": "claude", "profile": "development", "attempt_id": "unit", "tools": [],
    }


def test_governed_execution_rejects_retained_preflight_substitution(tmp_path):
    execution = _run(*_fixture(tmp_path))
    altered = json.loads(json.dumps(execution))
    altered["environment_preflight_receipt"]["catalog_sha256"] = "sha256:" + "0" * 64
    unsigned = {name: value for name, value in altered.items() if name != "execution_hash"}
    altered["execution_hash"] = _hash(unsigned)
    with pytest.raises(ReviewRunnerError, match="environment preflight binding mismatch"):
        validate_execution(altered)


def test_root_request_path_has_an_explicit_console_entrypoint():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["scripts"]["tgw-governed-review"] == (
        "tgw.governed_review_adapter:main"
    )


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
    with pytest.raises(ReviewRunnerError, match="account/skill"):
        _run(*values)
    values = _fixture(tmp_path / "malformed", malformed=True)
    with pytest.raises(ReviewRunnerError, match="malformed"):
        _run(*values)


def test_source_mutation_holds_and_failed_verdict_is_retained(tmp_path):
    values = _fixture(tmp_path / "mutation", mutate=True)
    with pytest.raises(ReviewRunnerError, match="snapshot|source X|changed|failed"):
        _run(*values)
    values = _fixture(tmp_path / "failed", failed=True)
    assert _run(*values)["review"]["verdict"] == "FAIL"


def test_execution_cannot_rebind_codegraph_away_from_retained_card(tmp_path):
    values = _fixture(tmp_path)
    handoff = _handoff(values[0])
    execution = _run(*values, handoff=handoff)
    altered = json.loads(json.dumps(execution))
    altered["bindings"]["codegraph_snapshot"] = _binding("different-codegraph")
    unsigned = {name: value for name, value in altered.items() if name != "execution_hash"}
    altered["execution_hash"] = _hash(unsigned)
    with pytest.raises(ReviewRunnerError, match="context binding|execution/handoff binding"):
        validate_execution_handoff_binding(altered, handoff["card"], handoff)


def test_pid_namespace_reaps_sets_id_descendant(tmp_path):
    values = _fixture(tmp_path, detach=True)
    _run(*values)
    target = values[1].resolve()
    escaped = []
    for process in Path("/proc").glob("[0-9]*/exe"):
        try:
            if process.resolve() == target:
                escaped.append(process)
        except (FileNotFoundError, PermissionError):
            continue
    assert escaped == []


def test_non_test_request_composes_provider_and_pinned_sink_readback(tmp_path, monkeypatch):
    source, executable, identity, environment, context_private_key = _fixture(tmp_path)
    sink_descriptor = _sink_descriptor()
    handoff = _handoff(source, sink_descriptor=sink_descriptor)
    skill_contract_hash = _fixture_skill_contract_hash(identity)
    context_grant = _context_grant(identity, handoff, skill_contract_hash)
    resource_contents = _resource_contents(handoff, source, sink_descriptor)

    class Sink:
        retained = None
        artifacts = {}
        tamper_readback = None

        def __init__(self, _descriptor):
            pass

        def publish(self, execution):
            self.retained = execution
            return {
                "schema": "tgw-governed-review-publication/v1",
                "sink_ref": handoff["card"]["bindings"]["receipt_sink"]["ref"],
                "execution_hash": execution["execution_hash"],
                "artifact_ref": "candidate:review-execution",
                "artifact_hash": _hash(execution),
            }

        def read(self, _publication):
            return self.retained

        def publish_artifact(self, artifact_ref, value):
            self.artifacts[artifact_ref] = value
            return {"ref": artifact_ref, "content_sha256": _hash(value)}

        def read_artifact(self, pointer):
            if self.tamper_readback == "artifact" and pointer["ref"].endswith(
                ":review_report"
            ):
                return {"tampered": True}
            if self.tamper_readback == "bundle" and pointer["ref"].endswith(
                ":independent-review-evidence:v3"
            ):
                return {"tampered": True}
            if (
                self.tamper_readback == "governed-artifact"
                and pointer["ref"].endswith(":candidate_receipt")
            ):
                return {"tampered": True}
            if (
                self.tamper_readback == "governed-bundle"
                and pointer["ref"]
                == f"candidate:{COMMIT}:governed-execution:independent-review"
            ):
                return {"tampered": True}
            return self.artifacts[pointer["ref"]]

        def fetch_bytes(self, artifact_ref):
            return json.dumps(
                self.artifacts[artifact_ref], sort_keys=True, separators=(",", ":"),
            ).encode()

        def fetch_object(self, artifact_ref):
            return self.artifacts[artifact_ref]

    monkeypatch.setattr(governed_adapter, "HTTPReviewEvidenceSink", Sink)

    class ContextClient:
        def __init__(self, _descriptor):
            pass

        def read(self, challenge):
            run_id = "context-run"
            attestation = _context_attestation(
                identity, context_private_key, handoff, run_id, challenge,
            )
            return _context_service_bundle(
                attestation, challenge=challenge,
                skill_contract_hash=skill_contract_hash,
                resource_contents=resource_contents,
            )

    monkeypatch.setattr(governed_adapter, "HTTPContextBundleClient", ContextClient)
    request = {
        "schema": "tgw-governed-review-request/v1", "handoff": handoff,
        "snapshot": str(source), "source_commit": COMMIT, "source_tree": TREE,
        "plan_commit": PLAN, "provider": "claude", "provider_identity": identity,
        "provider_argv": identity["argv_template"], "environment": environment,
        "trusted_uid": os.getuid(), "trusted_gid": os.getgid(),
        "timeout_seconds": 5, "output_limit": 8 * 1024 * 1024,
        "evidence_sink": sink_descriptor,
        "review_packet": _review_packet(source),
            "resource_service_catalog": _resource_service_catalog()[1],
            "context_grant": context_grant,
            "environment_preflight_receipt": {
                "schema": "tgw-environment-preflight-receipt/v1", "result": "PASS",
                "catalog_sha256": handoff["card"]["bindings"]["execution_environment"]["hash"],
                "actor": "claude", "profile": "development", "attempt_id": "unit", "tools": [],
            },
        }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    request_path.chmod(0o444)
    subprocess.run(["sudo", "-n", "chown", "0:0", request_path], check=True)
    finalized = execute_request(request_path)
    assert finalized["execution"]["review"]["verdict"] == "PASS"
    assert finalized["governed_execution_bundle"]["role"] == "independent-review"
    assert finalized["governed_execution_bundle_pointer"]["ref"] == (
        f"candidate:{COMMIT}:governed-execution:independent-review"
    )
    governed_bundle = validate_governed_execution_bundle(
        finalized["governed_execution_bundle"]
    )
    governed_artifacts = {
        name: Sink.artifacts[governed_bundle[name]["ref"]]
        for name in (
            "candidate_receipt", "card", "resource_receipt", "role_receipt",
            "resource_service_catalog",
        )
    }
    assert verify_candidate_governed_execution_receipt(
        governed_artifacts["candidate_receipt"],
        card=governed_artifacts["card"],
        resource_receipt=governed_artifacts["resource_receipt"],
        role_receipt=governed_artifacts["role_receipt"],
        resource_service_catalog=governed_artifacts["resource_service_catalog"],
        source_commit=COMMIT, source_tree=TREE, plan_commit=PLAN,
    )["status"] == "PASS"
    assert set(finalized["evidence_bundle"]) == {
        "schema", "source_commit", "source_tree", "plan_commit",
        "review_packet", "review_report", "review_result",
        "governed_review_execution", "review_card", "review_handoff",
        "promptcraft_receipt", "bundle_hash",
    }
    assert validate_independent_review_evidence_bundle(
        finalized["evidence_bundle"],
    )["bundle_hash"] == finalized["evidence_bundle"]["bundle_hash"]
    verified = verify_independent_review_evidence_bundle(
        Sink(None), source_commit=COMMIT, source_tree=TREE, plan_commit=PLAN,
        candidate_manifest_hash=finalized["packet"]["candidate_manifest_hash"],
        independent_review_receipt=finalized["governed_review_receipt"],
    )
    assert verified["governed_review_execution_hash"] == (
        finalized["execution"]["execution_hash"]
    )

    report_ref = finalized["evidence_bundle"]["review_report"]["ref"]
    retained_report = Sink.artifacts[report_ref]
    Sink.artifacts[report_ref] = {"tampered": True}
    with pytest.raises(CandidateReceiptSinkError, match="artifact hash mismatch"):
        verify_independent_review_evidence_bundle(
            Sink(None), source_commit=COMMIT, source_tree=TREE, plan_commit=PLAN,
            candidate_manifest_hash=finalized["packet"]["candidate_manifest_hash"],
            independent_review_receipt=finalized["governed_review_receipt"],
        )
    Sink.artifacts[report_ref] = retained_report

    Sink.tamper_readback = "artifact"
    with pytest.raises(ValueError, match="review_report artifact X readback differs"):
        execute_request(request_path)
    Sink.tamper_readback = "bundle"
    with pytest.raises(ValueError, match="bundle X readback differs"):
        execute_request(request_path)
    Sink.tamper_readback = "governed-artifact"
    with pytest.raises(ValueError, match="candidate_receipt artifact X readback differs"):
        execute_request(request_path)
    Sink.tamper_readback = "governed-bundle"
    with pytest.raises(ValueError, match="governed execution bundle X readback differs"):
        execute_request(request_path)
    Sink.tamper_readback = None

    original_body = request_path.read_bytes()
    displaced = tmp_path / "request-original.json"

    def exchange_request(*_args, **_kwargs):
        request_path.rename(displaced)
        request_path.write_bytes(original_body)
        request_path.chmod(0o444)
        subprocess.run(["sudo", "-n", "chown", "0:0", request_path], check=True)
        return {"not": "an execution"}

    monkeypatch.setattr(governed_adapter, "run_governed_review", exchange_request)
    with pytest.raises(ReviewRunnerError, match="changed during composition"):
        execute_request(request_path)


def test_stale_health_mutation_tool_and_unbound_mcp_command_hold(tmp_path):
    values = list(_fixture(tmp_path))
    stale = json.loads(json.dumps(values[2]))
    stale_unsigned = {
        **{name: item for name, item in stale["health"].items() if name != "evidence_hash"},
        "expires_at": "2026-08-15T00:00:00+00:00",
    }
    stale["health"] = {**stale_unsigned, "evidence_hash": _hash(stale_unsigned)}
    with pytest.raises(ReviewRunnerError, match="health is stale"):
        _run(values[0], values[1], stale, values[3], values[4])

    mutation_tool = json.loads(json.dumps(values[2]))
    mutation_tool["command_policy"]["tool_policy"].append("Bash")
    with pytest.raises(ReviewRunnerError, match="mutation tools"):
        _run(values[0], values[1], mutation_tool, values[3], values[4])

    mcp = Path(values[2]["artifacts"]["mcp_config"]["resolved_path"])
    mcp.chmod(0o644)
    mcp.write_text(json.dumps({
        "mcpServers": {"wrong": {"command": "/tmp/unbound-provider", "args": []}},
    }))
    mcp.chmod(0o444)
    with pytest.raises(ReviewRunnerError, match="configured identity|content mismatch"):
        _run(values[0], values[1], values[2], values[3], values[4])


def test_context_closure_card_binding_and_network_environment_are_exact(tmp_path):
    source, executable, identity, environment, identity_private_key = _fixture(tmp_path)

    stale_context = json.loads(json.dumps(identity))
    stale_context["command_policy"]["context_bindings"]["codegraph_snapshot"] = (
        _binding("stale-codegraph")
    )
    with pytest.raises(ReviewRunnerError, match="context bindings are stale"):
        _run(source, executable, stale_context, environment, identity_private_key)

    invalid_network = json.loads(json.dumps(identity))
    unsigned = {
        "schema": "tgw-governed-review-network-environment/v1",
        "mode": "shared-network-enforced-endpoints",
        "observed_endpoints": ["https://api.anthropic.com"],
        "endpoint_confinement": True,
    }
    invalid_network["network_environment"] = {
        **unsigned, "policy_hash": _hash(unsigned),
    }
    with pytest.raises(ReviewRunnerError, match="network environment is invalid"):
        _run(source, executable, invalid_network, environment, identity_private_key)

    argv_mismatch = json.loads(json.dumps(identity))
    argv_mismatch["argv_template"].remove("--strict-mcp-config")
    argv_mismatch["argv_template_hash"] = _hash(argv_mismatch["argv_template"])
    with pytest.raises(ReviewRunnerError, match="policy is not enforced by argv"):
        _run(source, executable, argv_mismatch, environment, identity_private_key)


def test_adversarial_semantic_non_consumption_and_x_substitution_hold(tmp_path):
    ignored = _fixture(tmp_path / "ignored", consume_context=False)
    with pytest.raises(ReviewRunnerError, match="context challenge is unavailable"):
        _run(*ignored, context_service_available=False)

    forged = _fixture(tmp_path / "forged", consume_context="forged")
    with pytest.raises(ReviewRunnerError, match="context challenge is unavailable"):
        _run(*forged, context_service_available=False)

    source, executable, identity, environment, private_key = _fixture(
        tmp_path / "substitution",
    )
    original_sink = _sink_descriptor()
    handoff = _handoff(source, sink_descriptor=original_sink)
    substituted_unsigned = {
        **{key: value for key, value in original_sink.items() if key != "descriptor_hash"},
        "endpoint": "https://echo.invalid",
    }
    substituted = {
        **substituted_unsigned, "descriptor_hash": _hash(substituted_unsigned),
    }
    with pytest.raises(ReviewRunnerError, match="differs from the review card"):
        _run(
            source, executable, identity, environment, private_key,
            handoff=handoff, sink_descriptor=substituted,
        )


def test_context_grant_is_preissued_exact_and_fresh(tmp_path):
    values = _fixture(tmp_path)
    handoff = _handoff(values[0])
    skill_hash = _fixture_skill_contract_hash(values[2])
    now = datetime.now(timezone.utc)
    stale = _context_grant(
        values[2], handoff, skill_hash, now=now - timedelta(minutes=20),
    )
    with pytest.raises(ReviewRunnerError, match="grant is stale"):
        _run(*values, handoff=handoff, context_grant=stale)
    future = _context_grant(
        values[2], handoff, skill_hash, now=now + timedelta(minutes=5),
    )
    with pytest.raises(ReviewRunnerError, match="grant is stale"):
        _run(*values, handoff=handoff, context_grant=future)
    overlong = _context_grant(values[2], handoff, skill_hash, now=now)
    overlong["request"]["expires_at"] = (now + timedelta(minutes=20)).isoformat()
    overlong["request_hash"] = _hash(overlong["request"])
    with pytest.raises(ReviewRunnerError, match="grant is stale"):
        _run(*values, handoff=handoff, context_grant=overlong)
    execution = _run(*values, handoff=handoff)
    substituted = json.loads(json.dumps(execution))
    substituted["invocation"]["context_grant"]["request"]["challenge"] = "d" * 64
    unsigned = {
        name: value for name, value in substituted.items() if name != "execution_hash"
    }
    substituted["execution_hash"] = _hash(unsigned)
    with pytest.raises(ReviewRunnerError, match="context grant"):
        validate_execution(substituted)


def test_context_comparison_report_and_shared_network_are_exact(tmp_path):
    source, executable, identity, environment, private_key = _fixture(tmp_path)

    def substituted_bytes(bundle):
        changed = json.loads(json.dumps(bundle))
        raw = b"attacker-controlled resource"
        changed["resources"]["plan_graph"]["content_base64"] = base64.b64encode(
            raw
        ).decode()
        changed["resources"]["plan_graph"]["content_sha256"] = (
            "sha256:" + hashlib.sha256(raw).hexdigest()
        )
        unsigned = {
            name: value for name, value in changed.items() if name != "bundle_hash"
        }
        changed["bundle_hash"] = _hash(unsigned)
        return changed

    with pytest.raises(ReviewRunnerError, match="resource content differs"):
        _run(
            source, executable, identity, environment, private_key,
            resource_bundle_mutator=substituted_bytes,
        )

    def stale_context(attestation, _run_id, _challenge):
        changed = {
            key: value for key, value in attestation.items()
            if key not in {"attestation_hash", "signature"}
        }
        changed["resources"] = json.loads(json.dumps(changed["resources"]))
        changed["resources"]["codegraph_snapshot"] = _binding("wrong-codegraph")
        return issue_harness_retrieval_attestation(
            changed, signing_private_key=private_key,
        )

    with pytest.raises(
        ReviewRunnerError,
        match="context attestation|bundle readback differs|resource binding differs",
    ):
        _run(
            source, executable, identity, environment, private_key,
            context_bundle_mutator=stale_context,
        )

    def root_runtime(attestation, _run_id, _challenge):
        changed = {
            key: value for key, value in attestation.items()
            if key not in {"attestation_hash", "signature"}
        }
        changed["execution_identity"] = (
            f"governed-review:{changed['execution_identity'].split(':')[1]}:uid=0:gid=0"
        )
        return issue_harness_retrieval_attestation(
            changed, signing_private_key=private_key,
        )

    with pytest.raises(ReviewRunnerError, match="context attestation|bundle readback differs"):
        _run(
            source, executable, identity, environment, private_key,
            context_bundle_mutator=root_runtime,
        )

    execution = _run(source, executable, identity, environment, private_key)
    malformed_report = json.loads(json.dumps(execution))
    malformed_report["review"]["findings"] = [{"message": "missing required fields"}]
    unsigned_execution = {
        key: value for key, value in malformed_report.items() if key != "execution_hash"
    }
    malformed_report["execution_hash"] = _hash(unsigned_execution)
    with pytest.raises(ReviewRunnerError, match="finding is invalid"):
        validate_execution(malformed_report)

    false_enforcement = json.loads(json.dumps(identity))
    false_enforcement["network_environment"]["enforcement_receipt"] = {
        "status": "ENFORCED",
    }
    with pytest.raises(ReviewRunnerError, match="network environment"):
        _run(
            source, executable, false_enforcement, environment, private_key,
        )

    substituted_service = json.loads(json.dumps(identity))
    context_service = substituted_service["context_bundle_service"]
    context_service["client_id"] = "attacker-review-client"
    resource_descriptor = {
        "schema": "tgw-registered-resource-service/v2",
        "id": context_service["service_id"],
        "client_id": context_service["client_id"],
        "endpoint": context_service["endpoint"],
        "credential_env": context_service["credential_env"],
        "timeout_seconds": context_service["timeout_seconds"],
    }
    context_service["resource_service_descriptor_hash"] = (
        resource_service_descriptor_hash(resource_descriptor)
    )
    context_unsigned = {
        name: value for name, value in context_service.items()
        if name != "descriptor_hash"
    }
    context_service["descriptor_hash"] = _hash(context_unsigned)
    with pytest.raises(ReviewRunnerError, match="card resource service differs"):
        _run(
            source, executable, substituted_service, environment, private_key,
        )

    missing_tools = json.loads(json.dumps(identity))
    missing_tools["command_policy"]["required_mcp_tools"] = []
    with pytest.raises(ReviewRunnerError, match="command policy is invalid"):
        _run(source, executable, missing_tools, environment, private_key)

    wrong_identity = json.loads(json.dumps(identity))
    wrong_identity["sandbox_identity"]["uid"] += 1
    with pytest.raises(ReviewRunnerError, match="environment authority is invalid"):
        _run(source, executable, wrong_identity, environment, private_key)

    retained = _run(source, executable, identity, environment, private_key)
    retained["registered_resource_retrieval"]["runtime_identity"]["uid"] += 1
    context_unsigned = {
        key: value for key, value in retained["registered_resource_retrieval"].items()
        if key != "bundle_hash"
    }
    retained["registered_resource_retrieval"]["bundle_hash"] = _hash(context_unsigned)
    execution_unsigned = {
        key: value for key, value in retained.items() if key != "execution_hash"
    }
    retained["execution_hash"] = _hash(execution_unsigned)
    with pytest.raises(ReviewRunnerError, match="context comparison"):
        validate_execution(retained)

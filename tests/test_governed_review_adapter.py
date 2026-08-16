import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import tgw.governed_review_adapter as governed_adapter
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


def _fixture(tmp_path, *, malformed=False, failed=False, mutate=False, detach=False):
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
        payload = json.dumps({"is_error": False, "result": json.dumps(review)})
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
        "#include <stdio.h>\n#include <sys/stat.h>\n#include <unistd.h>\nint main(void) {"
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
    mcp = tmp_path / "mcp.json"
    mcp_command = "/opt/tgw-context/bin/tgw-context-mcp"
    mcp.write_text(json.dumps({
        "mcpServers": {"tgw-context": {"command": mcp_command, "args": []}},
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
    context = tmp_path / "context-provider"
    (context / "bin").mkdir(parents=True)
    (context / "bin/tgw-context-mcp").write_text("protected context provider\n")
    (context / "bin/tgw-context-mcp").chmod(0o555)
    (context / "bin").chmod(0o555)
    context.chmod(0o555)
    environment = {
        "HOME": "/home/reviewer", "PATH": "/usr/bin", "USER": "claude",
        "LOGNAME": "claude", "LANG": "C",
    }
    tool_argument = "Read,Glob,Grep,Skill,mcp__tgw-context__brief"
    denied_argument = "Bash,Edit,Write,NotebookEdit"
    provider_argv = [
        str(executable), "-p", "{prompt}", "--tools", tool_argument,
        "--disallowedTools", denied_argument, "--setting-sources", "",
        "--mcp-config", "{mcp_config}", "--strict-mcp-config",
        "--add-dir", "{snapshot}", "--output-format", "json",
    ]
    account_identity = _hash("claude-account")
    health_unsigned = {
        "schema": "tgw-governed-review-provider-health/v1", "provider": "claude",
        "account_identity": account_identity, "observed_at": "2026-08-16T00:00:00+00:00",
        "expires_at": "2026-08-17T00:00:00+00:00", "status": "AUTHENTICATED",
    }
    skill_artifact = _tree_artifact(skill)
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
            "context_provider": _tree_artifact(context),
            "executable": _file_artifact(executable), "skill_contract": skill_artifact,
            "mcp_config": _file_artifact(mcp), "credential": _secret_artifact(credential),
        },
        "skill_source_provenance": {
            **provenance_unsigned,
            "projection_receipt_hash": _hash(provenance_unsigned),
        },
        "sandbox_layout": {
            "home": "/home/reviewer",
            "skill_mount": "/home/reviewer/.claude/skills/tgw-review",
            "credential_mount": "/home/reviewer/.claude/.credentials.json",
            "workspace": "/tmp/workspace", "context_root": "/opt/tgw-context",
        },
        "environment_sha256": _hash(environment), "argv_template": provider_argv,
        "argv_template_hash": _hash(provider_argv),
        "command_policy": {
            "tool_policy": [
                "Read", "Glob", "Grep", "Skill", "mcp__tgw-context__brief",
            ],
            "read_only": True,
            "settings_sources_disabled": True, "held_mcp_config": True,
            "held_skill_contract": True, "sandbox_profile_hash": _hash([
                "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
                "--die-with-parent", "--new-session", "--tmpfs", "/", "--proc", "/proc",
                "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home",
            ]),
            "pid_namespace": True, "root_read_only": True,
            "mcp_commands": [mcp_command],
            "context_bindings": {
                "plan_input": _binding("plan-input"),
                "plan_commit": _binding("plan-commit"),
                "plan_graph": _binding("plan-graph"),
                "codegraph_snapshot": _binding("codegraph"),
                "source_tree": {"ref": source.resolve().as_uri(), "hash": expected},
                "execution_environment": _binding("environment"),
            },
            "argv_policy_fragments": [
                ["--tools", tool_argument],
                ["--disallowedTools", denied_argument],
                ["--setting-sources", ""],
                ["--mcp-config", "{mcp_config}", "--strict-mcp-config"],
            ],
            "forbidden_argv_tokens": ["--dangerously-skip-permissions"],
        },
        "network_policy": {
            "schema": "tgw-governed-review-network-policy/v1",
            "mode": "shared-network-admitted-endpoints",
            "endpoints": ["https://api.anthropic.com"],
            "policy_hash": _hash({
                "schema": "tgw-governed-review-network-policy/v1",
                "mode": "shared-network-admitted-endpoints",
                "endpoints": ["https://api.anthropic.com"],
            }),
        },
        "health": {**health_unsigned, "evidence_hash": _hash(health_unsigned)},
    }
    return source, executable, identity, environment


def _run(source, executable, identity, environment, *, handoff=None, provider="claude"):
    selected_handoff = handoff or _handoff(source, provider=provider)
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
    return run_governed_review(
        selected_handoff, snapshot=source,
        source_commit=COMMIT, source_tree=TREE, plan_commit=PLAN, provider=provider,
        provider_identity=identity,
        provider_argv=identity["argv_template"],
        environment=environment, trusted_uid=os.getuid(), trusted_gid=os.getgid(),
        publish_execution=publish,
        read_execution=lambda _publication: retained["execution"],
        timeout_seconds=5,
    )


def test_provider_neutral_governed_review_captures_exact_execution(tmp_path):
    values = _fixture(tmp_path)
    execution = _run(*values)
    assert validate_execution(execution)["review"]["verdict"] == "PASS"
    assert execution["provider"] == "claude"


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
    source, executable, identity, environment = _fixture(tmp_path)
    handoff = _handoff(source)

    class Sink:
        retained = None

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

    monkeypatch.setattr(governed_adapter, "HTTPReviewEvidenceSink", Sink)
    request = {
        "schema": "tgw-governed-review-request/v1", "handoff": handoff,
        "snapshot": str(source), "source_commit": COMMIT, "source_tree": TREE,
        "plan_commit": PLAN, "provider": "claude", "provider_identity": identity,
        "provider_argv": identity["argv_template"], "environment": environment,
        "trusted_uid": os.getuid(), "trusted_gid": os.getgid(),
        "timeout_seconds": 5, "output_limit": 8 * 1024 * 1024,
        "evidence_sink": {"test": True},
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    request_path.chmod(0o444)
    subprocess.run(["sudo", "-n", "chown", "0:0", request_path], check=True)
    assert execute_request(request_path)["review"]["verdict"] == "PASS"

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
        _run(values[0], values[1], stale, values[3])

    mutation_tool = json.loads(json.dumps(values[2]))
    mutation_tool["command_policy"]["tool_policy"].append("Bash")
    with pytest.raises(ReviewRunnerError, match="mutation tools"):
        _run(values[0], values[1], mutation_tool, values[3])

    mcp = Path(values[2]["artifacts"]["mcp_config"]["resolved_path"])
    mcp.chmod(0o644)
    mcp.write_text(json.dumps({
        "mcpServers": {"wrong": {"command": "/tmp/unbound-provider", "args": []}},
    }))
    mcp.chmod(0o444)
    with pytest.raises(ReviewRunnerError, match="configured identity|content mismatch"):
        _run(values[0], values[1], values[2], values[3])


def test_context_closure_card_binding_and_network_policy_are_exact(tmp_path):
    source, executable, identity, environment = _fixture(tmp_path)

    stale_context = json.loads(json.dumps(identity))
    stale_context["command_policy"]["context_bindings"]["codegraph_snapshot"] = (
        _binding("stale-codegraph")
    )
    with pytest.raises(ReviewRunnerError, match="context bindings are stale"):
        _run(source, executable, stale_context, environment)

    invalid_network = json.loads(json.dumps(identity))
    unsigned = {
        "schema": "tgw-governed-review-network-policy/v1",
        "mode": "shared-network-admitted-endpoints",
        "endpoints": ["https://user:secret@api.anthropic.com"],
    }
    invalid_network["network_policy"] = {
        **unsigned, "policy_hash": _hash(unsigned),
    }
    with pytest.raises(ReviewRunnerError, match="network policy is invalid"):
        _run(source, executable, invalid_network, environment)

    context_command = Path(
        identity["artifacts"]["context_provider"]["resolved_path"]
    ) / "bin/tgw-context-mcp"
    context_command.chmod(0o444)
    non_executable = json.loads(json.dumps(identity))
    non_executable["artifacts"]["context_provider"] = _tree_artifact(
        context_command.parents[1]
    )
    with pytest.raises(ReviewRunnerError, match="outside the held context closure"):
        _run(source, executable, non_executable, environment)

    argv_mismatch = json.loads(json.dumps(identity))
    argv_mismatch["argv_template"].remove("--strict-mcp-config")
    argv_mismatch["argv_template_hash"] = _hash(argv_mismatch["argv_template"])
    with pytest.raises(ReviewRunnerError, match="policy is not enforced by argv"):
        _run(source, executable, argv_mismatch, environment)

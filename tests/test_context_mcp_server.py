from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.sse import sse_client

from tgw import context_mcp_server as context
from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    card_resource_receipt,
    content_hash,
    issue_harness_retrieval_attestation,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Context Test", "-c", "user.email=context@example.invalid", "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def bound_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path | str]:
    plan_repository = tmp_path / "plans"
    for path, content in {
        "plan/SPEC-plan-capability-graph-v2.md": "# Plan v2\n",
        "plan/TGW-Master-Plan.md": "# Master Plan\n",
        "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml": (
            "work_units:\n"
            "  - id: W10\n"
            "    title: Prove the canonical gate\n"
            "  - id: W11\n"
            "    title: Cut over consumers\n"
            "    requires: [W10]\n"
        ),
        "plan/pp/PP-CONTEXT-001.md": "# PP\n",
        "reference/context.md": "# Context\n",
    }.items():
        target = plan_repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(plan_repository, "init", "-q")
    approved = _commit(plan_repository, "approved")
    materialization = tmp_path / "approved"
    _git(plan_repository, "worktree", "add", "--detach", str(materialization), approved)
    (plan_repository / "evidence.md").write_text("later evidence\n")
    evidence_head = _commit(plan_repository, "evidence")

    source = tmp_path / "source"
    (source / "src" / "fixture").mkdir(parents=True)
    (source / "src" / "fixture" / "provider.py").write_text("class ContextProvider:\n    pass\n")
    (source / "docs" / "runbooks").mkdir(parents=True)
    (source / "docs" / "runbooks" / "context.md").write_text("# Context\n\nCommitted runbook.\n")
    _git(source, "init", "-q")
    source_commit = _commit(source, "source")

    monkeypatch.setenv("TGW_CONTEXT_PLAN_ROOT", str(materialization))
    monkeypatch.setenv("TGW_CONTEXT_PLAN_REPOSITORY", str(plan_repository))
    monkeypatch.setenv("TGW_CONTEXT_PLAN_COMMIT", approved)
    monkeypatch.setenv("TGW_CONTEXT_PLAN_SOLUTION", "sha256:" + "a" * 64)
    monkeypatch.setenv("TGW_CONTEXT_SOURCE_ROOT", str(source))
    monkeypatch.setenv("TGW_CONTEXT_RUNTIME_ROOT", str(tmp_path / "runtime"))
    context._code_snapshot.cache_clear()
    return {"approved": approved, "evidence_head": evidence_head, "source": source, "source_commit": source_commit}


def test_status_binds_approved_plan_evidence_and_committed_source(bound_context):
    status = context.context_status()
    assert status["plan"]["approved_commit"] == bound_context["approved"]
    assert status["plan"]["approved_solution_hash"] == "sha256:" + "a" * 64
    assert status["plan"]["evidence_head"] == bound_context["evidence_head"]
    assert status["source"]["commit"] == bound_context["source_commit"]
    assert status["code_graph"]["commit"] == bound_context["source_commit"]
    assert status["scope_semantics"]["platform_w11_completion_implies_master_plan_completion"] is False


def test_context_plan_graph_indexes_approved_execution_work_unit(bound_context):
    result = context.plan_graph("W11")
    assert result["status"] == "matched"
    assert result["plan_commit"] == bound_context["approved"]
    candidate = result["candidates"][0]
    assert candidate["node_id"] == "work-unit:W11"
    assert candidate["citation"]["path"] == "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml"


def test_committed_plan_runbook_and_codegraph_ignore_dirty_bytes(bound_context):
    source = bound_context["source"]
    assert isinstance(source, Path)
    (source / "src" / "fixture" / "provider.py").write_text("broken python !!!\n")
    (source / "docs" / "runbooks" / "context.md").write_text("dirty bytes\n")
    assert context.code_graph("symbols", "ContextProvider")["result"][0]["name"] == "ContextProvider"
    assert "Committed runbook" in context.runbooks(path="docs/runbooks/context.md")["content"]
    assert "dirty bytes" not in context.runbooks(path="docs/runbooks/context.md")["content"]
    assert context.context_status()["source"]["working_tree_clean"] is False


def test_fail_closed_path_and_materialization_checks_and_read_only_surface(bound_context, monkeypatch):
    with pytest.raises(context.ContextError, match="outside"):
        context.source_chunk("docs/runbooks/context.md")
    monkeypatch.setenv("TGW_CONTEXT_PLAN_COMMIT", str(bound_context["evidence_head"]))
    with pytest.raises(context.ContextError, match="does not match"):
        context.context_status()
    tools = set(context.mcp._tool_manager._tools)
    assert tools == {
        "tgw_context_status", "tgw_context_bundle", "tgw_context_plan_graph",
        "tgw_context_plan_source", "tgw_context_runbooks", "tgw_context_code_graph",
    }
    payload = json.loads(context.tgw_context_status())
    assert payload["ok"] is False


def test_governed_review_context_run_fetches_every_bound_resource(monkeypatch):
    contents = {
        name: ("a" * 40 if name == "plan_commit" else f"resource:{name}").encode()
        for name in CARD_RESOURCE_NAMES
    }
    bindings = {
        name: {"ref": f"resource:{name}", "hash": content_hash(contents[name])}
        for name in CARD_RESOURCE_NAMES
    }
    unsigned = {
        "schema": "tgw-execution-card/v1", "role": "independent-review",
        "plan_commit": "a" * 40, "bindings": bindings,
    }
    card = {
        **unsigned,
        "card_hash": "sha256:" + hashlib.sha256(context._canonical(unsigned)).hexdigest(),
    }
    receipt = card_resource_receipt(card)
    observed = {}

    signing_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )).decode()

    class FakeBroker:
        def __init__(self, endpoint):
            observed["endpoint"] = endpoint

        def execute(self, request):
            observed["request"] = request
            attestation = issue_harness_retrieval_attestation(
                {
                    "schema": "tgw-registered-resource-retrieval-attestation/v3",
                    "service_id": "review-resources", "client_id": "review-client",
                    "run_id": "run-bound-context", "card_hash": request["card_hash"],
                    "role": request["role"],
                    "execution_identity": request["execution_identity"],
                    "handoff_hash": request["handoff_hash"],
                    "resource_receipt_hash": request["resource_receipt_hash"],
                    "resources": request["resources"], "attestation_key_id": "test-key",
                }, signing_private_key=signing_key,
            )
            unsigned_bundle = {
                "schema": "tgw-context-review-resource-bundle/v1",
                "client_id": "review-client", "challenge": request["challenge"],
                "skill_contract_hash": request["skill_contract_hash"],
                "retrieval_attestation": attestation,
                "resources": {
                    name: {
                        **bindings[name],
                        "content_sha256": content_hash(contents[name]),
                        "content_base64": base64.b64encode(contents[name]).decode(),
                    }
                    for name in sorted(bindings)
                },
            }
            return {
                **unsigned_bundle,
                "bundle_hash": context._sha(context._canonical(unsigned_bundle)),
            }

    monkeypatch.setenv("TGW_CONTEXT_REVIEW_BROKER_ENDPOINT", "https://broker.invalid")
    monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_ID", "review-resources")
    monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_CLIENT_ID", "review-client")
    monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_REF", "catalog:test")
    monkeypatch.setenv(
        "TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_HASH", "sha256:" + "f" * 64,
    )
    monkeypatch.setenv(
        "TGW_CONTEXT_REVIEW_SKILL_CONTRACT_HASH", "sha256:" + "e" * 64,
    )
    monkeypatch.setenv("TGW_CONTEXT_REVIEW_UID", str(os.geteuid()))
    monkeypatch.setenv("TGW_CONTEXT_REVIEW_GID", str(os.getegid()))
    monkeypatch.setenv("TGW_CONTEXT_ATTESTATION_KEY_ID", "test-key")
    monkeypatch.setenv("TGW_CONTEXT_ATTESTATION_PUBLIC_KEY", public_key)
    now = datetime.now(timezone.utc)
    grant_request = {
        "schema": "tgw-context-review-broker-request/v2",
        "client_id": "review-client", "challenge": "c" * 64,
        "skill_contract_hash": "sha256:" + "e" * 64,
        "card_hash": card["card_hash"], "role": "independent-review",
        "execution_identity": (
            f"governed-review:{'c' * 64}:uid={os.geteuid()}:gid={os.getegid()}"
        ),
        "handoff_hash": "sha256:" + "d" * 64,
        "resource_receipt_hash": receipt["receipt_hash"],
        "resource_service_catalog_ref": "catalog:test",
        "resource_service_catalog_hash": "sha256:" + "f" * 64,
        "resources": bindings,
        "issued_at": (now - timedelta(seconds=1)).isoformat(),
        "not_before": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }
    grant = {
        "schema": "tgw-governed-review-context-grant/v1",
        "request": grant_request,
        "request_hash": context._sha(context._canonical(grant_request)),
    }
    result, visible = context._review_context_run(
        challenge="c" * 64, card_json=json.dumps(card),
        handoff_hash="sha256:" + "d" * 64,
        resource_receipt_hash=receipt["receipt_hash"],
        skill_contract_hash="sha256:" + "e" * 64,
        grant_json=json.dumps(grant),
        broker_factory=FakeBroker,
    )
    assert result == {
        "schema": "tgw-context-review-run/v1", "status": "PASS",
        "context_run_id": "run-bound-context", "challenge": "c" * 64,
        "card_hash": card["card_hash"],
        "resource_receipt_hash": receipt["receipt_hash"],
        "skill_contract_hash": "sha256:" + "e" * 64,
        "runtime_uid": os.geteuid(), "runtime_gid": os.getegid(),
        "retrieval_attestation": result["retrieval_attestation"],
        "resource_bundle_hash": result["resource_bundle_hash"],
    }
    assert {
        name: visible[name]["content"].encode() for name in sorted(visible)
    } == contents
    assert observed["endpoint"] == "https://broker.invalid"
    assert observed["request"]["resources"] == bindings


def test_governed_review_sse_is_loopback_only_and_exposes_only_bound_tool(monkeypatch):
    monkeypatch.setenv("TGW_CONTEXT_MCP_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        context._sse_binding(governed=True)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        **os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "TGW_CONTEXT_MCP_HOST": "127.0.0.1", "TGW_CONTEXT_MCP_PORT": str(port),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m", "tgw.context_mcp_server", "--governed-review-sse",
        ],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        for _attempt in range(50):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("governed review SSE server did not start")
        mcp_config = {
            "mcpServers": {"tgw-context": {
                "type": "sse", "url": f"http://127.0.0.1:{port}/sse",
            }},
        }

        async def inspect_server() -> None:
            async with sse_client(
                mcp_config["mcpServers"]["tgw-context"]["url"],
                timeout=2, sse_read_timeout=5,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert [tool.name for tool in tools.tools] == ["tgw_context_bundle"]
                    result = await session.call_tool(
                        "tgw_context_bundle", {"task": "must be governed"},
                    )
                    assert "arguments must be complete" in result.content[0].text

        anyio.run(inspect_server)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

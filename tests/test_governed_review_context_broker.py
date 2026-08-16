from __future__ import annotations

import hashlib
import json
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw import context_mcp_server
from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    HTTPRegisteredResourceResolver,
    card_resource_receipt,
    content_hash,
    ed25519_public_key,
    validate_harness_retrieval_attestation,
)
from tgw.governed_resource_service import (
    ResourceServiceConfig,
    create_resource_service_server,
)
from tgw.governed_review_context_broker import PrivilegedReviewContextBroker


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_mcp_uses_privileged_broker_and_controller_reads_exact_service_origin(
    tmp_path, monkeypatch,
):
    backend_private_key = Ed25519PrivateKey.generate()
    broker_private_key = Ed25519PrivateKey.generate()
    resources = []
    bindings = {}
    for name in sorted(CARD_RESOURCE_NAMES):
        content = f"protected {name}\n".encode()
        path = tmp_path / name
        path.write_bytes(content)
        path.chmod(0o444)
        binding = {"ref": f"review:{name}", "hash": content_hash(content)}
        bindings[name] = binding
        resources.append({
            "ref": binding["ref"], "path": str(path),
            "content_hash": binding["hash"],
        })
    config = ResourceServiceConfig.parse({
        "schema": "tgw-governed-resource-service-config/v5",
        "service_id": "review-source-service",
        "clients": [{
            "id": "review-context-broker", "credential_env": "BROKER_TOKEN",
            "execution_identity": "review-context-broker-service",
            "role": "independent-review",
        }],
        "attestation_key_id": "review-source-key-1",
        "attestation_private_key_env": "SOURCE_SIGNING_KEY",
        "harness_run_ttl_seconds": 60,
        "completed_run_ttl_seconds": 60,
        "max_open_runs_per_client": 4,
        "max_completed_runs_per_client": 4,
        "resources": resources,
    })
    server = create_resource_service_server(
        config, {"review-context-broker": "backend-secret"},
        signing_private_key=backend_private_key,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        descriptor = {
            "schema": "tgw-registered-resource-service/v2",
            "id": "review-source-service", "client_id": "review-context-broker",
            "endpoint": f"http://127.0.0.1:{server.server_port}",
            "credential_env": "BROKER_TOKEN", "timeout_seconds": 5,
        }
        monkeypatch.setenv("BROKER_TOKEN", "backend-secret")
        backend = HTTPRegisteredResourceResolver.from_descriptor(descriptor)
        broker = PrivilegedReviewContextBroker(
            backend=backend,
            backend_execution_identity="review-context-broker-service",
            backend_attestation_key_id="review-source-key-1",
            backend_attestation_public_key=ed25519_public_key(backend_private_key),
            service_id="review-context-service", client_id="review-provider",
            attestation_key_id="review-context-key-1",
            signing_private_key=broker_private_key,
        )
        card_unsigned = {
            "role": "independent-review",
            "plan_commit": "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99",
            "bindings": bindings,
        }
        card = {**card_unsigned, "card_hash": _hash(card_unsigned)}
        resource_receipt = card_resource_receipt(card)
        challenge = "c" * 64
        handoff_hash = "sha256:" + "1" * 64
        skill_hash = "sha256:" + "2" * 64
        monkeypatch.setenv("TGW_CONTEXT_REVIEW_BROKER_ENDPOINT", "https://broker.invalid")
        monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_ID", "review-context-service")
        monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_CLIENT_ID", "review-provider")
        monkeypatch.setenv("TGW_CONTEXT_ATTESTATION_KEY_ID", "review-context-key-1")
        monkeypatch.setenv(
            "TGW_CONTEXT_ATTESTATION_PUBLIC_KEY",
            ed25519_public_key(broker_private_key),
        )
        monkeypatch.setenv("TGW_CONTEXT_REVIEW_SKILL_CONTRACT_HASH", skill_hash)
        monkeypatch.setenv("TGW_CONTEXT_REVIEW_UID", str(context_mcp_server.os.geteuid()))
        monkeypatch.setenv("TGW_CONTEXT_REVIEW_GID", str(context_mcp_server.os.getegid()))
        receipt = context_mcp_server._review_context_run(
            challenge=challenge, card_json=json.dumps(card),
            handoff_hash=handoff_hash,
            resource_receipt_hash=resource_receipt["receipt_hash"],
            skill_contract_hash=skill_hash,
            broker_factory=lambda _endpoint: broker,
        )
        retained = broker.read_attestation(receipt["context_run_id"])
        assert retained == receipt["retrieval_attestation"]
        assert validate_harness_retrieval_attestation(
            retained,
            attestation_key_id="review-context-key-1",
            attestation_public_key=ed25519_public_key(broker_private_key),
        )["execution_identity"] == (
            f"governed-review:{challenge}:uid={context_mcp_server.os.geteuid()}:"
            f"gid={context_mcp_server.os.getegid()}"
        )
        assert "backend-secret" not in json.dumps(receipt)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

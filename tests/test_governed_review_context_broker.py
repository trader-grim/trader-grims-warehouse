from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw import context_mcp_server
from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    RESOURCE_SERVICE_CAPABILITIES,
    HTTPRegisteredResourceResolver,
    card_resource_receipt,
    content_hash,
    ed25519_public_key,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
    validate_harness_retrieval_attestation,
)
from tgw.governed_resource_service import (
    ResourceServiceConfig,
    create_resource_service_server,
)
from tgw.governed_review_context_broker import (
    PrivilegedReviewContextBroker,
    ReviewContextBrokerError,
    _attach_config_guard,
    _load_protected_config,
    broker_server_from_config,
    create_review_context_broker_server,
)


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _private_key(private_key):
    return base64.b64encode(private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )).decode()


def test_broker_config_is_root_protected_held_and_xy_checked(tmp_path):
    if subprocess.run(
        ["sudo", "-n", "true"], capture_output=True, check=False,
    ).returncode:
        pytest.skip("passwordless sudo is required for protected config regression")
    path = Path("/tmp") / f"tgw-review-broker-{os.getpid()}-{tmp_path.name}.json"
    displaced = path.with_suffix(".old")
    replacement = path.with_suffix(".new")
    try:
        path.write_text("{}")
        subprocess.run(["sudo", "-n", "chown", "0:0", str(path)], check=True)
        subprocess.run(["sudo", "-n", "chmod", "0664", str(path)], check=True)
        with pytest.raises(ReviewContextBrokerError, match="not protected"):
            _load_protected_config(path)
        subprocess.run(["sudo", "-n", "chmod", "0444", str(path)], check=True)
        _value, descriptor, identity = _load_protected_config(path)
        server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        _attach_config_guard(
            server, path=path, descriptor=descriptor, identity=identity,
        )
        replacement.write_text('{"replaced":true}')
        subprocess.run(
            ["sudo", "-n", "mv", str(path), str(displaced)], check=True,
        )
        subprocess.run(
            ["sudo", "-n", "mv", str(replacement), str(path)], check=True,
        )
        subprocess.run(["sudo", "-n", "chown", "0:0", str(path)], check=True)
        subprocess.run(["sudo", "-n", "chmod", "0444", str(path)], check=True)
        with pytest.raises(ReviewContextBrokerError, match="changed while held"):
            server.server_close()
    finally:
        subprocess.run(
            ["sudo", "-n", "rm", "-f", str(path), str(displaced), str(replacement)],
            check=False,
        )


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
        backend_catalog = {
            "schema": "tgw-registered-resource-service-catalog/v3",
            "catalog_ref": "catalog:review-backend",
            "plan_commit": "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99",
            "services": [{
                "id": descriptor["id"], "client_id": descriptor["client_id"],
                "descriptor_hash": resource_service_descriptor_hash(descriptor),
                "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
                "attestation_key_id": "review-source-key-1",
                "attestation_public_key": ed25519_public_key(backend_private_key),
            }],
        }
        backend_catalog_hash = resource_service_catalog_hash(backend_catalog)
        monkeypatch.setenv("BROKER_TOKEN", "backend-secret")
        backend = HTTPRegisteredResourceResolver.from_descriptor(descriptor)
        broker_clock = [0.0]
        grant_now = datetime.now(timezone.utc)
        broker = PrivilegedReviewContextBroker(
            backend=backend,
            backend_execution_identity="review-context-broker-service",
            backend_attestation_key_id="review-source-key-1",
            backend_attestation_public_key=ed25519_public_key(backend_private_key),
            backend_catalog_ref=backend_catalog["catalog_ref"],
            backend_catalog_hash=backend_catalog_hash,
            service_id="review-context-service", client_id="review-provider",
            attestation_key_id="review-context-key-1",
            signing_private_key=broker_private_key,
            retained_ttl_seconds=1, clock=lambda: broker_clock[0],
            grant_clock=lambda: grant_now,
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
        broker_request = {
            "schema": "tgw-context-review-broker-request/v2",
            "client_id": "review-provider", "challenge": challenge,
            "skill_contract_hash": skill_hash,
            "card_hash": card["card_hash"], "role": "independent-review",
            "execution_identity": (
                f"governed-review:{challenge}:uid={context_mcp_server.os.geteuid()}:"
                f"gid={context_mcp_server.os.getegid()}"
            ),
            "handoff_hash": handoff_hash,
            "resource_receipt_hash": resource_receipt["receipt_hash"],
            "resource_service_catalog_ref": backend_catalog["catalog_ref"],
            "resource_service_catalog_hash": backend_catalog_hash,
            "resources": bindings,
            "issued_at": (grant_now - timedelta(seconds=1)).isoformat(),
            "not_before": (grant_now - timedelta(seconds=1)).isoformat(),
            "expires_at": (grant_now + timedelta(minutes=10)).isoformat(),
        }
        stale_grant = {
            **broker_request,
            "issued_at": (grant_now - timedelta(minutes=20)).isoformat(),
            "not_before": (grant_now - timedelta(minutes=20)).isoformat(),
            "expires_at": (grant_now - timedelta(minutes=10)).isoformat(),
        }
        with pytest.raises(ReviewContextBrokerError, match="not active"):
            broker.validate_grant(stale_grant)
        future_grant = {
            **broker_request,
            "issued_at": (grant_now + timedelta(minutes=1)).isoformat(),
            "not_before": (grant_now + timedelta(minutes=1)).isoformat(),
            "expires_at": (grant_now + timedelta(minutes=2)).isoformat(),
        }
        with pytest.raises(ReviewContextBrokerError, match="not active"):
            broker.validate_grant(future_grant)
        overlong_grant = {
            **broker_request,
            "expires_at": (grant_now + timedelta(minutes=20)).isoformat(),
        }
        with pytest.raises(ReviewContextBrokerError, match="window"):
            broker.validate_grant(overlong_grant)
        monkeypatch.setenv("BROKER_SIGNING", _private_key(broker_private_key))
        monkeypatch.setenv("BROKER_REQUEST", "config-request-secret")
        monkeypatch.setenv("BROKER_READBACK", "config-readback-secret")
        broker_config = {
            "schema": "tgw-context-review-broker-config/v2",
            "backend_descriptor": descriptor,
            "backend_execution_identity": "review-context-broker-service",
            "backend_attestation_key_id": "review-source-key-1",
            "backend_attestation_public_key": ed25519_public_key(backend_private_key),
            "backend_resource_service_catalog": backend_catalog,
            "backend_resource_service_catalog_hash": backend_catalog_hash,
            "service_id": "review-context-service", "client_id": "review-provider",
            "attestation_key_id": "review-context-key-1",
            "attestation_public_key": ed25519_public_key(broker_private_key),
            "signing_private_key_env": "BROKER_SIGNING",
            "request_grants": [{
                "client_id": "review-provider",
                "credential_env": "BROKER_REQUEST", "request": broker_request,
            }],
            "readback_clients": [{
                "client_id": "review-provider", "credential_env": "BROKER_READBACK",
            }],
        }
        wrong_backend_key = json.loads(json.dumps(broker_config))
        wrong_backend_key["backend_attestation_public_key"] = ed25519_public_key(
            Ed25519PrivateKey.generate()
        )
        with pytest.MonkeyPatch.context() as startup_guard:
            startup_guard.setattr(
                HTTPRegisteredResourceResolver,
                "from_descriptor",
                classmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("backend work preceded catalog key validation")
                )),
            )
            with pytest.raises(ReviewContextBrokerError, match="backend is invalid"):
                broker_server_from_config(
                    wrong_backend_key, environment=os.environ,
                    host="127.0.0.1", port=0,
                )
        configured = broker_server_from_config(
            broker_config, environment=os.environ, host="127.0.0.1", port=0,
        )
        configured_thread = threading.Thread(
            target=configured.serve_forever, daemon=True,
        )
        configured_thread.start()
        configured_endpoint = f"http://127.0.0.1:{configured.server_port}"
        configured_request = Request(
            configured_endpoint + "/v1/review-context",
            data=json.dumps(broker_request).encode(), method="POST",
            headers={
                "Authorization": "Bearer config-request-secret",
                "Content-Type": "application/json",
            },
        )
        configured_bundle = json.loads(urlopen(configured_request).read())
        assert configured_bundle["challenge"] == challenge
        with pytest.raises(HTTPError) as configured_replay:
            urlopen(configured_request)
        assert configured_replay.value.code == 404
        configured.shutdown()
        configured.server_close()
        configured_thread.join(timeout=5)
        wrong_key = json.loads(json.dumps(broker_config))
        wrong_key["attestation_public_key"] = ed25519_public_key(
            Ed25519PrivateKey.generate()
        )
        with pytest.raises(ReviewContextBrokerError, match="identity differs"):
            broker_server_from_config(
                wrong_key, environment=os.environ, host="127.0.0.1", port=0,
            )
        malformed_key = {**broker_config, "attestation_public_key": "not-base64"}
        with pytest.raises(ReviewContextBrokerError, match="signing key is invalid"):
            broker_server_from_config(
                malformed_key, environment=os.environ, host="127.0.0.1", port=0,
            )
        malformed_backend = {
            **broker_config, "backend_attestation_public_key": "not-base64",
        }
        with pytest.raises(ReviewContextBrokerError, match="signing key is invalid"):
            broker_server_from_config(
                malformed_backend, environment=os.environ, host="127.0.0.1", port=0,
            )
        wrong_catalog_hash = {
            **broker_config,
            "backend_resource_service_catalog_hash": "sha256:" + "0" * 64,
        }
        with pytest.raises(ReviewContextBrokerError, match="backend is invalid"):
            broker_server_from_config(
                wrong_catalog_hash, environment=os.environ, host="127.0.0.1", port=0,
            )
        mutated_catalog = json.loads(json.dumps(broker_config))
        mutated_catalog["backend_resource_service_catalog"]["services"][0][
            "descriptor_hash"
        ] = "sha256:" + "1" * 64
        mutated_catalog["backend_resource_service_catalog_hash"] = (
            resource_service_catalog_hash(
                mutated_catalog["backend_resource_service_catalog"]
            )
        )
        with pytest.raises(ReviewContextBrokerError, match="backend is invalid"):
            broker_server_from_config(
                mutated_catalog, environment=os.environ, host="127.0.0.1", port=0,
            )
        substituted_catalog = json.loads(json.dumps(broker_config))
        substituted_public_key = ed25519_public_key(Ed25519PrivateKey.generate())
        substituted_catalog["backend_attestation_public_key"] = substituted_public_key
        substituted_catalog["backend_resource_service_catalog"]["services"][0][
            "attestation_public_key"
        ] = substituted_public_key
        substituted_catalog["backend_resource_service_catalog_hash"] = (
            resource_service_catalog_hash(
                substituted_catalog["backend_resource_service_catalog"]
            )
        )
        with pytest.MonkeyPatch.context() as startup_guard:
            startup_guard.setattr(
                HTTPRegisteredResourceResolver,
                "from_descriptor",
                classmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("backend work preceded card catalog validation")
                )),
            )
            with pytest.raises(
                ReviewContextBrokerError, match="request grant is invalid",
            ):
                broker_server_from_config(
                    substituted_catalog, environment=os.environ,
                    host="127.0.0.1", port=0,
                )
        with pytest.raises(ReviewContextBrokerError, match="credentials are invalid"):
            create_review_context_broker_server(
                broker, request_grants={"shared": broker_request},
                readback_credentials={
                    "review-provider": "shared", "other-client": "shared",
                },
            )
        broker_server = create_review_context_broker_server(
            broker, request_grants={"one-use-request": broker_request},
            readback_credentials={"review-provider": "controller-readback"},
        )
        broker_thread = threading.Thread(
            target=broker_server.serve_forever, daemon=True,
        )
        broker_thread.start()
        broker_endpoint = f"http://127.0.0.1:{broker_server.server_port}"

        def post(value, credential=None):
            headers = {"Content-Type": "application/json"}
            if credential is not None:
                headers["Authorization"] = "Bearer " + credential
            return json.loads(urlopen(Request(
                broker_endpoint + "/v1/review-context",
                data=json.dumps(value).encode(), method="POST", headers=headers,
            )).read())

        with pytest.raises(HTTPError) as direct:
            post(broker_request)
        assert direct.value.code == 404
        wrong_client = {**broker_request, "client_id": "wrong-client"}
        with pytest.raises(HTTPError) as wrong:
            post(wrong_client, "one-use-request")
        assert wrong.value.code == 404
        monkeypatch.setenv(
            "TGW_CONTEXT_BROKER_REQUEST_CREDENTIAL", "one-use-request",
        )
        service_bundle = context_mcp_server.HTTPReviewContextBrokerClient(
            broker_endpoint,
        ).execute(broker_request)
        with pytest.raises(HTTPError) as replay:
            post(broker_request, "one-use-request")
        assert replay.value.code == 404
        readback_url = (
            broker_endpoint
            + f"/v1/review-context-challenges/{challenge}/bundle"
        )
        with pytest.raises(HTTPError) as wrong_readback:
            urlopen(Request(
                readback_url,
                headers={"Authorization": "Bearer one-use-request"},
            ))
        assert wrong_readback.value.code == 404
        with pytest.raises(HTTPError) as cross_client:
            urlopen(Request(
                readback_url,
                headers={"Authorization": "Bearer wrong-readback"},
            ))
        assert cross_client.value.code == 404
        retained_service_bundle = json.loads(urlopen(Request(
            readback_url,
            headers={"Authorization": "Bearer controller-readback"},
        )).read())
        assert retained_service_bundle == service_bundle
        with pytest.raises(HTTPError) as second_readback:
            urlopen(Request(
                readback_url,
                headers={"Authorization": "Bearer controller-readback"},
            ))
        assert second_readback.value.code == 404

        monkeypatch.setenv("TGW_CONTEXT_REVIEW_BROKER_ENDPOINT", broker_endpoint)
        monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_ID", "review-context-service")
        monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_CLIENT_ID", "review-provider")
        monkeypatch.setenv(
            "TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_REF", backend_catalog["catalog_ref"],
        )
        monkeypatch.setenv(
            "TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_HASH", backend_catalog_hash,
        )
        monkeypatch.setenv("TGW_CONTEXT_ATTESTATION_KEY_ID", "review-context-key-1")
        monkeypatch.setenv(
            "TGW_CONTEXT_ATTESTATION_PUBLIC_KEY",
            ed25519_public_key(broker_private_key),
        )
        monkeypatch.setenv("TGW_CONTEXT_REVIEW_SKILL_CONTRACT_HASH", skill_hash)
        monkeypatch.setenv("TGW_CONTEXT_REVIEW_UID", str(context_mcp_server.os.geteuid()))
        monkeypatch.setenv("TGW_CONTEXT_REVIEW_GID", str(context_mcp_server.os.getegid()))
        grant = {
            "schema": "tgw-governed-review-context-grant/v1",
            "request": broker_request,
            "request_hash": _hash(broker_request),
        }
        class IssuedBundle:
            def __init__(self, _endpoint):
                pass

            def execute(self, request):
                assert request == broker_request
                return service_bundle

        monkeypatch.setattr(
            context_mcp_server, "HTTPReviewContextBrokerClient", IssuedBundle,
        )
        monkeypatch.setattr(
            context_mcp_server, "context_bundle",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("governed mode must not read local Plan/source")
            ),
        )
        monkeypatch.setattr(
            context_mcp_server, "_per_call_guard", lambda: {},
        )
        tool_result = json.loads(context_mcp_server.governed_review_context_bundle(
            "review exact candidate", receiver="claude", challenge=challenge,
            card_json=json.dumps(card), handoff_hash=handoff_hash,
            resource_receipt_hash=resource_receipt["receipt_hash"],
            skill_contract_hash=skill_hash,
            grant_json=json.dumps(grant),
        ))
        assert tool_result["registered_resources"]["plan_graph"]["content"] == (
            "protected plan_graph\n"
        )
        assert tool_result["registered_resource_retrieval"]["resource_bundle_hash"] == (
            service_bundle["bundle_hash"]
        )
        omitted = json.loads(context_mcp_server.governed_review_context_bundle(
            "review exact candidate", receiver="claude",
        ))
        assert omitted["ok"] is False
        assert "complete" in omitted["error"]

        receipt, visible = context_mcp_server._review_context_run(
            challenge=challenge, card_json=json.dumps(card),
            handoff_hash=handoff_hash,
            resource_receipt_hash=resource_receipt["receipt_hash"],
            skill_contract_hash=skill_hash,
            grant_json=json.dumps(grant),
            broker_factory=IssuedBundle,
        )
        retained_bundle = retained_service_bundle
        retained = retained_bundle["retrieval_attestation"]
        assert retained == receipt["retrieval_attestation"]
        assert retained_bundle["bundle_hash"] == receipt["resource_bundle_hash"]
        assert {
            name: visible[name]["content"]
            for name in sorted(CARD_RESOURCE_NAMES)
        } == {
            name: f"protected {name}\n" for name in sorted(CARD_RESOURCE_NAMES)
        }
        assert validate_harness_retrieval_attestation(
            retained,
            attestation_key_id="review-context-key-1",
            attestation_public_key=ed25519_public_key(broker_private_key),
        )["execution_identity"] == (
            f"governed-review:{challenge}:uid={context_mcp_server.os.geteuid()}:"
            f"gid={context_mcp_server.os.getegid()}"
        )
        assert "backend-secret" not in json.dumps(receipt)
        abandoned = broker.execute(broker_request)
        broker_clock[0] = 2.0
        with pytest.raises(ReviewContextBrokerError, match="run is unavailable"):
            broker.read_bundle(
                abandoned["retrieval_attestation"]["run_id"],
                client_id="review-provider",
            )
        substituted = json.loads(json.dumps(service_bundle))
        substituted["resources"]["plan_graph"]["content_base64"] = (
            substituted["resources"]["codegraph_snapshot"]["content_base64"]
        )
        substituted["resources"]["plan_graph"]["content_sha256"] = (
            substituted["resources"]["codegraph_snapshot"]["content_sha256"]
        )
        substituted_unsigned = {
            name: value for name, value in substituted.items()
            if name != "bundle_hash"
        }
        substituted["bundle_hash"] = _hash(substituted_unsigned)

        class SubstitutedBundle:
            def __init__(self, _endpoint):
                pass

            def execute(self, _request):
                return substituted

        with pytest.raises(context_mcp_server.ContextError, match="registered context retrieval failed"):
            context_mcp_server._review_context_run(
                challenge=challenge, card_json=json.dumps(card),
                handoff_hash=handoff_hash,
                resource_receipt_hash=resource_receipt["receipt_hash"],
                skill_contract_hash=skill_hash,
                grant_json=json.dumps(grant),
                broker_factory=SubstitutedBundle,
            )
        broker_server.shutdown()
        broker_thread.join()
        broker_server.server_close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

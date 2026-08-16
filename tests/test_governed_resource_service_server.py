import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.execution_resources import (
    RESOURCE_SERVICE_CAPABILITIES,
    HTTPRegisteredResourceResolver,
    ResourceVerificationError,
    content_hash,
    ed25519_public_key,
    load_resource_service_catalog,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
    verify_card_resources,
    verify_resource_service_registration,
)
from tgw.governed_resource_service import (
    ResourceServiceConfigurationError,
    create_resource_service_server,
    load_resource_service_config,
    main,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
REFS = {
    "plan_input": "plan:input",
    "plan_commit": "plan:commit",
    "plan_graph": "plan:graph",
    "codegraph_snapshot": "codegraph:snapshot",
    "source_tree": "git:source",
    "execution_environment": "environment:manifest",
    "authority_conditions": "authority:conditions",
    "receipt_sink": "receipt:sink",
}


def _canonical_hash(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _contents():
    return {
        "plan_input": b"specification-formatted Plan input",
        "plan_commit": PLAN_COMMIT.encode(),
        "plan_graph": b"resolved Plan Graph snapshot",
        "codegraph_snapshot": b"CodeGraph snapshot",
        "source_tree": b"source tree content",
        "execution_environment": b"execution environment manifest",
        "authority_conditions": b"authority and solved conditions",
        "receipt_sink": b"registered receipt sink descriptor",
    }


def _config_path(tmp_path, contents=None, *, signing_private_key=None, ttl=60):
    contents = _contents() if contents is None else contents
    signing_private_key = signing_private_key or Ed25519PrivateKey.generate()
    exports = tmp_path / "exports"
    exports.mkdir()
    resources = []
    for name, content in contents.items():
        path = exports / name
        path.write_bytes(content)
        resources.append({"ref": REFS[name], "path": str(path), "content_hash": content_hash(content)})
    config_path = tmp_path / "resource-service.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": "tgw-governed-resource-service-config/v2",
                "service_id": "portable-resource-service",
                "credential_env": "TGW_PORTABLE_RESOURCE_TOKEN",
                "attestation_key_id": "portable-test-key-1",
                "attestation_private_key_env": "TGW_PORTABLE_RESOURCE_SIGNING_KEY",
                "harness_run_ttl_seconds": ttl,
                "resources": resources,
            }
        )
    )
    return config_path, contents, signing_private_key


def _card(contents):
    bindings = {
        name: {"ref": REFS[name], "hash": content_hash(contents[name])}
        for name in sorted(REFS)
    }
    unsigned = {"plan_commit": PLAN_COMMIT, "bindings": bindings}
    return {**unsigned, "card_hash": _canonical_hash(unsigned)}


def _descriptor(endpoint):
    return {
        "schema": "tgw-registered-resource-service/v1",
        "id": "portable-resource-service",
        "endpoint": endpoint,
        "credential_env": "TGW_PORTABLE_RESOURCE_TOKEN",
        "timeout_seconds": 5,
    }


def _catalog(descriptor, signing_private_key):
    return {
        "schema": "tgw-registered-resource-service-catalog/v2",
        "catalog_ref": "catalog:portable-resource-service-test@1",
        "plan_commit": PLAN_COMMIT,
        "services": [
            {
                "id": descriptor["id"],
                "descriptor_hash": resource_service_descriptor_hash(descriptor),
                "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
                "attestation_key_id": "portable-test-key-1",
                "attestation_public_key": ed25519_public_key(signing_private_key),
            }
        ],
    }


@contextmanager
def _server(config, credential, signing_private_key, **kwargs):
    server = create_resource_service_server(
        config, credential, signing_private_key=signing_private_key, **kwargs,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_portable_resource_service_implements_registered_protocol(tmp_path, monkeypatch):
    config_path, contents, signing_private_key = _config_path(tmp_path)
    config = load_resource_service_config(config_path)
    monkeypatch.setenv("TGW_PORTABLE_RESOURCE_TOKEN", "test-token")
    with _server(config, "test-token", signing_private_key) as endpoint:
        descriptor = _descriptor(endpoint)
        resolver = HTTPRegisteredResourceResolver.from_descriptor(descriptor)
        catalog = _catalog(descriptor, signing_private_key)
        verify_resource_service_registration(catalog, descriptor, resolver=resolver)
        card = _card(contents)
        receipt = verify_card_resources(card, resolver)
        run = resolver.begin_harness_run(
            card_hash=card["card_hash"],
            role="implementation",
            execution_identity="portable-service-test",
            handoff_hash="sha256:" + "1" * 64,
            resource_receipt_hash=receipt["receipt_hash"],
            resources=card["bindings"],
        )
        assert verify_card_resources(card, resolver.for_harness_run(run)) == receipt
        attestation = resolver.complete_harness_run(run)
        assert resolver.verify_harness_retrieval_attestation(
            attestation,
            card_hash=card["card_hash"],
            role="implementation",
            execution_identity="portable-service-test",
            handoff_hash="sha256:" + "1" * 64,
            resource_receipt_hash=receipt["receipt_hash"],
            resources=card["bindings"],
            attestation_key_id=catalog["services"][0]["attestation_key_id"],
            attestation_public_key=catalog["services"][0]["attestation_public_key"],
        ) == attestation
        assert attestation["resources"] == card["bindings"]
        assert resource_service_catalog_hash(catalog).startswith("sha256:")


def test_service_configuration_rejects_changed_or_unhashed_exports(tmp_path):
    config_path, _, _ = _config_path(tmp_path)
    config = json.loads(config_path.read_text())
    config["resources"][0]["content_hash"] = "sha256:" + "0" * 64
    config_path.write_text(json.dumps(config))

    with pytest.raises(ResourceServiceConfigurationError, match="content hash mismatch"):
        load_resource_service_config(config_path)


def test_uninstalled_template_is_not_a_qualified_catalog_or_descriptor():
    template = ROOT / "agent-services/catalogs/governed-resource-service-v1.template.json"
    with pytest.raises(ResourceVerificationError, match="registered resource service catalog is invalid"):
        load_resource_service_catalog(template)


def test_cli_fails_closed_when_runtime_credential_is_not_provisioned(tmp_path, monkeypatch, capsys):
    config_path, _, _ = _config_path(tmp_path)
    monkeypatch.delenv("TGW_PORTABLE_RESOURCE_TOKEN", raising=False)

    assert main(["--config", str(config_path), "--port", "18444"]) == 2
    assert "credential is unavailable" in capsys.readouterr().err


def test_cli_fails_closed_when_runtime_signing_key_is_not_provisioned(tmp_path, monkeypatch, capsys):
    config_path, _, _ = _config_path(tmp_path)
    monkeypatch.setenv("TGW_PORTABLE_RESOURCE_TOKEN", "test-token")
    monkeypatch.delenv("TGW_PORTABLE_RESOURCE_SIGNING_KEY", raising=False)

    assert main(["--config", str(config_path), "--port", "18444"]) == 2
    assert "private signing key is unavailable" in capsys.readouterr().err


def test_cli_fails_closed_when_runtime_signing_key_is_malformed(tmp_path, monkeypatch, capsys):
    config_path, _, _ = _config_path(tmp_path)
    monkeypatch.setenv("TGW_PORTABLE_RESOURCE_TOKEN", "test-token")
    monkeypatch.setenv("TGW_PORTABLE_RESOURCE_SIGNING_KEY", "not-a-raw-ed25519-key")

    assert main(["--config", str(config_path), "--port", "18444"]) == 2
    assert "private signing key is invalid" in capsys.readouterr().err


def test_signed_attestation_rejects_a_wrong_catalog_key_or_key_identity(tmp_path, monkeypatch):
    config_path, contents, signing_private_key = _config_path(tmp_path)
    config = load_resource_service_config(config_path)
    monkeypatch.setenv("TGW_PORTABLE_RESOURCE_TOKEN", "test-token")
    with _server(config, "test-token", signing_private_key) as endpoint:
        descriptor = _descriptor(endpoint)
        resolver = HTTPRegisteredResourceResolver.from_descriptor(descriptor)
        card = _card(contents)
        receipt = verify_card_resources(card, resolver)
        run = resolver.begin_harness_run(
            card_hash=card["card_hash"], role="implementation", execution_identity="portable-service-test",
            handoff_hash="sha256:" + "1" * 64, resource_receipt_hash=receipt["receipt_hash"],
            resources=card["bindings"],
        )
        verify_card_resources(card, resolver.for_harness_run(run))
        attestation = resolver.complete_harness_run(run)
        wrong_key_catalog = _catalog(descriptor, Ed25519PrivateKey.generate())
        wrong_entry = wrong_key_catalog["services"][0]
        expected = {
            "card_hash": card["card_hash"], "role": "implementation",
            "execution_identity": "portable-service-test", "handoff_hash": "sha256:" + "1" * 64,
            "resource_receipt_hash": receipt["receipt_hash"], "resources": card["bindings"],
        }
        with pytest.raises(ResourceVerificationError, match="attestation is invalid"):
            resolver.verify_harness_retrieval_attestation(
                {key: value for key, value in attestation.items() if key != "signature"},
                **expected,
                attestation_key_id="portable-test-key-1",
                attestation_public_key=ed25519_public_key(signing_private_key),
            )
        with pytest.raises(ResourceVerificationError, match="signature is invalid"):
            resolver.verify_harness_retrieval_attestation(
                attestation,
                **expected, **{
                    "attestation_key_id": wrong_entry["attestation_key_id"],
                    "attestation_public_key": wrong_entry["attestation_public_key"],
                },
            )
        with pytest.raises(ResourceVerificationError, match="key identity mismatch"):
            resolver.verify_harness_retrieval_attestation(
                attestation,
                **expected,
                attestation_key_id="other-key",
                attestation_public_key=ed25519_public_key(signing_private_key),
            )


def test_unfinished_harness_runs_expire_and_are_reclaimed(tmp_path):
    config_path, contents, signing_private_key = _config_path(tmp_path, ttl=1)
    config = load_resource_service_config(config_path)
    now = [100.0]
    with _server(config, "test-token", signing_private_key, clock=lambda: now[0]) as endpoint:
        descriptor = _descriptor(endpoint)
        resolver = HTTPRegisteredResourceResolver.from_descriptor(
            descriptor, environment={"TGW_PORTABLE_RESOURCE_TOKEN": "test-token"},
        )
        card = _card(contents)
        receipt = verify_card_resources(card, resolver)
        run = resolver.begin_harness_run(
            card_hash=card["card_hash"], role="implementation", execution_identity="portable-service-test",
            handoff_hash="sha256:" + "1" * 64, resource_receipt_hash=receipt["receipt_hash"],
            resources=card["bindings"],
        )
        now[0] += 1
        with pytest.raises(ResourceVerificationError, match="attestation request failed"):
            resolver.complete_harness_run(run)

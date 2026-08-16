import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from tgw.execution_resources import (
    RESOURCE_SERVICE_CAPABILITIES,
    HTTPRegisteredResourceResolver,
    ResourceVerificationError,
    resource_service_descriptor_hash,
    verify_card_resources,
    verify_resource_service_registration,
)
from tgw.governed_coding import dispatch_role
from tgw.harness_registry import load_registry, observe_health

ROOT = Path(__file__).resolve().parents[1]
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def canonical_hash(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def resources():
    return {
        "plan:input": b"specification-formatted Plan input",
        "plan:commit": PLAN_COMMIT.encode(),
        "plan:graph": b"resolved Plan Graph snapshot",
        "codegraph:snapshot": b"CodeGraph snapshot",
        "git:source": b"source tree content",
        "environment:manifest": b"execution environment manifest",
        "authority:conditions": b"authority and solved conditions",
        "receipt:sink": b"registered receipt sink descriptor",
    }


def card_template(content, service=None):
    def binding(ref):
        return {"ref": ref, "hash": "sha256:" + hashlib.sha256(content[ref]).hexdigest()}

    value = {
        "card_id": "resource-service-card",
        "solution_id": "sha256:solution",
        "plan_commit": PLAN_COMMIT,
        "bindings": {
            "plan_input": binding("plan:input"),
            "plan_commit": binding("plan:commit"),
            "plan_graph": binding("plan:graph"),
            "codegraph_snapshot": binding("codegraph:snapshot"),
            "source_tree": binding("git:source"),
            "execution_environment": binding("environment:manifest"),
            "authority_conditions": binding("authority:conditions"),
            "receipt_sink": binding("receipt:sink"),
        },
        "authority": ["local source and tests only"],
        "exclusions": ["no deployment"],
        "acceptance": ["role receipt passes"],
        "lease": {
            "id": "lease:l",
            "expires_at": "2027-08-11T23:00:00Z",
            "stop_policy": "hold",
        },
    }
    if service is not None:
        value["resource_service"] = {
            "id": service["id"],
            "descriptor_hash": resource_service_descriptor_hash(service),
        }
    return value


def card(content, service=None):
    unsigned = {
        **card_template(content, service),
        "schema": "tgw-execution-card/v1",
        "role": "implementation",
        "selected_provider": "resource-service-runner",
        "receiver_profile": {"id": "codex", "version": 1},
    }
    return {**unsigned, "card_hash": canonical_hash(unsigned)}


@contextmanager
def resource_service(content, *, token="test-token"):
    runs = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/v1/health" and self.headers.get("Authorization") == f"Bearer {token}":
                body = json.dumps(
                    {
                        "schema": "tgw-registered-resource-health/v1",
                        "service_id": "test-resource-service",
                        "status": "healthy",
                    },
                    sort_keys=True,
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            attestation_prefix = "/v1/harness-runs/"
            if parsed.path.startswith(attestation_prefix) and parsed.path.endswith("/attestation"):
                if self.headers.get("Authorization") != f"Bearer {token}":
                    self.send_error(404)
                    return
                run_id = unquote(parsed.path[len(attestation_prefix):-len("/attestation")]).strip("/")
                run = runs.get(run_id)
                if run is None or "attestation" not in run:
                    self.send_error(404)
                    return
                body = json.dumps(run["attestation"], sort_keys=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            prefix = "/v1/resources/"
            if not parsed.path.startswith(prefix) or self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error(404)
                return
            ref = unquote(parsed.path.removeprefix(prefix))
            if ref not in content:
                self.send_error(404)
                return
            run_id = self.headers.get("X-TGW-Harness-Run")
            if run_id:
                run = runs.get(run_id)
                if run is None or ref not in {binding["ref"] for binding in run["resources"].values()}:
                    self.send_error(403)
                    return
                run["seen"].add(ref)
            body = json.dumps(
                {
                    "schema": "tgw-registered-resource/v1",
                    "service_id": "test-resource-service",
                    "ref": ref,
                    "content_base64": base64.b64encode(content[ref]).decode(),
                },
                sort_keys=True,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                value = json.loads(self.rfile.read(length).decode())
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400)
                return
            if self.path == "/v1/harness-runs":
                required = {
                    "schema", "service_id", "card_hash", "role", "execution_identity",
                    "handoff_hash", "resource_receipt_hash", "resources",
                }
                if set(value) != required or value["schema"] != "tgw-registered-resource-harness-run/v1" or value["service_id"] != "test-resource-service":
                    self.send_error(400)
                    return
                run_id = f"run-{len(runs) + 1}"
                runs[run_id] = {**value, "seen": set()}
                body = json.dumps({**value, "run_id": run_id}, sort_keys=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            prefix = "/v1/harness-runs/"
            suffix = "/complete"
            if not self.path.startswith(prefix) or not self.path.endswith(suffix):
                self.send_error(404)
                return
            run_id = unquote(self.path[len(prefix):-len(suffix)]).strip("/")
            run = runs.get(run_id)
            if run is None or value != {"schema": "tgw-registered-resource-harness-run/v1", "run_id": run_id}:
                self.send_error(400)
                return
            if run["seen"] != {binding["ref"] for binding in run["resources"].values()}:
                self.send_error(409)
                return
            unsigned = {
                "schema": "tgw-registered-resource-retrieval-attestation/v1",
                "service_id": "test-resource-service", "run_id": run_id,
                "card_hash": run["card_hash"], "role": run["role"],
                "execution_identity": run["execution_identity"], "handoff_hash": run["handoff_hash"],
                "resource_receipt_hash": run["resource_receipt_hash"], "resources": run["resources"],
            }
            attestation = {**unsigned, "attestation_hash": canonical_hash(unsigned)}
            run["attestation"] = attestation
            body = json.dumps(attestation, sort_keys=True).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def descriptor(endpoint):
    return {
        "schema": "tgw-registered-resource-service/v1",
        "id": "test-resource-service",
        "endpoint": endpoint,
        "credential_env": "TGW_TEST_RESOURCE_TOKEN",
        "timeout_seconds": 5,
    }


def catalog(service):
    return {
        "schema": "tgw-registered-resource-service-catalog/v1",
        "services": [
            {
                "id": service["id"],
                "descriptor_hash": resource_service_descriptor_hash(service),
                "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
            }
        ],
    }


def test_http_registered_resource_resolver_fetches_and_verifies_every_binding(monkeypatch):
    content = resources()
    monkeypatch.setenv("TGW_TEST_RESOURCE_TOKEN", "test-token")
    with resource_service(content) as endpoint:
        receipt = verify_card_resources(
            card(content), HTTPRegisteredResourceResolver.from_descriptor(descriptor(endpoint))
        )

    assert receipt["resources"] == card(content)["bindings"]
    assert receipt["plan_commit"] == PLAN_COMMIT


def test_http_registered_resource_content_drift_fails_closed(monkeypatch):
    bound = resources()
    drifted = {**bound, "git:source": b"substituted source tree"}
    monkeypatch.setenv("TGW_TEST_RESOURCE_TOKEN", "test-token")
    with resource_service(drifted) as endpoint:
        resolver = HTTPRegisteredResourceResolver.from_descriptor(descriptor(endpoint))
        with pytest.raises(ResourceVerificationError, match="source_tree content hash mismatch"):
            verify_card_resources(card(bound), resolver)


def test_qualified_catalog_rejects_an_unbound_service_descriptor(monkeypatch):
    content = resources()
    monkeypatch.setenv("TGW_TEST_RESOURCE_TOKEN", "test-token")
    with resource_service(content) as endpoint:
        service = descriptor(endpoint)
        resolver = HTTPRegisteredResourceResolver.from_descriptor(service)
        verify_resource_service_registration(catalog(service), service, resolver=resolver)
        substituted = {**service, "endpoint": endpoint + "/substituted"}
        with pytest.raises(ResourceVerificationError, match="not catalog-bound"):
            verify_resource_service_registration(catalog(service), substituted)


def test_registered_resource_service_rejects_plaintext_non_loopback_endpoint():
    with pytest.raises(ResourceVerificationError, match="HTTPS or loopback HTTP"):
        HTTPRegisteredResourceResolver.from_descriptor(
            {
                "schema": "tgw-registered-resource-service/v1",
                "id": "untrusted-remote-service",
                "endpoint": "http://resources.example.invalid",
                "credential_env": None,
                "timeout_seconds": 5,
            }
        )


def test_absent_codegraph_holds_before_a_harness_can_receive_the_card(monkeypatch):
    content = resources()
    missing = {name: value for name, value in content.items() if name != "codegraph:snapshot"}
    monkeypatch.setenv("TGW_TEST_RESOURCE_TOKEN", "test-token")
    with resource_service(missing) as endpoint:
        service = descriptor(endpoint)
        with pytest.raises(ResourceVerificationError, match="registered resource is unavailable: codegraph:snapshot"):
            verify_card_resources(card(content, service), HTTPRegisteredResourceResolver.from_descriptor(service))


def test_every_role_retrieves_card_bound_sources_from_the_qualified_service(tmp_path, monkeypatch):
    """The selected implementation, review, and controller processes fetch all sources.

    The runner deliberately re-verifies every handoff binding through the HTTP
    service.  It receives no resource bytes from the launcher, only the compact
    descriptor and immutable card/receipt identities.
    """

    content = resources()
    monkeypatch.setenv("TGW_TEST_RESOURCE_TOKEN", "test-token")
    runner = tmp_path / "all-role-runner"
    runner.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "from tgw.execution_resources import HTTPRegisteredResourceResolver,verify_card_resources\n"
        "handoff=json.load(sys.stdin)\n"
        "resolver=HTTPRegisteredResourceResolver.from_descriptor(handoff['resource_service'])\n"
        "run=resolver.begin_harness_run(card_hash=handoff['card']['card_hash'],role=handoff['card']['role'],execution_identity=handoff['receipt']['receiver_identity'],handoff_hash=handoff['handoff_hash'],resource_receipt_hash=handoff['resource_receipt']['receipt_hash'],resources=handoff['card']['bindings'])\n"
        "actual=verify_card_resources(handoff['card'],resolver.for_harness_run(run))\n"
        "assert actual == handoff['resource_receipt']\n"
        "attestation=resolver.complete_harness_run(run)\n"
        "role=handoff['card']['role']\n"
        "conditions={'implementation':['implemented'],'independent-review':['reviewed'],'controller-verification':['tested','linted','controller_verified']}[role]\n"
        "print(json.dumps({'outcome':'satisfied','established_conditions':conditions,'artifacts':[], 'resource_receipt_hash':actual['receipt_hash'],'resource_retrieval_attestation':attestation}))\n"
    )
    runner.chmod(0o755)
    registry_value = {
        "schema": "tgw-harness-provider-registry/v1",
        "providers": [
            {
                "id": "qualified-all-role-runner",
                "qualified_roles": ["implementation", "independent-review", "controller-verification"],
                "capabilities": ["source-mutation", "isolated-snapshot-review", "tests"],
                "preference": 1,
                "receiver_profile": {"id": "codex", "version": 1},
                "adapter_requirements": ["promptcraft-card-handoff"],
                "runner": {"kind": "configured-argv", "key": "all"},
            }
        ],
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry_value))
    registry = load_registry(registry_path)
    adapters = {"promptcraft-card-handoff": ROOT / "agent-services/providers/promptcraft/bin/promptcraft-handoff"}
    health = observe_health(registry, coding_config={"commands": {"all": [str(runner)]}}, adapters=adapters)
    with resource_service(content) as endpoint:
        service = descriptor(endpoint)
        resolver = HTTPRegisteredResourceResolver.from_descriptor(service)
        verify_resource_service_registration(catalog(service), service, resolver=resolver)
        receipts = [
            dispatch_role(
                registry,
                health,
                role=role,
                adapters=adapters,
                card_template=card_template(content, service),
                execution_identity=f"qualified-service:{role}",
                required_capabilities=capabilities,
                resource_resolver=resolver,
                resource_service=service,
            )
            for role, capabilities in (
                ("implementation", ["source-mutation"]),
                ("independent-review", ["isolated-snapshot-review"]),
                ("controller-verification", ["tests"]),
            )
        ]
        # The pre-launch receipt is visible in the handoff.  An untrusted
        # runner could echo it without fetching anything; only the service
        # read-back attestation makes that claim fail closed.
        runner.write_text(
            "#!/usr/bin/env python3\n"
            "import json,sys\n"
            "handoff=json.load(sys.stdin)\n"
            "print(json.dumps({'outcome':'satisfied','established_conditions':['implemented'],'artifacts':[],'resource_receipt_hash':handoff['resource_receipt']['receipt_hash']}))\n"
        )
        echo_receipt = dispatch_role(
            registry,
            health,
            role="implementation",
            adapters=adapters,
            card_template=card_template(content, service),
            execution_identity="qualified-service:echo-only",
            required_capabilities=["source-mutation"],
            resource_resolver=resolver,
            resource_service=service,
        )

    assert [receipt["status"] for receipt in receipts] == ["PASS", "PASS", "PASS"]
    assert all(receipt["harness_resource_receipt_hash"] == receipt["resource_receipt_hash"] for receipt in receipts)
    assert all(receipt["harness_retrieval_attestation_hash"] for receipt in receipts)
    assert echo_receipt["status"] == "FAIL"
    assert "did not return a service-issued retrieval attestation" in echo_receipt["artifacts"][-1]["detail"]


def test_non_test_governed_role_script_dispatches_via_registered_resource_service(tmp_path):
    content = resources()
    runner = tmp_path / "runner"
    runner.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "from tgw.execution_resources import HTTPRegisteredResourceResolver,verify_card_resources\n"
        "handoff=json.load(sys.stdin)\n"
        "assert handoff['resource_service']['id'] == 'test-resource-service'\n"
        "resolver=HTTPRegisteredResourceResolver.from_descriptor(handoff['resource_service'])\n"
        "run=resolver.begin_harness_run(card_hash=handoff['card']['card_hash'],role=handoff['card']['role'],execution_identity=handoff['receipt']['receiver_identity'],handoff_hash=handoff['handoff_hash'],resource_receipt_hash=handoff['resource_receipt']['receipt_hash'],resources=handoff['card']['bindings'])\n"
        "actual=verify_card_resources(handoff['card'],resolver.for_harness_run(run))\n"
        "assert actual == handoff['resource_receipt']\n"
        "attestation=resolver.complete_harness_run(run)\n"
        "print(json.dumps({'outcome':'satisfied','established_conditions':['implemented'],\n"
        "'artifacts':[], 'resource_receipt_hash':actual['receipt_hash'],'resource_retrieval_attestation':attestation}))\n"
    )
    runner.chmod(0o755)
    registry = {
        "schema": "tgw-harness-provider-registry/v1",
        "providers": [
            {
                "id": "resource-service-runner",
                "qualified_roles": ["implementation"],
                "capabilities": ["source-mutation"],
                "preference": 1,
                "receiver_profile": {"id": "codex", "version": 1},
                "adapter_requirements": ["promptcraft-card-handoff"],
                "runner": {"kind": "configured-argv", "key": "implementation"},
            }
        ],
    }
    registry_path = tmp_path / "registry.json"
    config_path = tmp_path / "coding.json"
    card_path = tmp_path / "card.json"
    service_path = tmp_path / "resource-service.json"
    catalog_path = tmp_path / "resource-service-catalog.json"
    registry_path.write_text(json.dumps(registry))
    config_path.write_text(json.dumps({"commands": {"implementation": [str(runner)]}}))
    with resource_service(content) as endpoint:
        service = descriptor(endpoint)
        service_path.write_text(json.dumps(service))
        catalog_path.write_text(json.dumps(catalog(service)))
        card_path.write_text(json.dumps(card_template(content, service)))
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_governed_role.py"),
                "--registry", str(registry_path),
                "--coding-config", str(config_path),
                "--card-template", str(card_path),
                "--resource-service", str(service_path),
                "--resource-service-catalog", str(catalog_path),
                "--adapter", f"promptcraft-card-handoff={ROOT / 'agent-services/providers/promptcraft/bin/promptcraft-handoff'}",
                "--role", "implementation",
                "--execution-identity", "resource-service-context:1",
                "--required-capability", "source-mutation",
            ],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "TGW_TEST_RESOURCE_TOKEN": "test-token"},
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "PASS"
    assert receipt["resource_receipt_hash"].startswith("sha256:")

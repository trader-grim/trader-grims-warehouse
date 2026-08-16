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
    HTTPRegisteredResourceResolver,
    ResourceVerificationError,
    verify_card_resources,
)

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


def card_template(content):
    def binding(ref):
        return {"ref": ref, "hash": "sha256:" + hashlib.sha256(content[ref]).hexdigest()}

    return {
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


def card(content):
    unsigned = {
        **card_template(content),
        "schema": "tgw-execution-card/v1",
        "role": "implementation",
        "selected_provider": "resource-service-runner",
        "receiver_profile": {"id": "codex", "version": 1},
    }
    return {**unsigned, "card_hash": canonical_hash(unsigned)}


@contextmanager
def resource_service(content, *, token="test-token"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):  # noqa: N802
            parsed = urlsplit(self.path)
            prefix = "/v1/resources/"
            if not parsed.path.startswith(prefix) or self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error(404)
                return
            ref = unquote(parsed.path.removeprefix(prefix))
            if ref not in content:
                self.send_error(404)
                return
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


def test_non_test_governed_role_script_dispatches_via_registered_resource_service(tmp_path):
    content = resources()
    runner = tmp_path / "runner"
    runner.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "handoff=json.load(sys.stdin)\n"
        "assert handoff['resource_receipt']['resources']['receipt_sink']['ref'] == 'receipt:sink'\n"
        "print(json.dumps({'outcome':'satisfied','established_conditions':['implemented'],'artifacts':[]}))\n"
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
    registry_path.write_text(json.dumps(registry))
    config_path.write_text(json.dumps({"commands": {"implementation": [str(runner)]}}))
    card_path.write_text(json.dumps(card_template(content)))
    with resource_service(content) as endpoint:
        service_path.write_text(json.dumps(descriptor(endpoint)))
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_governed_role.py"),
                "--registry", str(registry_path),
                "--coding-config", str(config_path),
                "--card-template", str(card_path),
                "--resource-service", str(service_path),
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

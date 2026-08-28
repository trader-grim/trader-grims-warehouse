from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import tarfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests import test_governed_review_adapter as governed_fixture
from tests.test_candidate_receipt_sink import (
    W06_PLAN_SOURCE_PATH,
    _candidate_evidence_sink,
    _commit_sink,
    _new_sink,
    _pinned_candidate_evidence_descriptor,
    approved_plan_repository,
    candidate_repository,
    canonical,
    digest,
    w06_plan_materialization,
    write_json,
)
from tests.test_coding_lifecycle import plan_binding
from tgw import context_mcp_server
from tgw.development import coding_lifecycle
from tgw.development.coding_lifecycle import (
    LifecycleStore,
    build_binding,
    candidate_job_binding,
    create,
    job_binding,
)
from tgw.development.coding_review import run_local_review
from tgw.development.coding_review_protection import (
    BROKER_CREDENTIAL_ENV,
    CONTEXT_CREDENTIAL_ENV,
    EVIDENCE_CREDENTIAL_ENV,
    RESOURCE_CREDENTIAL_ENV,
)
from tgw.development.coding_root_effect import (
    RootEffectPaths,
    ensure_review_preparation_request,
    read_review_preparation_response,
)
from tgw.execution_resources import (
    RESOURCE_SERVICE_CAPABILITIES,
    card_resource_receipt,
    content_hash,
    ed25519_public_key,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
)
from tgw.governed_resource_service import (
    ResourceServiceConfig,
    create_resource_service_server,
)
from tgw.governed_review_adapter import (
    HTTPContextBundleClient,
    HTTPReviewEvidenceSink,
)
from tgw.review_contract import ReviewRunnerError
from tgw.review_snapshot import snapshot_hash_entries


def _sudo(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sudo", "-n", *arguments],
        check=True,
        text=True,
        capture_output=True,
    )


def _root_python_result(code: str, *arguments: Path) -> subprocess.CompletedProcess[str]:
    python_path = str(Path(__file__).resolve().parents[1] / "src") + ":" + str(
        Path(__file__).resolve().parents[1]
        / "agent-services/providers/promptcraft"
    )
    return subprocess.run(
        [
            "sudo",
            "-n",
            "/usr/bin/env",
            "PYTHONDONTWRITEBYTECODE=1",
            f"PYTHONPATH={python_path}",
            "/opt/TGW/.venvs/controller/bin/python3",
            "-c",
            code,
            *map(str, arguments),
        ],
        check=False,
        text=True,
        capture_output=True,
    )


def _root_python(code: str, *arguments: Path) -> str:
    result = _root_python_result(code, *arguments)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def _generated_cache_artifacts(root: Path) -> list[str]:
    exact_root = root.resolve(strict=True)
    artifacts: list[str] = []
    for directory, directories, files in os.walk(
        exact_root, topdown=True, followlinks=False,
    ):
        current = Path(directory)
        artifacts.extend(
            str((current / name).relative_to(exact_root))
            for name in directories
            if name in {".ruff_cache", "__pycache__"}
        )
        artifacts.extend(
            str((current / name).relative_to(exact_root))
            for name in files
            if name.endswith(".pyc")
        )
    return sorted(artifacts)


def _archive_snapshot_hash(repository: Path, commit: str) -> str:
    archive = subprocess.check_output(["git", "archive", commit], cwd=repository)
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        for member in stream.getmembers():
            if member.isfile():
                source = stream.extractfile(member)
                assert source is not None
                entries[member.name] = source.read()
    return snapshot_hash_entries(entries)


def _closure(solution: dict) -> dict:
    return {
        name: solution[name]
        for name in (
            "plan_commit",
            "root",
            "complete",
            "selected_providers",
            "selected_capabilities",
            "selected_alternatives",
            "satisfied_installed",
            "work_units",
            "phase_order",
        )
    }


def _install_root_json(source: Path, destination: Path, mode: str = "400") -> None:
    _sudo(
        "install",
        "-o",
        "root",
        "-g",
        "root",
        "-m",
        mode,
        str(source),
        str(destination),
    )


def _private_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode()


def _free_port() -> int:
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _wait_port(port: int, process: subprocess.Popen[str] | None = None) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            detail = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(detail or "disposable broker exited")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"disposable service did not listen on {port}")


class _EvidenceState:
    def __init__(self, bearer: str) -> None:
        self.bearer = bearer
        self.artifacts: dict[str, dict] = {}
        self.executions: list[dict] = []
        self.lock = threading.Lock()


def _evidence_server(state: _EvidenceState) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_arguments: object) -> None:
            return

        def _send(self, status: int, value: dict) -> None:
            raw = canonical(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if self.headers.get("Authorization") != "Bearer " + state.bearer:
                self.send_error(404)
                return False
            return True

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= 1024 * 1024:
                raise ValueError
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError
            return value

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            try:
                value = self._body()
            except (ValueError, json.JSONDecodeError):
                self.send_error(400)
                return
            path = urlsplit(self.path).path
            if path == "/executions":
                execution_hash = value.get("execution_hash")
                if not isinstance(execution_hash, str):
                    self.send_error(400)
                    return
                artifact_ref = f"candidate:governed-review:{execution_hash}"
                with state.lock:
                    state.executions.append(value)
                    state.artifacts[artifact_ref] = value
                self._send(
                    200,
                    {
                        "schema": "tgw-governed-review-publication/v1",
                        "sink_ref": "test:protected-live-evidence",
                        "execution_hash": execution_hash,
                        "artifact_ref": artifact_ref,
                        "artifact_hash": governed_fixture._hash(value),
                    },
                )
                return
            if path == "/artifacts":
                artifact_ref = value.get("ref")
                artifact = value.get("value")
                if not isinstance(artifact_ref, str) or not isinstance(artifact, dict):
                    self.send_error(400)
                    return
                with state.lock:
                    state.artifacts[artifact_ref] = artifact
                self._send(
                    200,
                    {
                        "ref": artifact_ref,
                        "content_sha256": governed_fixture._hash(artifact),
                    },
                )
                return
            self.send_error(404)

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parsed = urlsplit(self.path)
            values = parse_qs(parsed.query)
            if parsed.path != "/artifacts" or set(values) != {"ref"}:
                self.send_error(404)
                return
            with state.lock:
                value = state.artifacts.get(values["ref"][0])
            if value is None:
                self.send_error(404)
                return
            self._send(200, value)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server


def _start_resource_server(
    *,
    config: ResourceServiceConfig,
    bearer: str,
    signing_key: Ed25519PrivateKey,
    port: int,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = create_resource_service_server(
        config,
        {"review-context-backend": bearer},
        signing_private_key=signing_key,
        port=port,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_port(port)
    return server, thread


def _stop_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    thread.join(timeout=10)
    server.server_close()
    assert not thread.is_alive()


def _start_root_broker(
    config_path: Path, credential_root: Path, signing_key_path: Path, port: int,
) -> subprocess.Popen[str]:
    python_path = str(Path(__file__).resolve().parents[1] / "src")
    code = """
import json, signal, sys
from pathlib import Path
from tgw.governed_review_context_broker import broker_server_from_config
credentials = {
    name: json.loads((Path(sys.argv[2]) / (name + '.json')).read_text())['bearer']
    for name in ('context', 'resource', 'broker')
}
environment = {
    'TGW_REVIEW_RESOURCE_CREDENTIAL': credentials['resource'],
    'LIVE_BROKER_MASTER': credentials['broker'],
    'LIVE_READBACK_CREDENTIAL': credentials['context'],
    'LIVE_BROKER_SIGNING': Path(sys.argv[3]).read_text().strip(),
}
server = broker_server_from_config(
    json.loads(Path(sys.argv[1]).read_text()), environment=environment,
    host='127.0.0.1', port=int(sys.argv[4]),
)
stopping = False
def stop(_signal, _frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
server.timeout = 0.2
while not stopping:
    server.handle_request()
server.server_close()
"""
    process = subprocess.Popen(
        [
            "sudo",
            "-n",
            "/usr/bin/env",
            "PYTHONDONTWRITEBYTECODE=1",
            f"PYTHONPATH={python_path}",
            "/opt/TGW/.venvs/controller/bin/python3",
            "-c",
            code,
            str(config_path),
            str(credential_root),
            str(signing_key_path),
            str(port),
        ],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _wait_port(port, process)
    return process


def _stop_broker(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert process.returncode == 0


def _issue_grant(
    protected_root: Path, request_value: dict, request_path: Path,
) -> dict[str, str]:
    request_path.write_bytes(canonical(request_value))
    code = """
import json, sys
from pathlib import Path
from tgw.governed_review_context_broker import FileReviewContextGrantStore
root = Path(sys.argv[1])
request = json.loads(Path(sys.argv[2]).read_text())
credential = json.loads((root / 'credentials' / 'broker.json').read_text())['bearer']
store = FileReviewContextGrantStore(
    root / 'broker-grants', master_credential=credential,
    client_id=request['client_id'],
    catalog_ref=request['resource_service_catalog_ref'],
    catalog_hash=request['resource_service_catalog_hash'],
)
print(json.dumps(store.issue(request), sort_keys=True))
"""
    return json.loads(_root_python(code, protected_root, request_path))


def _grant(request_value: dict) -> dict:
    return {
        "schema": "tgw-governed-review-context-grant/v1",
        "request": request_value,
        "request_hash": governed_fixture._hash(request_value),
    }


@pytest.mark.skipif(
    subprocess.run(
        ["sudo", "-n", "true"], check=False, capture_output=True,
    ).returncode
    != 0,
    reason="passwordless sudo is required for protected review end-to-end coverage",
)
def test_disposable_services_execute_the_protected_governed_review_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    protected_root = Path(
        _sudo(
            "mktemp", "-d", "/var/lib/tgw/coding-protected-review-test-XXXXXXXX"
        ).stdout.strip()
    )
    onboarding_destination = protected_root.with_name(protected_root.name + ".json")
    receipts = protected_root.with_name(protected_root.name + "-receipts")
    resource_server: ThreadingHTTPServer | None = None
    resource_thread: threading.Thread | None = None
    broker_process: subprocess.Popen[str] | None = None
    evidence_server: ThreadingHTTPServer | None = None
    evidence_thread: threading.Thread | None = None
    try:
        _sudo("chmod", "0755", str(protected_root))
        plan_repository, plan_commit = approved_plan_repository(tmp_path)
        candidate, candidate_commit, candidate_tree = candidate_repository(
            tmp_path, plan_commit=plan_commit
        )
        assert _generated_cache_artifacts(candidate) == []
        materialization_pin, materialization = w06_plan_materialization(
            plan_repository, plan_commit=plan_commit
        )
        _candidate_sink, candidate_sink_config, _evidence = _candidate_evidence_sink(
            tmp_path,
            candidate_repo=candidate,
            source_commit=candidate_commit,
            source_tree=candidate_tree,
            plan_commit=plan_commit,
            w06_materialization=materialization,
        )
        _descriptor_authority, descriptor_config = (
            _pinned_candidate_evidence_descriptor(
                tmp_path,
                candidate,
                candidate_sink_config,
                materialization_pin,
            )
        )
        descriptor_value = json.loads(descriptor_config.read_text())

        context_bearer = secrets.token_urlsafe(36)
        evidence_bearer = secrets.token_urlsafe(36)
        resource_bearer = secrets.token_urlsafe(36)
        broker_master = secrets.token_urlsafe(36)
        generations = {
            name: f"protected-live-{name}-v1"
            for name in ("context", "evidence", "resource", "broker")
        }
        credentials = {
            "context": {
                "schema": "tgw-protected-service-credential/v1",
                "purpose": "context",
                "generation": generations["context"],
                "bearer": context_bearer,
            },
            "evidence": {
                "schema": "tgw-protected-service-credential/v1",
                "purpose": "evidence",
                "generation": generations["evidence"],
                "bearer": evidence_bearer,
            },
            "resource": {
                "schema": "tgw-protected-service-credential/v1",
                "purpose": "resource",
                "generation": generations["resource"],
                "bearer": resource_bearer,
            },
            "broker": {
                "schema": "tgw-protected-service-credential/v1",
                "purpose": "broker",
                "generation": generations["broker"],
                "bearer": broker_master,
            },
        }

        evidence_state = _EvidenceState(evidence_bearer)
        evidence_server = _evidence_server(evidence_state)
        evidence_thread = threading.Thread(
            target=evidence_server.serve_forever, daemon=True,
        )
        evidence_thread.start()
        evidence_endpoint = f"http://127.0.0.1:{evidence_server.server_port}"
        evidence_unsigned = {
            "schema": "tgw-governed-review-evidence-sink-client/v1",
            "sink_ref": "test:protected-live-evidence",
            "endpoint": evidence_endpoint,
            "credential_env": EVIDENCE_CREDENTIAL_ENV,
            "timeout_seconds": 5,
        }
        evidence_sink = {
            **evidence_unsigned,
            "descriptor_hash": governed_fixture._hash(evidence_unsigned),
        }

        resource_port = _free_port()
        broker_port = _free_port()
        backend_private_key = Ed25519PrivateKey.generate()
        broker_private_key = Ed25519PrivateKey.generate()
        backend_service = {
            "schema": "tgw-registered-resource-service/v2",
            "id": "review-source-service",
            "client_id": "review-context-backend",
            "endpoint": f"http://127.0.0.1:{resource_port}",
            "credential_env": RESOURCE_CREDENTIAL_ENV,
            "timeout_seconds": 5,
        }
        backend_catalog = {
            "schema": "tgw-registered-resource-service-catalog/v3",
            "catalog_ref": "catalog:protected-live-source@1",
            "plan_commit": plan_commit,
            "services": [
                {
                    "id": backend_service["id"],
                    "client_id": backend_service["client_id"],
                    "descriptor_hash": resource_service_descriptor_hash(
                        backend_service
                    ),
                    "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
                    "attestation_key_id": "protected-source-key-1",
                    "attestation_public_key": ed25519_public_key(
                        backend_private_key
                    ),
                }
            ],
        }
        wrapper_service = {
            "schema": "tgw-registered-resource-service/v2",
            "id": "review-context-service",
            "client_id": "review-provider",
            "endpoint": f"http://127.0.0.1:{broker_port}",
            "credential_env": CONTEXT_CREDENTIAL_ENV,
            "timeout_seconds": 5,
        }
        wrapper_catalog = {
            "schema": "tgw-registered-resource-service-catalog/v3",
            "catalog_ref": "catalog:protected-live-context@1",
            "plan_commit": plan_commit,
            "services": [
                {
                    "id": wrapper_service["id"],
                    "client_id": wrapper_service["client_id"],
                    "descriptor_hash": resource_service_descriptor_hash(
                        wrapper_service
                    ),
                    "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
                    "attestation_key_id": "protected-context-key-1",
                    "attestation_public_key": ed25519_public_key(
                        broker_private_key
                    ),
                }
            ],
        }

        monkeypatch.setattr(governed_fixture, "PLAN", plan_commit)
        monkeypatch.setattr(governed_fixture, "COMMIT", candidate_commit)
        monkeypatch.setattr(governed_fixture, "TREE", candidate_tree)
        expected_snapshot = _archive_snapshot_hash(candidate, candidate_commit)
        (
            _unused_source,
            _executable,
            provider_identity,
            environment,
            _unused_context_key,
        ) = governed_fixture._fixture(
            tmp_path / "provider",
            review_snapshot_hash=expected_snapshot,
        )
        service_unsigned = {
            **provider_identity["context_bundle_service"],
            "endpoint": wrapper_service["endpoint"],
            "credential_env": CONTEXT_CREDENTIAL_ENV,
            "timeout_seconds": wrapper_service["timeout_seconds"],
            "service_id": wrapper_service["id"],
            "client_id": wrapper_service["client_id"],
            "broker_endpoint": wrapper_service["endpoint"],
            "resource_service_descriptor_hash": resource_service_descriptor_hash(
                wrapper_service
            ),
            "resource_service_catalog_ref": wrapper_catalog["catalog_ref"],
            "resource_service_catalog_hash": resource_service_catalog_hash(
                wrapper_catalog
            ),
            "attestation_key_id": "protected-context-key-1",
            "attestation_public_key": ed25519_public_key(broker_private_key),
        }
        service_unsigned.pop("descriptor_hash")
        provider_identity["context_bundle_service"] = {
            **service_unsigned,
            "descriptor_hash": governed_fixture._hash(service_unsigned),
        }
        execution_environment_hash = provider_identity["artifacts"][
            "execution_environment"
        ]["content_sha256"]

        registered_sources = {
            "plan_input": subprocess.check_output(
                ["git", "show", f"{plan_commit}:{W06_PLAN_SOURCE_PATH}"],
                cwd=plan_repository,
            ),
            "plan_commit": canonical(
                {
                    "schema": "tgw-approved-plan-binding/v1",
                    "approved_commit": plan_commit,
                    "evidence_commit": materialization_pin["solution"]["commit"],
                }
            ),
            "plan_graph": canonical(materialization["graph"]),
            "execution_environment": Path(
                provider_identity["artifacts"]["execution_environment"][
                    "resolved_path"
                ]
            ).read_bytes(),
            "authority_conditions": canonical(
                _closure(materialization["solution"])
            ),
        }
        input_root = protected_root / "registered-inputs"
        _sudo(
            "install", "-d", "-o", "root", "-g", "root", "-m", "0755",
            str(input_root),
        )
        registered_inputs = {}
        for name, raw in registered_sources.items():
            source = tmp_path / f"registered-{name}.bin"
            source.write_bytes(raw)
            destination = input_root / f"{name}.bin"
            _sudo(
                "install", "-o", "root", "-g", "root", "-m", "0444",
                str(source), str(destination),
            )
            registered_inputs[name] = {
                "ref": f"mcp:tgw-context/registered/{name}/{plan_commit}",
                "hash": content_hash(raw),
                "path": str(destination),
            }
        assert registered_inputs["execution_environment"]["hash"] == (
            execution_environment_hash
        )

        profile = {
            "schema": "tgw-local-coding-protected-review-profile/v2",
            "provider_identity": provider_identity,
            "environment": environment,
            "evidence_sink": evidence_sink,
            "resource_service": wrapper_service,
            "resource_service_catalog": wrapper_catalog,
            "registered_resource_service": backend_service,
            "registered_resource_service_catalog": backend_catalog,
            "registered_resource_inputs": registered_inputs,
            "credential_generations": generations,
            "receiver_profile": {"id": "claude-code", "version": 1},
            "environment_preflight_receipt": {
                "schema": "tgw-environment-preflight-receipt/v1",
                "result": "PASS",
                "catalog_sha256": execution_environment_hash,
                "actor": "claude",
                "profile": "development",
                "attempt_id": "protected-live-e2e",
                "tools": [],
            },
            "skill_contract_hash": governed_fixture._fixture_skill_contract_hash(
                provider_identity
            ),
            "timeout_seconds": 10,
            "output_limit": 8 * 1024 * 1024,
        }

        execution_sink = tmp_path / "initial-execution-sink"
        _new_sink(
            execution_sink,
            email="initial@example.invalid",
            name="Initial protected execution sink",
        )
        initial_raw = write_json(execution_sink / "initial.json", {"held": True})
        initial_descriptor = _commit_sink(
            execution_sink,
            sink_id="initial-execution-evidence",
            artifacts=[
                {
                    "ref": "artifact:initial:held",
                    "path": "initial.json",
                    "content_sha256": digest(initial_raw),
                }
            ],
            message="initial protected evidence binding",
        )
        onboarding = {
            "schema": "tgw-local-coding-protected-review-onboarding/v2",
            "request_profile": profile,
            "candidate_evidence_descriptor_config": descriptor_value,
            "execution_evidence_sink_config": initial_descriptor,
            "execution_evidence_pin_source_config": initial_descriptor,
            "credentials": credentials,
        }
        onboarding_source = tmp_path / "onboarding.json"
        onboarding_source.write_text(json.dumps(onboarding, sort_keys=True))
        _install_root_json(onboarding_source, onboarding_destination)

        live_config = Path("/opt/TGW/tgw-lib/config")
        config_identity_before = live_config.stat(follow_symlinks=False)
        repair_code = """
import json, sys
from pathlib import Path
from tgw.doctor_cli import DoctorPaths, repair_protected_review
p=DoctorPaths(repository=Path(sys.argv[1]), protected_review_root=Path(sys.argv[2]), protected_review_onboarding=Path(sys.argv[3]), receipts=Path(sys.argv[4]))
print(json.dumps(repair_protected_review(p), sort_keys=True))
print(json.dumps(repair_protected_review(p), sort_keys=True))
"""
        repairs = [
            json.loads(line)
            for line in _root_python(
                repair_code,
                candidate,
                protected_root,
                onboarding_destination,
                receipts,
            ).splitlines()
        ]
        assert repairs[0]["changed"] is True
        assert repairs[1]["changed"] is False
        assert repairs[0]["config_root_unchanged"] is True
        config_identity_after = live_config.stat(follow_symlinks=False)
        assert (
            config_identity_after.st_uid,
            config_identity_after.st_gid,
            stat.S_IMODE(config_identity_after.st_mode),
        ) == (
            config_identity_before.st_uid,
            config_identity_before.st_gid,
            stat.S_IMODE(config_identity_before.st_mode),
        )

        stale_credential = tmp_path / "stale-context.json"
        stale_credential.write_text(
            json.dumps(
                {
                    **credentials["context"],
                    "generation": "stale-context-generation",
                }
            )
        )
        _install_root_json(
            stale_credential,
            protected_root / "credentials" / "context.json",
        )
        check_code = """
import json, sys
from pathlib import Path
from tgw.doctor_cli import DoctorPaths, check_protected_review
p=DoctorPaths(repository=Path(sys.argv[1]), protected_review_root=Path(sys.argv[2]), protected_review_onboarding=Path(sys.argv[3]), receipts=Path(sys.argv[4]))
print(json.dumps(check_protected_review(p), sort_keys=True))
"""
        stale_check = json.loads(
            _root_python(
                check_code,
                candidate,
                protected_root,
                onboarding_destination,
                receipts,
            )
        )
        assert stale_check["state"] == "FAIL"
        assert context_bearer not in json.dumps(stale_check)
        repaired = json.loads(
            _root_python(
                repair_code,
                candidate,
                protected_root,
                onboarding_destination,
                receipts,
            ).splitlines()[0]
        )
        assert repaired["changed"] is True

        backend_config = ResourceServiceConfig.parse(
            {
                "schema": "tgw-governed-resource-service-config/v6",
                "service_id": backend_service["id"],
                "clients": [
                    {
                        "id": backend_service["client_id"],
                        "credential_env": "LIVE_BACKEND_CREDENTIAL",
                        "execution_identity": "review-context-broker-service",
                        "role": "independent-review",
                    }
                ],
                "attestation_key_id": "protected-source-key-1",
                "attestation_private_key_env": "LIVE_SOURCE_SIGNING",
                "harness_run_ttl_seconds": 60,
                "completed_run_ttl_seconds": 60,
                "max_open_runs_per_client": 8,
                "max_completed_runs_per_client": 8,
                "resources": [],
                "resource_registry_root": str(
                    protected_root / "registered-resources"
                ),
            }
        )
        resource_server, resource_thread = _start_resource_server(
            config=backend_config,
            bearer=resource_bearer,
            signing_key=backend_private_key,
            port=resource_port,
        )
        broker_config = {
            "schema": "tgw-context-review-broker-config/v3",
            "backend_descriptor": backend_service,
            "backend_execution_identity": "review-context-broker-service",
            "backend_attestation_key_id": "protected-source-key-1",
            "backend_attestation_public_key": ed25519_public_key(
                backend_private_key
            ),
            "backend_resource_service_catalog": backend_catalog,
            "backend_resource_service_catalog_hash": resource_service_catalog_hash(
                backend_catalog
            ),
            "service_id": wrapper_service["id"],
            "client_id": wrapper_service["client_id"],
            "attestation_key_id": "protected-context-key-1",
            "attestation_public_key": ed25519_public_key(broker_private_key),
            "signing_private_key_env": "LIVE_BROKER_SIGNING",
            "request_grant_root": str(protected_root / "broker-grants"),
            "request_credential_env": "LIVE_BROKER_MASTER",
            "request_resource_service_catalog_ref": wrapper_catalog["catalog_ref"],
            "request_resource_service_catalog_hash": resource_service_catalog_hash(
                wrapper_catalog
            ),
            "readback_clients": [
                {
                    "client_id": wrapper_service["client_id"],
                    "credential_env": "LIVE_READBACK_CREDENTIAL",
                }
            ],
        }
        broker_config_source = tmp_path / "broker.json"
        broker_config_source.write_bytes(canonical(broker_config))
        broker_config_path = protected_root / "broker.json"
        _install_root_json(broker_config_source, broker_config_path)
        broker_key_source = tmp_path / "broker-signing.key"
        broker_key_source.write_text(_private_key(broker_private_key))
        broker_key_path = protected_root / "broker-signing.key"
        _install_root_json(broker_key_source, broker_key_path)
        broker_process = _start_root_broker(
            broker_config_path,
            protected_root / "credentials",
            broker_key_path,
            broker_port,
        )

        plan_todo = plan_binding(
            candidate,
            source=candidate_commit,
            source_tree=candidate_tree,
        )
        plan_todo.update(
            {
                "plan_commit": plan_commit,
                "solution_hash": materialization["solution_hash"],
                "closure_hash": materialization["closure_hash"],
            }
        )
        store = LifecycleStore(tmp_path / "lifecycles", group_gid=os.getegid())
        binding = build_binding(
            target=1915,
            plan_binding=plan_todo,
            source_tree=candidate_tree,
        )
        record = create(store, target=1915, binding=binding)
        candidate_receipt = {
            "schema": "tgw-local-coding-candidate-evidence/v1",
            "root_id": record["root_id"],
            "binding_hash": binding["binding_hash"],
            "worktree": str(candidate),
            "commit": candidate_commit,
            "tree": candidate_tree,
            "classification": "CLOSED_CANDIDATE",
        }
        record["effects"]["candidate"] = {
            "receipt": candidate_receipt,
            "receipt_hash": "sha256:"
            + hashlib.sha256(canonical(candidate_receipt)).hexdigest(),
            "idempotency_key": coding_lifecycle.stage_idempotency_key(
                record, "candidate"
            ),
        }
        store.put(record)

        trigger_root = tmp_path / "root-effects"
        trigger_root.mkdir()
        _sudo("chown", f"0:{os.getegid()}", str(trigger_root))
        _sudo("chmod", "3770", str(trigger_root))
        paths = RootEffectPaths(
            request_root=trigger_root,
            lifecycle_root=store.root,
            repository=candidate,
            runtime_root=tmp_path / "runtime",
            coding_config=tmp_path / "coding.json",
            protected_review_config=protected_root / "config.json",
            group_gid=os.getegid(),
            root_uid=0,
        )
        preparation_request = ensure_review_preparation_request(paths, record)
        serialized_preparation = json.dumps(preparation_request).lower()
        assert not any(
            forbidden in serialized_preparation
            for forbidden in ("/var/lib", "request_path", "provider", "result")
        )
        consume_code = """
import sys
from pathlib import Path
from tgw.development.coding_root_effect import RootEffectPaths, consume_once
p=RootEffectPaths(
    request_root=Path(sys.argv[1]), lifecycle_root=Path(sys.argv[2]),
    repository=Path(sys.argv[3]), runtime_root=Path(sys.argv[4]),
    coding_config=Path(sys.argv[5]), protected_review_config=Path(sys.argv[6]),
    group_gid=int(sys.argv[7]), root_uid=0,
)
print(consume_once(p))
"""
        assert _root_python(
            consume_code,
            trigger_root,
            store.root,
            candidate,
            tmp_path / "runtime",
            tmp_path / "coding.json",
            protected_root / "config.json",
            Path(str(os.getegid())),
        ).strip() == "1"
        preparation = read_review_preparation_response(paths, preparation_request)
        assert preparation is not None
        assert preparation["candidate_commit"] == candidate_commit
        assert preparation["candidate_tree"] == candidate_tree
        assert preparation["plan_commit"] == plan_commit
        request_path = protected_root / "requests" / f"{candidate_commit}.request.json"
        governed_request = json.loads(request_path.read_text())
        request_raw = json.dumps(governed_request, sort_keys=True)
        assert governed_request["source_commit"] == candidate_commit
        assert governed_request["source_tree"] == candidate_tree
        assert governed_request["plan_commit"] == plan_commit
        assert all(
            secret not in request_raw
            for secret in (
                context_bearer, evidence_bearer, resource_bearer, broker_master,
            )
        )
        assert governed_request["review_packet"]["plan"] == {
            "commit": plan_commit,
            "solution_hash": materialization["solution_hash"],
            "closure_hash": materialization["closure_hash"],
        }
        grant_path = protected_root / "broker-grants" / (
            governed_request["context_grant"]["request"]["challenge"] + ".json"
        )
        grant_value = json.loads(
            _root_python(
                "import json,sys; print(json.dumps(json.loads(open(sys.argv[1]).read()), sort_keys=True))",
                grant_path,
            )
        )
        assert grant_value["request"] == governed_request["context_grant"]["request"]
        assert set(grant_value) == {
            "schema", "request", "request_hash", "bearer_hash",
        }

        # Both durable stores must survive daemon restart before one-use
        # consumption; no request or resource is reissued during restart.
        _stop_broker(broker_process)
        broker_process = None
        _stop_server(resource_server, resource_thread)
        resource_server = None
        resource_thread = None
        resource_server, resource_thread = _start_resource_server(
            config=backend_config,
            bearer=resource_bearer,
            signing_key=backend_private_key,
            port=resource_port,
        )
        broker_process = _start_root_broker(
            broker_config_path,
            protected_root / "credentials",
            broker_key_path,
            broker_port,
        )

        load_context = tmp_path / "load-context.json"
        load_evidence = tmp_path / "load-evidence.json"
        load_broker = tmp_path / "load-broker.json"
        for path, name in (
            (load_context, "context"),
            (load_evidence, "evidence"),
            (load_broker, "broker"),
        ):
            path.write_bytes(canonical(credentials[name]))
            path.chmod(0o400)

        handoff = governed_request["handoff"]
        context_grant = governed_request["context_grant"]
        challenge = context_grant["request"]["challenge"]
        context_environment = {
            BROKER_CREDENTIAL_ENV + "_FILE": str(load_broker),
            "TGW_CONTEXT_REVIEW_SKILL_CONTRACT_HASH": profile[
                "skill_contract_hash"
            ],
            "TGW_CONTEXT_REVIEW_UID": str(
                provider_identity["sandbox_identity"]["uid"]
            ),
            "TGW_CONTEXT_REVIEW_GID": str(
                provider_identity["sandbox_identity"]["gid"]
            ),
            "TGW_CONTEXT_RESOURCE_SERVICE_ID": wrapper_service["id"],
            "TGW_CONTEXT_RESOURCE_SERVICE_CLIENT_ID": wrapper_service["client_id"],
            "TGW_CONTEXT_REVIEW_BROKER_ENDPOINT": wrapper_service["endpoint"],
            "TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_REF": wrapper_catalog[
                "catalog_ref"
            ],
            "TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_HASH": resource_service_catalog_hash(
                wrapper_catalog
            ),
            "TGW_CONTEXT_ATTESTATION_KEY_ID": "protected-context-key-1",
            "TGW_CONTEXT_ATTESTATION_PUBLIC_KEY": ed25519_public_key(
                broker_private_key
            ),
        }
        for name, value in context_environment.items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv(BROKER_CREDENTIAL_ENV, raising=False)

        context_receipt, visible_resources = (
            context_mcp_server.execute_governed_review_context_request(
                challenge=challenge,
                card_json=json.dumps(handoff["card"]),
                handoff_hash=handoff["handoff_hash"],
                resource_receipt_hash=handoff["resource_receipt"]["receipt_hash"],
                skill_contract_hash=profile["skill_contract_hash"],
                grant_json=json.dumps(context_grant),
            )
        )
        assert context_receipt["status"] == "PASS"
        codegraph = json.loads(visible_resources["codegraph_snapshot"]["content"])
        assert (codegraph["commit"], codegraph["tree"]) == (
            candidate_commit,
            candidate_tree,
        )
        assert json.loads(visible_resources["plan_commit"]["content"])[
            "approved_commit"
        ] == plan_commit
        generation_code = """
import json, sys
from pathlib import Path
from tgw.governed_resource_service import load_registered_resource_generation
value=load_registered_resource_generation(Path(sys.argv[1]), sys.argv[2])
print(json.dumps({'source': value.source, 'bindings': value.bindings}, sort_keys=True))
"""
        generation = json.loads(
            _root_python(
                generation_code,
                protected_root / "registered-resources",
                Path(candidate_commit),
            )
        )
        assert generation["source"] == {
            "commit": candidate_commit,
            "tree": candidate_tree,
            "canonical_installed": False,
        }
        assert generation["bindings"] == handoff["card"]["bindings"]

        with pytest.raises(context_mcp_server.ContextError, match="broker failed"):
            context_mcp_server.execute_governed_review_context_request(
                challenge=challenge,
                card_json=json.dumps(handoff["card"]),
                handoff_hash=handoff["handoff_hash"],
                resource_receipt_hash=handoff["resource_receipt"]["receipt_hash"],
                skill_contract_hash=profile["skill_contract_hash"],
                grant_json=json.dumps(context_grant),
            )

        original_request = context_grant["request"]
        expired_challenge = secrets.token_hex(32)
        expired_request = {
            **original_request,
            "challenge": expired_challenge,
            "execution_identity": (
                f"governed-review:{expired_challenge}:"
                f"uid={provider_identity['sandbox_identity']['uid']}:"
                f"gid={provider_identity['sandbox_identity']['gid']}"
            ),
            "issued_at": (
                datetime.now(timezone.utc) - timedelta(minutes=14)
            ).isoformat(),
            "not_before": (
                datetime.now(timezone.utc) - timedelta(minutes=14)
            ).isoformat(),
            "expires_at": (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        }
        _issue_grant(
            protected_root,
            expired_request,
            tmp_path / "expired-grant.json",
        )
        with pytest.raises(context_mcp_server.ContextError, match="broker failed"):
            context_mcp_server.execute_governed_review_context_request(
                challenge=expired_challenge,
                card_json=json.dumps(handoff["card"]),
                handoff_hash=handoff["handoff_hash"],
                resource_receipt_hash=handoff["resource_receipt"]["receipt_hash"],
                skill_contract_hash=profile["skill_contract_hash"],
                grant_json=json.dumps(_grant(expired_request)),
            )

        incorrect_challenge = secrets.token_hex(32)
        incorrect_unsigned_card = dict(handoff["card"])
        incorrect_unsigned_card.pop("card_hash")
        incorrect_unsigned_card["bindings"] = {
            name: dict(value)
            for name, value in handoff["card"]["bindings"].items()
        }
        incorrect_unsigned_card["bindings"]["plan_graph"]["hash"] = (
            "sha256:" + "0" * 64
        )
        incorrect_card = {
            **incorrect_unsigned_card,
            "card_hash": governed_fixture._hash(incorrect_unsigned_card),
        }
        incorrect_receipt = card_resource_receipt(incorrect_card)
        now = datetime.now(timezone.utc)
        incorrect_request = {
            **original_request,
            "challenge": incorrect_challenge,
            "card_hash": incorrect_card["card_hash"],
            "execution_identity": (
                f"governed-review:{incorrect_challenge}:"
                f"uid={provider_identity['sandbox_identity']['uid']}:"
                f"gid={provider_identity['sandbox_identity']['gid']}"
            ),
            "resource_receipt_hash": incorrect_receipt["receipt_hash"],
            "resources": incorrect_card["bindings"],
            "issued_at": (now - timedelta(seconds=1)).isoformat(),
            "not_before": (now - timedelta(seconds=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }
        _issue_grant(
            protected_root,
            incorrect_request,
            tmp_path / "incorrect-grant.json",
        )
        with pytest.raises(context_mcp_server.ContextError, match="broker failed"):
            context_mcp_server.execute_governed_review_context_request(
                challenge=incorrect_challenge,
                card_json=json.dumps(incorrect_card),
                handoff_hash=handoff["handoff_hash"],
                resource_receipt_hash=incorrect_receipt["receipt_hash"],
                skill_contract_hash=profile["skill_contract_hash"],
                grant_json=json.dumps(_grant(incorrect_request)),
            )

        with monkeypatch.context() as missing:
            missing.delenv(CONTEXT_CREDENTIAL_ENV, raising=False)
            missing.delenv(CONTEXT_CREDENTIAL_ENV + "_FILE", raising=False)
            missing.delenv(EVIDENCE_CREDENTIAL_ENV, raising=False)
            missing.delenv(EVIDENCE_CREDENTIAL_ENV + "_FILE", raising=False)
            with pytest.raises(
                ReviewRunnerError, match="context credential is unavailable",
            ):
                HTTPContextBundleClient(provider_identity["context_bundle_service"])
            with pytest.raises(
                ReviewRunnerError, match="evidence credential is unavailable",
            ):
                HTTPReviewEvidenceSink(evidence_sink)
        unsafe_credential = tmp_path / "unsafe-context.json"
        unsafe_credential.write_bytes(canonical(credentials["context"]))
        unsafe_credential.chmod(0o444)
        with monkeypatch.context() as unsafe:
            unsafe.setenv(CONTEXT_CREDENTIAL_ENV + "_FILE", str(unsafe_credential))
            unsafe.delenv(CONTEXT_CREDENTIAL_ENV, raising=False)
            with pytest.raises(ReviewRunnerError, match="credential is unsafe"):
                HTTPContextBundleClient(provider_identity["context_bundle_service"])

        monkeypatch.setenv(CONTEXT_CREDENTIAL_ENV + "_FILE", str(load_context))
        monkeypatch.setenv(EVIDENCE_CREDENTIAL_ENV + "_FILE", str(load_evidence))
        monkeypatch.delenv(CONTEXT_CREDENTIAL_ENV, raising=False)
        monkeypatch.delenv(EVIDENCE_CREDENTIAL_ENV, raising=False)
        lifecycle_binding = job_binding(record)
        candidate_binding = candidate_job_binding(
            lifecycle_binding,
            commit=candidate_commit,
            tree=candidate_tree,
        )
        task = {
            "schema": "coding-task/v1",
            "todo_id": 1915,
            "agent": "codex",
            "body": "Apply the bounded protected-review defensive remediation.",
        }
        payload = {
            "status": "PASS",
            "todo_id": 1915,
            "treatment_id": "claude-review",
            "job_id": "protected-live-review-job",
            "plan_binding": record["binding"]["plan_todo_binding"],
            "coding_lifecycle": lifecycle_binding,
            "coding_candidate": candidate_binding,
            "task_spec": task,
        }
        projection = run_local_review(
            payload,
            candidate,
            protected_config=protected_root / "config.json",
        )
        assert projection["outcome"] == "satisfied"
        execution_hash = projection["artifacts"][0]["protected_review"][
            "execution_hash"
        ]
        with evidence_state.lock:
            assert len(evidence_state.executions) == 1
            assert evidence_state.executions[0]["execution_hash"] == execution_hash
            assert any(
                artifact.get("schema")
                == "tgw-candidate-governed-execution-receipt/v1"
                for artifact in evidence_state.artifacts.values()
            )
        context_client = HTTPContextBundleClient(
            provider_identity["context_bundle_service"]
        )
        with pytest.raises(ReviewRunnerError, match="context readback failed"):
            context_client.read(challenge)

        large_file = candidate / "oversized-candidate.bin"
        with large_file.open("wb") as stream:
            stream.seek(64 * 1024 * 1024)
            stream.write(b"X")
        subprocess.run(["git", "add", large_file.name], cwd=candidate, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "oversized disposable candidate"],
            cwd=candidate,
            check=True,
        )
        large_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=candidate, text=True,
        ).strip()
        large_tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=candidate, text=True,
        ).strip()
        prepare_large_code = """
import sys
from pathlib import Path
from tgw.development.coding_review import load_protected_review_config
from tgw.development.coding_review_protection import prepare_governed_request
repository=Path(sys.argv[1])
config=load_protected_review_config(Path(sys.argv[2]), candidate_repository=repository)
prepare_governed_request(
    repository=repository, candidate_commit=sys.argv[3], candidate_tree=sys.argv[4],
    plan_commit=sys.argv[5], solution_hash=sys.argv[6], closure_hash=sys.argv[7],
    profile_path=config['request_profile_config'],
    candidate_descriptor_path=config['candidate_evidence_descriptor_config'],
    request_root=config['request_root'], snapshot_root=config['snapshot_root'],
    resource_registry_root=config['resource_registry_root'],
    broker_grant_root=config['broker_grant_root'],
    credential_paths={
        'context': config['context_credential_config'],
        'evidence': config['evidence_credential_config'],
        'resource': config['resource_credential_config'],
        'broker': config['broker_credential_config'],
    },
)
"""
        large_result = _root_python_result(
            prepare_large_code,
            candidate,
            protected_root / "config.json",
            Path(large_commit),
            Path(large_tree),
            Path(plan_commit),
            Path(materialization["solution_hash"]),
            Path(materialization["closure_hash"]),
        )
        assert large_result.returncode != 0
        assert "candidate archive file exceeds its bound" in large_result.stderr
        assert not (protected_root / "snapshots" / large_commit).exists()
        assert not (
            protected_root / "requests" / f"{large_commit}.request.json"
        ).exists()
        assert not any(
            item.name.startswith(f".{large_commit}")
            for root in (
                protected_root / "snapshots",
                protected_root / "registered-resources",
            )
            for item in root.iterdir()
        )
        assert _generated_cache_artifacts(candidate) == []
    finally:
        if broker_process is not None:
            _stop_broker(broker_process)
        if resource_server is not None and resource_thread is not None:
            _stop_server(resource_server, resource_thread)
        if evidence_server is not None and evidence_thread is not None:
            _stop_server(evidence_server, evidence_thread)
        for target in (protected_root, onboarding_destination, receipts):
            if str(target).startswith(
                "/var/lib/tgw/coding-protected-review-test-"
            ):
                subprocess.run(
                    ["sudo", "-n", "rm", "-rf", "--", str(target)],
                    check=False,
                )

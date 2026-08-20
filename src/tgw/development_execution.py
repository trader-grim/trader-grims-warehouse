"""Worker-side execution of a Plan-bound, harness-neutral development lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    RESOURCE_SERVICE_CAPABILITIES,
    HTTPRegisteredResourceResolver,
    content_hash,
    ed25519_public_key,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
)
from tgw.governed_coding import admission_gate, dispatch_role
from tgw.governed_resource_service import ResourceServiceConfig, create_resource_service_server
from tgw.harness_registry import load_registry, observe_health
from tgw.queue.worker_base import HardFailure

_ROLE_ORDER = ("implementation", "controller-verification", "independent-review")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=False, text=True, capture_output=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode:
        raise HardFailure(f"development Git operation failed: {(result.stderr or result.stdout)[-500:]}")
    return result.stdout.strip()


def _config(config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    coding = config.get("coding")
    development = coding.get("development") if isinstance(coding, Mapping) else None
    if not isinstance(coding, Mapping) or not isinstance(development, Mapping):
        raise HardFailure("governed development worker configuration is unavailable")
    return dict(coding), dict(development)


def _load_registry(development: Mapping[str, Any], expected_hash: str) -> dict[str, Any]:
    path = development.get("provider_registry_path")
    if not isinstance(path, str):
        raise HardFailure("development provider registry path is unavailable")
    registry = load_registry(path)
    if _hash(registry) != expected_hash:
        raise HardFailure("development provider registry differs from the queued binding")
    return registry


def _adapters(development: Mapping[str, Any]) -> dict[str, Path]:
    value = development.get("adapters")
    if not isinstance(value, Mapping):
        raise HardFailure("development adapter bindings are unavailable")
    adapters = {str(name): Path(str(path)).resolve() for name, path in value.items()}
    if not adapters or any(not path.exists() for path in adapters.values()):
        raise HardFailure("development adapter binding is unavailable")
    return adapters


def _card_worktree(repository: Path, card: Mapping[str, Any], source: str, index: int) -> Path:
    allocation = card.get("allocation")
    raw = allocation.get("worktree") if isinstance(allocation, Mapping) else None
    if not isinstance(raw, str):
        raise HardFailure("development card worktree allocation is invalid")
    worktree = Path(raw)
    if worktree.is_symlink():
        raise HardFailure("development card worktree must not be a symlink")
    branch = "development/card-" + str(index).zfill(3) + "-" + card["idempotency_key"][7:19]
    if not worktree.exists():
        worktree.parent.mkdir(parents=True, exist_ok=True)
        added = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree), source],
            cwd=repository, check=False, text=True, capture_output=True,
        )
        if added.returncode:
            raise HardFailure(f"failed to create card worktree: {added.stderr[-500:]}")
    if Path(_git(worktree, "rev-parse", "--show-toplevel")) != worktree:
        raise HardFailure("development card worktree identity is invalid")
    if _git(worktree, "rev-parse", "HEAD") != source:
        raise HardFailure("development card worktree source changed before launch")
    if _git(worktree, "status", "--porcelain=v1", "--untracked-files=all"):
        raise HardFailure("development card worktree is not clean before launch")
    return worktree


def _checkpoint(worktree: Path, card: Mapping[str, Any]) -> tuple[str, str]:
    if not _git(worktree, "status", "--porcelain=v1", "--untracked-files=all"):
        raise HardFailure("implementation provider produced no candidate change")
    _git(worktree, "add", "-A")
    result = subprocess.run(
        [
            "git", "-c", "user.name=TGW Development Controller",
            "-c", "user.email=development-controller@tgw.local",
            "commit", "-m", f"candidate: {card['unit']} implementation",
        ],
        cwd=worktree, check=False, text=True, capture_output=True,
    )
    if result.returncode:
        raise HardFailure(f"controller could not checkpoint implementation: {result.stderr[-500:]}")
    return _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")


def _resource_values(
    document: Mapping[str, Any], card: Mapping[str, Any], worktree: Path,
    source: str, prior_receipts: list[dict[str, Any]], coding: Mapping[str, Any],
) -> dict[str, bytes]:
    lifecycle = document["lifecycle"]
    request = lifecycle.get("request", {})
    template = card["execution_card_template"]
    values: dict[str, Any] = {
        "plan_input": {"request": request, "root": card["root"], "unit": card["unit"]},
        "plan_commit": template["plan_commit"],
        "plan_graph": lifecycle.get("resolution"),
        "codegraph_snapshot": {
            "commit": source, "tree": _git(worktree, "rev-parse", "HEAD^{tree}"),
            "entries": _git(worktree, "ls-tree", "-r", "--full-tree", "HEAD").splitlines(),
        },
        "source_tree": {
            "repository": str(Path(coding["repository_root"]).resolve()),
            "commit": source, "tree": _git(worktree, "rev-parse", "HEAD^{tree}"),
        },
        "execution_environment": {
            "python": sys.executable, "python_version": sys.version,
            "commands": coding.get("commands", {}),
        },
        "authority_conditions": {
            "authority": template["authority"], "exclusions": template["exclusions"],
            "acceptance": template["acceptance"], "lease": template["lease"],
        },
        "candidate_evidence": {
            "source_commit": source, "prior_role_receipts": prior_receipts,
            "status": _git(worktree, "status", "--porcelain=v1", "--untracked-files=all"),
        },
        "receipt_sink": {
            "kind": "coding-provision/v2", "request_id": document["request_id"],
            "idempotency_key": card["idempotency_key"],
        },
    }
    return {
        name: (value.encode() if isinstance(value, str) else _canonical(value))
        for name, value in values.items()
    }


def _serve_resources(
    *, card: Mapping[str, Any], values: Mapping[str, bytes], root: Path,
) -> tuple[Any, threading.Thread, dict[str, Any], dict[str, Any], HTTPRegisteredResourceResolver, dict[str, str]]:
    root.mkdir(parents=True, exist_ok=True)
    resources = []
    bindings: dict[str, dict[str, str]] = {}
    for name in sorted(CARD_RESOURCE_NAMES):
        path = (root / f"{name}.json").resolve()
        path.write_bytes(values[name])
        ref = f"development:{card['idempotency_key'][7:23]}:{name}"
        digest = content_hash(values[name])
        resources.append({"ref": ref, "path": str(path), "content_hash": digest})
        bindings[name] = {"ref": ref, "hash": digest}
    token = secrets.token_urlsafe(32)
    credential_env = "TGW_DEVELOPMENT_RESOURCE_TOKEN"
    client_id = "card-" + card["idempotency_key"][7:31]
    service_id = "development-resources"
    key_id = "development-" + card["idempotency_key"][7:23]
    private_key = Ed25519PrivateKey.generate()
    parsed = ResourceServiceConfig.parse({
        "schema": "tgw-governed-resource-service-config/v5",
        "service_id": service_id,
        "clients": [{
            "id": client_id, "credential_env": credential_env,
            "execution_identity": card["execution_identity"], "role": card["role"],
        }],
        "attestation_key_id": key_id,
        "attestation_private_key_env": "TGW_DEVELOPMENT_ATTESTATION_KEY",
        "harness_run_ttl_seconds": 1800, "completed_run_ttl_seconds": 3600,
        "max_open_runs_per_client": 2, "max_completed_runs_per_client": 4,
        "resources": resources,
    })
    server = create_resource_service_server(
        parsed, {client_id: token}, signing_private_key=private_key,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    descriptor = {
        "schema": "tgw-registered-resource-service/v2", "id": service_id,
        "client_id": client_id, "endpoint": f"http://127.0.0.1:{server.server_port}",
        "credential_env": credential_env, "timeout_seconds": 15,
    }
    catalog = {
        "schema": "tgw-registered-resource-service-catalog/v3",
        "catalog_ref": f"catalog:development-{card['idempotency_key'][7:23]}@1",
        "plan_commit": card["plan"]["commit"],
        "services": [{
            "id": service_id, "client_id": client_id,
            "descriptor_hash": resource_service_descriptor_hash(descriptor),
            "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
            "attestation_key_id": key_id,
            "attestation_public_key": ed25519_public_key(private_key),
        }],
    }
    resolver = HTTPRegisteredResourceResolver.from_descriptor(
        descriptor, environment={credential_env: token},
    )
    template = dict(card["execution_card_template"])
    template["bindings"] = bindings
    template["resource_service"] = {
        "id": service_id, "client_id": client_id,
        "descriptor_hash": resource_service_descriptor_hash(descriptor),
        "catalog_ref": catalog["catalog_ref"],
        "catalog_hash": resource_service_catalog_hash(catalog),
    }
    return server, thread, template, catalog, resolver, {
        credential_env: token, "TGW_ATTEMPT_ROOT": str(root.parent.resolve()),
    }


def execute_development_lifecycle(config: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    """Run each role through launch-time provider selection and exact resources."""
    coding, development = _config(config)
    execution = document.get("execution")
    lifecycle = document.get("lifecycle")
    if (
        not isinstance(execution, Mapping)
        or execution.get("schema") != "tgw-development-execution/v1"
        or not isinstance(lifecycle, Mapping)
        or lifecycle.get("lifecycle_hash") != execution.get("development_request_hash")
    ):
        raise HardFailure("development execution authorization is invalid")
    registry = _load_registry(development, str(execution["provider_registry_hash"]))
    adapters = _adapters(development)
    health = observe_health(registry, coding_config=coding, adapters=adapters)
    repository = Path(str(coding["repository_root"])).resolve()
    source = str(execution["source_commit"])
    candidate_tree = _git(repository, "rev-parse", f"{source}^{{tree}}")
    cards = lifecycle.get("launch_cards")
    if not isinstance(cards, list) or [card.get("idempotency_key") for card in cards] != execution.get("card_idempotency_keys"):
        raise HardFailure("development card closure differs from service authorization")
    wrappers: list[dict[str, Any]] = []
    unit_receipts: list[dict[str, Any]] = []
    current_unit: str | None = None
    expected_role = 0
    for index, card in enumerate(cards, start=1):
        if not isinstance(card, Mapping):
            raise HardFailure("development card is invalid")
        if card.get("unit") != current_unit:
            if current_unit is not None:
                gate = admission_gate(unit_receipts)
                if not gate["allowed"]:
                    raise HardFailure("development unit failed admission: " + ",".join(gate["reasons"]))
            current_unit, expected_role, unit_receipts = str(card.get("unit")), 0, []
        role = str(card.get("role"))
        if expected_role >= len(_ROLE_ORDER) or role != _ROLE_ORDER[expected_role]:
            raise HardFailure("development role sequence is invalid")
        expected_role += 1
        worktree = _card_worktree(repository, card, source, index)
        values = _resource_values(document, card, worktree, source, wrappers, coding)
        attempt_root = Path(str(card["allocation"]["attempt_root"])) / "resources"
        server, thread, template, catalog, resolver, runner_env = _serve_resources(
            card=card, values=values, root=attempt_root,
        )
        try:
            receipt = dispatch_role(
                registry, health, role=role, adapters=adapters,
                card_template=template, execution_identity=str(card["execution_identity"]),
                required_capabilities={
                    "implementation": ["source-mutation"],
                    "controller-verification": ["tests"],
                    "independent-review": ["isolated-snapshot-review"],
                }[role],
                resource_resolver=resolver, resource_service={
                    "schema": "tgw-registered-resource-service/v2",
                    "id": template["resource_service"]["id"],
                    "client_id": template["resource_service"]["client_id"],
                    "endpoint": f"http://127.0.0.1:{server.server_port}",
                    "credential_env": "TGW_DEVELOPMENT_RESOURCE_TOKEN", "timeout_seconds": 15,
                },
                resource_service_catalog=catalog, runner_environment=runner_env,
                runner_cwd=worktree,
            )
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
        wrapper = {
            "idempotency_key": card["idempotency_key"], "unit": card["unit"],
            "role": role, "status": receipt["status"], "receipt": receipt,
        }
        wrappers.append(wrapper)
        unit_receipts.append(receipt)
        if receipt["status"] != "PASS":
            raise HardFailure(f"development {role} role did not pass")
        if role == "implementation":
            source, candidate_tree = _checkpoint(worktree, card)
    if unit_receipts:
        gate = admission_gate(unit_receipts)
        if not gate["allowed"]:
            raise HardFailure("development unit failed admission: " + ",".join(gate["reasons"]))
    unsigned = {
        "schema": "tgw-development-execution-result/v1",
        "development_request_hash": execution["development_request_hash"],
        "source_commit": execution["source_commit"], "outcome": "satisfied",
        "role_receipts": wrappers,
        "candidate": {"commit": source, "tree": candidate_tree},
    }
    return {**unsigned, "result_hash": _hash(unsigned)}

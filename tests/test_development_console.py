import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tgw import coding_provision
from tgw.development_console import resolve_request
from tgw.development_launch import DevelopmentLaunchError, validate_development_launch
from tgw.execution_resources import CARD_RESOURCE_NAMES, issue_harness_retrieval_attestation
from tgw.operator_console import create_operator_console_router, project_request
from tgw.plan_authority import AuthorityPrincipal, PrincipalRole

PLAN = "a" * 40
SOURCE = "b" * 40
CLOSURE_HASH = "sha256:" + "d" * 64
LIVE_REVISIONS = {
    "plan": PLAN,
    "capability_graph": "sha256:" + "1" * 64,
    "code_graph": SOURCE,
    "workflow": "sha256:" + "2" * 64,
    "actor_contract": "sha256:" + "3" * 64,
}
ACTIVE_GATE = {
    "schema": "tgw-w18-fleet-transition-gate/v1",
    "status": "ACTIVE",
}


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def passing_role_receipt(card):
    role = card["role"]
    resource_hash = "sha256:" + "3" * 64
    card_hash = "sha256:" + hashlib.sha256(card["idempotency_key"].encode()).hexdigest()
    handoff_hash = "sha256:" + "4" * 64
    resources = {name: {"ref": f"resource:{name}", "hash": "sha256:" + format(index, "064x")} for index, name in enumerate(sorted(CARD_RESOURCE_NAMES), start=1)}
    attestation = issue_harness_retrieval_attestation(
        {
            "schema": "tgw-registered-resource-retrieval-attestation/v3",
            "service_id": "test-development-service",
            "client_id": "test-development-client",
            "run_id": "test-development-run",
            "card_hash": card_hash,
            "role": role,
            "execution_identity": card["execution_identity"],
            "handoff_hash": handoff_hash,
            "resource_receipt_hash": resource_hash,
            "resources": resources,
            "attestation_key_id": "test-development-key",
        },
        signing_private_key=Ed25519PrivateKey.generate(),
    )
    established = {
        "implementation": ["implemented"],
        "controller-verification": ["controller_verified"],
        "independent-review": ["reviewed"],
    }[role]
    unsigned = {
        "schema": "tgw-governed-coding-receipt/v1",
        "status": "PASS",
        "role": role,
        "selected_provider": "replaceable-runner",
        "execution_identity": card["execution_identity"],
        "card_hash": card_hash,
        "promptcraft_receipt_hash": "sha256:" + "5" * 64,
        "handoff_hash": handoff_hash,
        "resource_receipt_hash": resource_hash,
        "harness_resource_receipt_hash": resource_hash,
        "harness_retrieval_attestation_hash": attestation["attestation_hash"],
        "harness_retrieval_attestation": attestation,
        "resource_service_descriptor_hash": "sha256:" + "6" * 64,
        "resource_service_client_id": "test-development-client",
        "resource_service_catalog_ref": "catalog:test-development@1",
        "resource_service_catalog_hash": "sha256:" + "7" * 64,
        "outcome": "satisfied",
        "established_conditions": established,
        "artifacts": [],
    }
    return {**unsigned, "receipt_hash": digest(unsigned)}


def solution():
    value = {
        "schema": "tgw-plan-solution/v1",
        "plan_commit": PLAN,
        "closure_hash": CLOSURE_HASH,
        "complete": True,
        "conformance_verified": True,
        "dispatchable": True,
        "unresolved": [],
        "root": {"id": "PLAN-SITE", "profile": "reviewed", "minimum_state": "reviewed"},
        "phase_order": [["foundation"], ["site", "mobile"]],
        "selected_providers": ["foundation", "site", "mobile"],
    }
    return {**value, "solution_hash": digest(value)}


def freshness():
    revisions = {
        name: {
            "source": source,
            "materialization": "sha256:" + format(index, "064x"),
            "build": "sha256:" + format(index + 10, "064x"),
            "built_at": "2026-08-20T12:00:00Z",
            "health": "READY",
        }
        for index, (name, source) in enumerate(LIVE_REVISIONS.items(), start=1)
    }
    body = {
        "schema": "tgw-w18-projection-refresh-receipt/v1",
        "status": "FRESH",
        "desired": revisions,
        "observed": json.loads(json.dumps(revisions)),
        "actors": [],
        "refresh": {
            "predecessor": "sha256:" + "4" * 64,
            "successor": "sha256:" + "5" * 64,
            "outcome": "HEALTHY",
        },
        "lease": {"id": "fleet", "generation": 1},
        "reasons": [],
        "activation": "declarative-only",
    }
    return {**body, "receipt_hash": digest(body)}


def registry():
    return {
        "schema": "tgw-harness-provider-registry/v1",
        "id": "providers@1",
        "providers": [
            {
                "id": "replaceable-runner",
                "qualified_roles": ["implementation", "independent-review", "controller-verification"],
            }
        ],
    }


def card_contract():
    role_contracts = {}
    for role in ("implementation", "independent-review", "controller-verification"):
        role_contracts[role] = {
            "execution_identity": f"development-{role}",
            "resource_service": {
                "id": "development-resources",
                "client_id": "client-" + role,
                "descriptor_hash": "sha256:" + "e" * 64,
                "catalog_ref": "catalog:development-resources@1",
                "catalog_hash": "sha256:" + "f" * 64,
            },
        }
    resources = {
        name: {"ref": f"resource:{name}", "hash": "sha256:" + format(index, "064x")}
        for index, name in enumerate(
            sorted(
                {
                    "plan_input",
                    "plan_commit",
                    "plan_graph",
                    "codegraph_snapshot",
                    "source_tree",
                    "execution_environment",
                    "authority_conditions",
                    "candidate_evidence",
                    "receipt_sink",
                }
            ),
            start=1,
        )
    }
    return {
        "schema": "tgw-development-card-contract/v1",
        "plan_commit": PLAN,
        "solution_hash": solution()["solution_hash"],
        "source_commit": SOURCE,
        "provider_registry_hash": digest(registry()),
        "bindings": resources,
        "roles": role_contracts,
    }


def body(**extra):
    return {
        "schema": "tgw-development-console-request/v1",
        "original_request": "Build the site",
        "scope": "site and mobile client",
        "constraints": ["preserve data"],
        "effect_limits": ["source and candidate installation"],
        **extra,
    }


def resolved(**extra):
    return resolve_request(
        body=body(**extra),
        solution=solution(),
        plan_commit=PLAN,
        requested_by="operator:dave",
        source_commit=SOURCE,
        freshness=freshness(),
        provider_registry=registry(),
        card_contract=card_contract(),
        live_revisions=LIVE_REVISIONS,
        recovery_status=ACTIVE_GATE,
    )


def test_resolved_cards_are_dependency_ordered_and_harness_neutral():
    lifecycle, authority = resolved()
    assert authority.effect.kind.value == "development-launch"
    assert lifecycle["resolution"]["root"] == {"kind": "Plan", "id": "PLAN-SITE"}
    assert [card["unit"] for card in lifecycle["launch_cards"][:4]] == [
        "foundation",
        "foundation",
        "foundation",
        "site",
    ]
    roles = {card["role"] for card in lifecycle["launch_cards"]}
    assert roles == {"implementation", "independent-review", "controller-verification"}
    assert all(card["provider_selection"]["selected_provider"] is None for card in lifecycle["launch_cards"])
    serialized = json.dumps(lifecycle).lower()
    assert all(product not in serialized for product in ("codex", "claude", "aider", "hermes"))
    validate_development_launch(authority.effect.parameters)


def test_submission_id_makes_replay_idempotent_and_retry_allocations_unique():
    first, _ = resolved(submission_id="submission-20270101t120000z-0000000000000001")
    replay, _ = resolved(submission_id="submission-20270101t120000z-0000000000000001")
    retry, _ = resolved(submission_id="submission-20270101t120001z-0000000000000002")
    assert first["lifecycle_hash"] == replay["lifecycle_hash"]
    assert first["allocation"] == replay["allocation"]
    assert first["allocation"] != retry["allocation"]
    assert {card["allocation"]["worktree"] for card in first["launch_cards"]}.isdisjoint({card["allocation"]["worktree"] for card in retry["launch_cards"]})


def test_narrow_root_without_an_exact_solution_is_retained_but_not_approvable():
    lifecycle, authority = resolved(root={"kind": "PP", "id": "PP-OTHER"})
    assert lifecycle["resolution"]["status"] == "CLARIFICATION_REQUIRED"
    assert lifecycle["launch_cards"] == []
    projected = project_request(
        {
            "request_id": authority.request_id,
            "effect_kind": "development-launch",
            "effect_parameters": authority.effect.parameters,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    assert projected["status"] == "clarification_required"
    assert projected["legal_actions"] == ["view-evidence"]
    with pytest.raises(DevelopmentLaunchError, match="no resolved launch closure"):
        validate_development_launch(authority.effect.parameters)


def test_provider_selection_or_lifecycle_tamper_fails_closed():
    _, authority = resolved()
    parameters = json.loads(json.dumps(authority.effect.parameters))
    parameters["lifecycle"]["launch_cards"][0]["provider_selection"]["selected_provider"] = "chat-picked-runner"
    with pytest.raises(DevelopmentLaunchError, match="lifecycle hash"):
        validate_development_launch(parameters)


def test_fresh_label_cannot_hide_live_revision_drift_or_active_recovery():
    stale = freshness()
    stale["desired"]["code_graph"]["source"] = "c" * 40
    stale["observed"]["code_graph"]["source"] = "c" * 40
    unsigned = dict(stale)
    unsigned.pop("receipt_hash")
    stale["receipt_hash"] = digest(unsigned)
    with pytest.raises(ValueError, match="not fresh"):
        resolve_request(
            body=body(), solution=solution(), plan_commit=PLAN,
            requested_by="operator:dave", source_commit=SOURCE,
            freshness=stale, provider_registry=registry(),
            card_contract=card_contract(), live_revisions=LIVE_REVISIONS,
            recovery_status=ACTIVE_GATE,
        )

    with pytest.raises(ValueError, match="recovery or fleet transition"):
        resolve_request(
            body=body(), solution=solution(), plan_commit=PLAN,
            requested_by="operator:dave", source_commit=SOURCE,
            freshness=freshness(), provider_registry=registry(),
            card_contract=card_contract(), live_revisions=LIVE_REVISIONS,
            recovery_status={**ACTIVE_GATE, "status": "QUIESCED"},
        )


class Store:
    def __init__(self):
        self.rows = []

    def create_request(self, request):
        row = {
            **request.__dict__,
            "effect_kind": request.effect.kind.value,
            "effect_generation": request.effect.generation,
            "effect_hash": request.effect.effect_hash,
            "effect_parameters": request.effect.parameters,
        }
        self.rows.append(row)
        return row

    def list(self, limit=100):
        return self.rows[:limit]

    def get(self, request_id):
        return next((row for row in self.rows if row["request_id"] == request_id), None)

    def events(self, request_id):
        return []


def test_http_creation_uses_the_same_authority_store_and_projection():
    store = Store()

    def operator():
        return AuthorityPrincipal("operator:dave", PrincipalRole.OPERATOR, "test")

    def executor():
        return AuthorityPrincipal("executor:worker", PrincipalRole.EXECUTOR, "test")

    app = FastAPI()
    app.include_router(
        create_operator_console_router(
            store,
            current_plan_commit=lambda: PLAN,
            load_solution=lambda _: solution(),
            require_operator=operator,
            require_executor=executor,
            resolve_development=lambda request, principal: resolve_request(
                body=request,
                solution=solution(),
                plan_commit=PLAN,
                requested_by=principal,
                source_commit=SOURCE,
                freshness=freshness(),
                provider_registry=registry(),
                card_contract=card_contract(),
                live_revisions=LIVE_REVISIONS,
                recovery_status=ACTIVE_GATE,
            ),
        )
    )
    response = TestClient(app).post("/api/operator-console/development-requests", json=body())
    assert response.status_code == 201
    value = response.json()
    assert value["request"]["effect"]["kind"] == "development-launch"
    assert value["request"]["development"]["request"]["original_request"] == "Build the site"
    assert len(store.rows) == 1


def test_v2_queue_claim_and_completion_are_bound_to_the_harness_neutral_cards(monkeypatch):
    """The service must authorize the Plan lifecycle, not reinterpret it as a Todo."""

    class Queue:
        def __init__(self):
            self.job = None

        def enqueue_job(self, _name, payload, **_kwargs):
            self.job = {"job_id": "development-job", "state": "queued", "payload_json": payload}
            return "development-job"

        def get_job(self, _job_id):
            return dict(self.job)

        def claim_job_with_envelope(self, _job_id, owner, envelope, **_kwargs):
            self.job.update(
                state="leased",
                lease_owner=owner,
                lease_token="development-lease",
                payload_json={**self.job["payload_json"], **envelope},
            )
            return dict(self.job)

        def start_claimed_job(self, _job_id, _owner, _token):
            self.job["state"] = "running"

        def succeed_claimed_job(self, _job_id, _owner, _token, result):
            self.job.update(state="succeeded", result=result)

    queue = Queue()
    monkeypatch.setattr(coding_provision, "state_machine", queue)
    _, authority = resolved()
    config = {
        "coding": {
            "host": "tgw-lib-local",
            "worker_identity": "development-worker",
            "api_endpoint": "https://tgw.example",
            "role": "coding-requester",
        }
    }
    request = coding_provision.create_development_request(
        config,
        launch=authority.effect.parameters,
    )
    first = request["lifecycle"]["launch_cards"][0]
    location = {
        "repository_root": "/opt/TGW/tgw-lib/src/trader-grims-warehouse",
        "worktree": first["allocation"]["worktree"],
        "request_hash": request["development_request_hash"],
        "branch": "development/development-job",
        "head": SOURCE,
        "worker_identity": "development-worker",
    }
    claimed = coding_provision.claim_request(
        config,
        request_id="development-job",
        local_host="tgw-lib-local",
        worker_identity="development-worker",
        envelope_hash=coding_provision._hash(location),
        location=location,
        snapshot=None,
    )
    assert claimed["request"]["execution"]["schema"] == "tgw-development-execution/v1"
    coding_provision.start_request(
        config,
        request_id="development-job",
        worker_identity="development-worker",
        lease_token="development-lease",
    )
    unsigned = {
        "schema": "tgw-development-execution-result/v1",
        "development_request_hash": request["development_request_hash"],
        "source_commit": SOURCE,
        "outcome": "satisfied",
        "role_receipts": [
            {
                "idempotency_key": card["idempotency_key"],
                "unit": card["unit"],
                "role": card["role"],
                "status": "PASS",
                "receipt": passing_role_receipt(card),
            }
            for card in request["lifecycle"]["launch_cards"]
        ],
        "candidate": {"commit": "c" * 40, "tree": "d" * 40},
    }
    result = {**unsigned, "result_hash": "sha256:" + coding_provision._hash(unsigned)}
    completed = coding_provision.complete_request(
        config,
        request_id="development-job",
        worker_identity="development-worker",
        lease_token="development-lease",
        result=result,
    )
    assert completed["state"] == "succeeded"
    assert completed["receipt"]["execution"]["development_request_hash"] == request["development_request_hash"]

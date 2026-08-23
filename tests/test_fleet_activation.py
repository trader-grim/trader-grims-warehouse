import hashlib
import json

import pytest

from tgw.fleet_activation import (
    FleetActivationError,
    _value_hash,
    apply_fleet_configuration,
    rollback_fleet_configuration,
    run_fleet_refresh_transaction,
)


def _hash(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _refresh_request(**updates):
    value = {
        "schema": "tgw-w18-fleet-refresh-request/v1", "transaction_id": "refresh-one",
        "idempotency_key": "plan-source-catalog-one", "predecessor_generation": "sha256:" + "a" * 64,
        "successor_generation": "sha256:" + "b" * 64,
        "revisions": {
            "plan": "f" * 40, "solution": "sha256:" + "1" * 64,
            "evidence_plan": "9" * 40, "evidence_tree": "8" * 40,
            "source": "e" * 40, "source_tree": "d" * 40,
            "current_plan_sources": {
                "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml": (
                    "sha256:" + "5" * 64
                ),
                "pp/PP-ACTOR-MCP-BOUNDARY-001.md": "sha256:" + "6" * 64,
                "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml": (
                    "sha256:" + "7" * 64
                ),
                "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml": (
                    "sha256:" + "8" * 64
                ),
            },
            "catalog": "sha256:" + "c" * 64,
            "bootstrap": "sha256:" + "2" * 64, "broker_policy": "sha256:" + "3" * 64,
            "review": "sha256:" + "5" * 64,
            "admission": "sha256:" + "4" * 64,
        },
        "actors": ["claude", "codex"],
    }
    value.update(updates)
    return value


def _refresh_providers(events, *, fail=None, rollback_fail=False):
    def result(name, status, extra=None):
        def call(*args):
            events.append(name)
            if fail == name:
                raise RuntimeError(f"{name} failed")
            return {"status": status, **(extra or {})}
        return call
    def resume(checkpoint, request):
        events.append("resume")
        if fail == "resume":
            raise RuntimeError("resume failed")
        return {"status": "RESUMED", "dispositions": {
            name: [{"checkpoint_identity": item["checkpoint_identity"], "disposition": "successor"}
                   for item in checkpoint[name]]
            for name in ("live_requests", "role_leases", "rendered_surfaces", "continuations")
        }}

    def verify_actor(actor, request):
        events.append(f"verify:{actor}")
        if fail == "verify-actor":
            raise RuntimeError("verify-actor failed")
        return {
            "status": "VERIFIED", "actor": actor,
            "generation": request["successor_generation"],
        }

    return {
        "checkpoint": result("checkpoint", "CHECKPOINTED", {"live_requests": [], "role_leases": [], "rendered_surfaces": [], "continuations": []}),
        "quiesce": result("quiesce", "QUIESCED"), "rebuild": result("rebuild", "REBUILT"),
        "activate": result("activate", "ACTIVATED"), "restart": result("restart", "RESTARTED"),
        "health": result("health", "HEALTHY"),
        "verify_actor": verify_actor,
        "resume": resume,
        "rollback": result("rollback", "FAILED" if rollback_fail else "ROLLED_BACK"),
    }


def test_refresh_transaction_orders_full_fleet_and_is_idempotent(durable_path):
    events = []
    request = _refresh_request()
    providers = _refresh_providers(events)
    receipt = run_fleet_refresh_transaction(
        request, receipt_root=durable_path / "receipts", lease_path=durable_path / "fleet.lock", **providers,
    )
    assert receipt["status"] == "VERIFIED_AND_RESUMED"
    assert events == ["checkpoint", "quiesce", "rebuild", "activate", "restart", "health", "verify:claude", "verify:codex", "resume"]
    again = run_fleet_refresh_transaction(
        request, receipt_root=durable_path / "receipts", lease_path=durable_path / "fleet.lock", **providers,
    )
    assert again == receipt
    assert events.count("checkpoint") == 1


def test_incomplete_journal_resumes_from_last_durable_successful_step(durable_path):
    request = _refresh_request()
    root = durable_path / "receipts"
    root.mkdir()
    journal_path = root / f"{request['transaction_id']}.journal.json"
    checkpoint = {
        "status": "CHECKPOINTED",
        "live_requests": [],
        "role_leases": [],
        "rendered_surfaces": [],
        "continuations": [],
    }
    journal = {
        "schema": "tgw-w18-fleet-refresh-journal/v1",
        "request_hash": _value_hash(request),
        "request": request,
        "status": "QUIESCED",
        "steps": [
            {"name": "checkpoint", "receipt": checkpoint},
            {"name": "quiesce", "receipt": {"status": "QUIESCED"}},
        ],
        "actor_receipts": {},
    }
    journal_path.write_text(json.dumps(journal, sort_keys=True) + "\n")
    events = []

    receipt = run_fleet_refresh_transaction(
        request,
        receipt_root=root,
        lease_path=durable_path / "fleet.lock",
        **_refresh_providers(events),
    )

    assert receipt["status"] == "VERIFIED_AND_RESUMED"
    assert events == [
        "rebuild", "activate", "restart", "health",
        "verify:claude", "verify:codex", "resume",
    ]
    completed = json.loads(journal_path.read_text())
    assert completed["status"] == "VERIFIED_AND_RESUMED"
    assert completed["terminal_receipt_hash"] == receipt["receipt_hash"]


def test_refresh_failure_rolls_whole_fleet_back_and_resumes_predecessor(durable_path):
    events = []
    receipt = run_fleet_refresh_transaction(
        _refresh_request(), receipt_root=durable_path / "receipts", lease_path=durable_path / "fleet.lock",
        **_refresh_providers(events, fail="restart"),
    )
    assert receipt["status"] == "FAILED_ROLLED_BACK"
    assert events[-2:] == ["rollback", "resume"]
    assert receipt["failure"] == "restart failed"


def test_refresh_rollback_failure_is_retryable_from_durable_rollback_required(
    durable_path,
):
    request = _refresh_request()
    receipt_root = durable_path / "receipts"
    receipt = run_fleet_refresh_transaction(
        request, receipt_root=receipt_root, lease_path=durable_path / "fleet.lock",
        **_refresh_providers([], fail="health", rollback_fail=True),
    )
    assert receipt["status"] == "ROLLBACK_REQUIRED"
    assert receipt["lifecycle"] == "HOLD_QUIESCED_RETRYABLE"
    journal_path = receipt_root / f"{request['transaction_id']}.journal.json"
    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "ROLLBACK_REQUIRED"
    assert journal["rollback_errors"] == ["fleet rollback did not report ROLLED_BACK"]

    retry_events = []
    recovered = run_fleet_refresh_transaction(
        request,
        receipt_root=receipt_root,
        lease_path=durable_path / "fleet.lock",
        **_refresh_providers(retry_events),
    )
    assert recovered["status"] == "FAILED_ROLLED_BACK"
    assert retry_events == ["rollback", "resume"]
    assert json.loads(journal_path.read_text())["status"] == "FAILED_ROLLED_BACK"


@pytest.mark.parametrize(
    "boundary",
    [
        "checkpoint", "quiesce", "rebuild", "activate", "restart", "health",
        "verify-actor", "resume",
    ],
)
def test_every_refresh_boundary_failure_is_terminal_and_never_claims_success(
    durable_path, boundary,
):
    events = []
    request = _refresh_request(
        transaction_id=f"refresh-{boundary}",
        idempotency_key=f"failure-{boundary}",
    )
    receipt = run_fleet_refresh_transaction(
        request,
        receipt_root=durable_path / "receipts",
        lease_path=durable_path / "fleet.lock",
        **_refresh_providers(events, fail=boundary),
    )

    expected = "ROLLBACK_REQUIRED" if boundary == "resume" else "FAILED_ROLLED_BACK"
    assert receipt["status"] == expected
    journal = json.loads(
        (durable_path / "receipts" / f"refresh-{boundary}.journal.json").read_text()
    )
    assert journal["status"] == receipt["status"]
    if boundary == "resume":
        assert receipt["lifecycle"] == "HOLD_QUIESCED_RETRYABLE"
        assert "terminal_receipt_hash" not in journal
    else:
        assert journal["terminal_receipt_hash"] == receipt["receipt_hash"]


def test_refresh_refuses_tmp_receipts_and_incomplete_checkpoint(durable_path):
    with pytest.raises(FleetActivationError, match="outside /tmp"):
        run_fleet_refresh_transaction(
            _refresh_request(), receipt_root="/tmp/tgw", lease_path=durable_path / "fleet.lock",
            **_refresh_providers([]),
        )
    providers = _refresh_providers([])
    providers["checkpoint"] = lambda _: {"status": "CHECKPOINTED"}
    receipt = run_fleet_refresh_transaction(
        _refresh_request(idempotency_key="incomplete"), receipt_root=durable_path / "receipts",
        lease_path=durable_path / "fleet.lock", **providers,
    )
    assert receipt["status"] == "FAILED_ROLLED_BACK"
    assert "omits live lifecycle state" in receipt["failure"]


def test_refresh_requires_exact_successor_disposition_for_every_checkpoint_object(durable_path):
    events = []
    providers = _refresh_providers(events)
    identity = "sha256:" + "1" * 64
    providers["checkpoint"] = lambda _: {
        "status": "CHECKPOINTED",
        "live_requests": [{"checkpoint_identity": identity, "request_id": "request-one"}],
        "role_leases": [], "rendered_surfaces": [], "continuations": [],
    }
    good_resume = providers["resume"]
    providers["resume"] = lambda checkpoint, request: (
        {"status": "RESUMED", "dispositions": {
            "live_requests": [], "role_leases": [], "rendered_surfaces": [], "continuations": [],
        }} if request["successor_generation"] == "sha256:" + "b" * 64
        else good_resume(checkpoint, request)
    )
    receipt = run_fleet_refresh_transaction(
        _refresh_request(idempotency_key="missing-disposition"),
        receipt_root=durable_path / "receipts", lease_path=durable_path / "fleet.lock", **providers,
    )
    assert receipt["status"] == "FAILED_ROLLED_BACK"
    assert "does not cover every live_requests checkpoint" in receipt["failure"]


def test_configuration_and_materialization_are_receipt_backed_and_rollbackable(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"old-one")
    second.write_bytes(b"old-two")
    applied, rolled_back = [], []
    receipt = apply_fleet_configuration(
        {first: {"expected_sha256": _hash(b"old-one"), "desired": b"new-one"}, second: {"expected_sha256": _hash(b"old-two"), "desired": b"new-two"}},
        materialize=lambda: applied.append(True) or {"schema": "materialization", "status": "MATERIALIZED_NOT_ACTIVATED", "rollback_journal": []},
        rollback_materialization=lambda value: rolled_back.append(value),
    )
    assert receipt["status"] == "CONFIGURED_MATERIALIZED_NOT_SERVICE_ACTIVATED"
    assert first.read_bytes() == b"new-one" and second.read_bytes() == b"new-two"
    rollback_fleet_configuration(receipt, rollback_materialization=lambda value: rolled_back.append(value))
    assert first.read_bytes() == b"old-one" and second.read_bytes() == b"old-two"
    assert applied and rolled_back


def test_changed_preimage_fails_closed_without_writes(tmp_path):
    config = tmp_path / "config"
    config.write_bytes(b"actual")
    with pytest.raises(FleetActivationError, match="preimage changed"):
        apply_fleet_configuration({config: {"expected_sha256": _hash(b"forged"), "desired": b"new"}}, materialize=lambda: {}, rollback_materialization=lambda _: None)
    assert config.read_bytes() == b"actual"
    assert not list(tmp_path.glob("*.tgw-w18-next"))


def test_materialization_failure_restores_configurations(tmp_path):
    config = tmp_path / "config"
    config.write_bytes(b"old")
    with pytest.raises(RuntimeError, match="stop"):
        apply_fleet_configuration(
            {config: {"expected_sha256": _hash(b"old"), "desired": b"new"}},
            materialize=lambda: (_ for _ in ()).throw(RuntimeError("stop")),
            rollback_materialization=lambda _: None,
        )
    assert config.read_bytes() == b"old"


def test_rollback_callback_failure_still_restores_configurations(tmp_path):
    config = tmp_path / "config"
    config.write_bytes(b"old")
    with pytest.raises(FleetActivationError, match="rollback failed"):
        apply_fleet_configuration(
            {config: {"expected_sha256": _hash(b"old"), "desired": b"new"}},
            materialize=lambda: {"status": "wrong"},
            rollback_materialization=lambda _: (_ for _ in ()).throw(RuntimeError("rollback")),
        )
    assert config.read_bytes() == b"old"


def test_explicit_rollback_callback_failure_still_restores_configurations(tmp_path):
    config = tmp_path / "config"
    config.write_bytes(b"old")
    receipt = apply_fleet_configuration(
        {config: {"expected_sha256": _hash(b"old"), "desired": b"new"}},
        materialize=lambda: {"status": "MATERIALIZED_NOT_ACTIVATED", "rollback_journal": []},
        rollback_materialization=lambda _: None,
    )
    with pytest.raises(FleetActivationError, match="materialization rollback failed"):
        rollback_fleet_configuration(
            receipt,
            rollback_materialization=lambda _: (_ for _ in ()).throw(RuntimeError("rollback")),
        )
    assert config.read_bytes() == b"old"


def test_failed_ownership_preservation_cleans_staging(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_bytes(b"old")
    monkeypatch.setattr("tgw.fleet_activation.os.chown", lambda *_: (_ for _ in ()).throw(PermissionError("no chown")))
    with pytest.raises(PermissionError, match="no chown"):
        apply_fleet_configuration(
            {config: {"expected_sha256": _hash(b"old"), "desired": b"new"}},
            materialize=lambda: {"status": "MATERIALIZED_NOT_ACTIVATED"},
            rollback_materialization=lambda _: None,
        )
    assert config.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.tgw-w18-next"))

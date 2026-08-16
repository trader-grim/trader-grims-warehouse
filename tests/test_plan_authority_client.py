import json
from unittest.mock import patch

import pytest

from tgw.authority_notifications import notify_authority_status
from tgw.plan_authority_client import PlanAuthorityClientError, PlanAuthorityHttpClient, cmd_plan_authority


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def test_operator_client_uses_shared_http_records_and_never_exposes_consume():
    client = PlanAuthorityHttpClient("https://authority.example", "operator-token")
    with patch("tgw.plan_authority_client.urlopen", return_value=_Response({"requests": []})) as request:
        assert client.list_requests(limit=7) == {"requests": []}
    outbound = request.call_args.args[0]
    assert outbound.full_url == "https://authority.example/api/plan-authority/requests?limit=7"
    assert outbound.get_header("Authorization") == "Bearer operator-token"
    assert not hasattr(client, "consume")


def test_operator_decision_is_explicit_and_request_id_is_path_escaped():
    client = PlanAuthorityHttpClient("https://authority.example", "operator-token")
    with patch("tgw.plan_authority_client.urlopen", return_value=_Response({"decision_id": "d"})) as request:
        assert client.decide("request/with space", kind="hold", reason="needs reconciliation") == {"decision_id": "d"}
    outbound = request.call_args.args[0]
    assert outbound.full_url.endswith("/requests/request%2Fwith%20space/decisions")
    assert json.loads(outbound.data) == {"kind": "hold", "reason": "needs reconciliation"}


def test_operator_client_can_submit_evidence_for_active_execution_reconciliation():
    client = PlanAuthorityHttpClient("https://authority.example", "operator-token")
    with patch("tgw.plan_authority_client.urlopen", return_value=_Response({"decision_id": "d"})) as request:
        assert client.decide(
            "request:active", kind="reconcile", reason="executor stopped",
            reconciliation_evidence=["worker:exit-137", "provider:outcome-unknown"],
        ) == {"decision_id": "d"}
    outbound = request.call_args.args[0]
    assert json.loads(outbound.data) == {
        "kind": "reconcile", "reason": "executor stopped",
        "reconciliation_evidence": ["worker:exit-137", "provider:outcome-unknown"],
    }


@pytest.mark.parametrize("endpoint", ["", "ftp://authority.example", "authority.example"])
def test_operator_client_rejects_non_http_endpoint(endpoint):
    with pytest.raises(ValueError, match="HTTP"):
        PlanAuthorityHttpClient(endpoint, "operator-token")


def test_operator_client_surfaces_transport_error():
    client = PlanAuthorityHttpClient("https://authority.example", "operator-token")
    with patch("tgw.plan_authority_client.urlopen", side_effect=OSError("offline")):
        with pytest.raises(PlanAuthorityClientError, match="failed"):
            client.list_requests()


def test_recovery_cli_uses_the_same_client_and_has_no_local_authority_store(monkeypatch):
    class Client:
        @classmethod
        def from_environment(cls):
            return cls()

        def get_request(self, request_id):
            return {"request": {"request_id": request_id, "status": "pending"}}

    monkeypatch.setattr("tgw.plan_authority_client.PlanAuthorityHttpClient", Client)
    assert cmd_plan_authority("show", request_id="request:recovery") == {
        "ok": True,
        "authority": {"request": {"request_id": "request:recovery", "status": "pending"}},
    }


def test_notification_adapter_can_only_project_shared_authority_status():
    delivered = []

    class Client:
        def get_request(self, request_id):
            return {"request": {"request_id": request_id, "status": "pending", "effect": {"kind": "authority-canary"}}}

    record = notify_authority_status(
        Client(), request_id="request:notification",
        deliver=lambda title, message, level: delivered.append((title, message, level)),
    )
    assert record["request_id"] == "request:notification"
    assert delivered[0][0] == "PlanAuthority status"
    assert "decide or reconcile" in delivered[0][1]


def test_recovery_notification_reads_the_shared_record_and_has_no_mutation_path(monkeypatch):
    delivered = []

    class Client:
        @classmethod
        def from_environment(cls):
            return cls()

        def get_request(self, request_id):
            return {"request": {"request_id": request_id, "status": "pending", "effect": {"kind": "authority-canary"}}}

    monkeypatch.setattr("tgw.plan_authority_client.PlanAuthorityHttpClient", Client)
    monkeypatch.setattr("tgw.notify.notify", lambda title, message, level: delivered.append((title, message, level)))
    assert cmd_plan_authority("notify", request_id="request:notification") == {
        "ok": True,
        "authority": {"request": {"request_id": "request:notification", "status": "pending", "effect": {"kind": "authority-canary"}}},
    }
    assert delivered and delivered[0][0] == "PlanAuthority status"
    assert not hasattr(Client(), "decide")

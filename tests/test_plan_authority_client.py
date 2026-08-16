import json
from unittest.mock import patch

import pytest

from tgw.plan_authority_client import PlanAuthorityClientError, PlanAuthorityHttpClient


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


@pytest.mark.parametrize("endpoint", ["", "ftp://authority.example", "authority.example"])
def test_operator_client_rejects_non_http_endpoint(endpoint):
    with pytest.raises(ValueError, match="HTTP"):
        PlanAuthorityHttpClient(endpoint, "operator-token")


def test_operator_client_surfaces_transport_error():
    client = PlanAuthorityHttpClient("https://authority.example", "operator-token")
    with patch("tgw.plan_authority_client.urlopen", side_effect=OSError("offline")):
        with pytest.raises(PlanAuthorityClientError, match="failed"):
            client.list_requests()

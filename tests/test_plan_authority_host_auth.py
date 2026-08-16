"""HTTP host principal binding checks; no real credentials or deployment."""

from fastapi import HTTPException
from starlette.requests import Request


def _request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/plan-authority/requests/request:fixture/consume",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    })


def test_http_host_derives_configured_named_principals_and_rejects_spoofing(monkeypatch):
    from tgw import http_server

    monkeypatch.setattr(http_server, "_cfg", {
        "plan_authority_executor_credential_env": "TGW_TEST_AUTHORITY_EXECUTOR_TOKEN",
        "plan_authority_executor_principal": "executor:fixture-runner",
        "plan_authority_operator_api_principal": "operator:fixture-alice",
    })
    monkeypatch.setenv("TGW_TEST_AUTHORITY_EXECUTOR_TOKEN", "ephemeral-test-token")
    monkeypatch.setattr(http_server, "_require_auth", lambda *_: "operator:api-key")

    operator = http_server._require_plan_operator(None, None)
    assert operator.identity == "operator:fixture-alice"
    assert operator.authentication_binding == "api-key"

    executor = http_server._require_plan_executor(_request({
        "X-TGW-Executor-Authorization": "Bearer ephemeral-test-token",
    }))
    assert executor.identity == "executor:fixture-runner"
    assert executor.authentication_binding == "credential-env:TGW_TEST_AUTHORITY_EXECUTOR_TOKEN"

    try:
        http_server._require_plan_executor(_request({
            "X-TGW-Executor-Authorization": "Bearer ephemeral-test-token",
            "X-TGW-Executor-Identity": "executor:attacker",
        }))
    except HTTPException as exc:
        assert exc.status_code == 401
        assert "client-supplied" in str(exc.detail)
    else:  # pragma: no cover - security assertion
        raise AssertionError("client supplied executor identity was accepted")


def test_http_host_fails_closed_when_named_principal_is_unconfigured(monkeypatch):
    from tgw import http_server

    monkeypatch.setattr(http_server, "_cfg", {
        "plan_authority_executor_credential_env": "TGW_TEST_AUTHORITY_EXECUTOR_TOKEN",
    })
    monkeypatch.setenv("TGW_TEST_AUTHORITY_EXECUTOR_TOKEN", "ephemeral-test-token")
    try:
        http_server._require_plan_executor(_request({
            "X-TGW-Executor-Authorization": "Bearer ephemeral-test-token",
        }))
    except HTTPException as exc:
        assert exc.status_code == 401
        assert "not configured" in str(exc.detail)
    else:  # pragma: no cover - security assertion
        raise AssertionError("unconfigured executor principal was accepted")

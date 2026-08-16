"""HTTP client for the one canonical PlanAuthority record service.

Operator surfaces may create, inspect, and decide a request through this
client.  Effect redemption is deliberately absent: ``/consume`` is restricted
to the separately authenticated registered executor in the authority host.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class PlanAuthorityClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlanAuthorityHttpClient:
    endpoint: str
    bearer_token: str
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.endpoint.rstrip("/").startswith(("http://", "https://")):
            raise ValueError("PlanAuthority endpoint must be an absolute HTTP(S) URL")
        if not self.bearer_token:
            raise ValueError("PlanAuthority bearer token is required")

    @classmethod
    def from_environment(cls) -> "PlanAuthorityHttpClient":
        endpoint = os.environ.get("TGW_AUTHORITY_URL", "").rstrip("/")
        token = os.environ.get("TGW_AUTHORITY_BEARER_TOKEN", "")
        if not endpoint.startswith(("http://", "https://")):
            raise PlanAuthorityClientError("TGW_AUTHORITY_URL must be an absolute HTTP(S) URL")
        if not token:
            raise PlanAuthorityClientError("TGW_AUTHORITY_BEARER_TOKEN is required")
        return cls(endpoint=endpoint, bearer_token=token)

    def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode() if body is not None else None
        request = Request(
            self.endpoint + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.bearer_token}",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310 -- configured operator endpoint
                decoded = json.loads(response.read().decode())
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise PlanAuthorityClientError(f"PlanAuthority HTTP {exc.code}: {detail}") from exc
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise PlanAuthorityClientError(f"PlanAuthority request failed: {exc}") from exc
        if not isinstance(decoded, dict):
            raise PlanAuthorityClientError("PlanAuthority response is not an object")
        return decoded

    def list_requests(self, *, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 1000:
            raise ValueError("authority request limit must be between 1 and 1000")
        return self._request("GET", f"/api/plan-authority/requests?limit={limit}")

    def get_request(self, request_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/plan-authority/requests/{quote(request_id, safe='')}")

    def create_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/plan-authority/requests", request)

    def decide(
        self,
        request_id: str,
        *,
        kind: str,
        reason: str,
        reconciliation_evidence: Sequence[str] = (),
    ) -> dict[str, Any]:
        if kind not in {"approve", "hold", "reconcile"}:
            raise ValueError("authority decision kind is invalid")
        if not reason.strip():
            raise ValueError("authority decision reason is required")
        if (
            isinstance(reconciliation_evidence, (str, bytes))
            or not all(isinstance(item, str) and item for item in reconciliation_evidence)
        ):
            raise ValueError("reconciliation evidence must be a sequence of non-empty identities")
        payload: dict[str, Any] = {"kind": kind, "reason": reason}
        if reconciliation_evidence:
            payload["reconciliation_evidence"] = list(reconciliation_evidence)
        return self._request(
            "POST", f"/api/plan-authority/requests/{quote(request_id, safe='')}/decisions",
            payload,
        )


def cmd_plan_authority(
    action: str,
    *,
    request_id: str | None = None,
    kind: str | None = None,
    reason: str | None = None,
    reconciliation_evidence: Sequence[str] = (),
    limit: int = 100,
    endpoint: str | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    """Recovery CLI projection over the shared HTTP authority records only."""
    try:
        if endpoint is not None or bearer_token is not None:
            if not endpoint or not bearer_token:
                return {"ok": False, "error": "PlanAuthority endpoint and bearer token must be supplied together"}
            client = PlanAuthorityHttpClient(endpoint.rstrip("/"), bearer_token)
        else:
            client = PlanAuthorityHttpClient.from_environment()
    except (PlanAuthorityClientError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        if action == "list":
            response = client.list_requests(limit=limit)
        elif action == "show":
            if not request_id:
                return {"ok": False, "error": "show requires --request-id"}
            response = client.get_request(request_id)
        elif action == "decide":
            if not request_id or not kind or not reason:
                return {"ok": False, "error": "decide requires --request-id, --kind and --reason"}
            response = client.decide(
                request_id, kind=kind, reason=reason,
                reconciliation_evidence=reconciliation_evidence,
            )
        elif action == "notify":
            if not request_id:
                return {"ok": False, "error": "notify requires --request-id"}
            # Delivery is deliberately a read-only projection: it first reads
            # the canonical HTTP record and then invokes the ordinary TGW
            # notifier.  It has neither a decision nor an execution surface.
            from tgw.authority_notifications import notify_authority_status
            from tgw.notify import notify

            response = {"request": notify_authority_status(
                client, request_id=request_id, deliver=notify,
            )}
        else:
            return {"ok": False, "error": f"unknown PlanAuthority action: {action}"}
    except (PlanAuthorityClientError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "authority": response}

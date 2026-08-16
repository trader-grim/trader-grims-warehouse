"""Read-only PlanAuthority notification adapter.

Notifications may project exact authority state for an operator, but they
cannot decide or consume an effect.  This keeps delivery channels out of the
authority path while ensuring their displayed state comes from the one shared
HTTP record service.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from tgw.plan_authority_client import PlanAuthorityHttpClient


def notify_authority_status(
    client: PlanAuthorityHttpClient,
    *,
    request_id: str,
    deliver: Callable[[str, str, str], None],
) -> Mapping[str, Any]:
    """Fetch an authority record and publish a non-actionable status notice."""
    record = client.get_request(request_id)
    request = record.get("request")
    if not isinstance(request, Mapping):
        raise ValueError("PlanAuthority response has no request projection")
    status = str(request.get("status", "unknown"))
    effect = str(request.get("effect", {}).get("kind", "unknown")) if isinstance(request.get("effect"), Mapping) else "unknown"
    deliver(
        "PlanAuthority status",
        f"{request_id}: {status} ({effect}). Open the PlanAuthority surface to decide or reconcile.",
        "info" if status in {"pending", "approve"} else "warning",
    )
    return request

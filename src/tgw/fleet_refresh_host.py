"""Configuration-bound W18 fleet refresh controller entry point."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tgw.config import DEFAULT_CONFIG, load_operational_config
from tgw.fleet_activation import FleetActivationError, run_fleet_refresh_transaction

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


class FleetRefreshHostError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _directory(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise FleetRefreshHostError(f"{label} is invalid")
    path = Path(value)
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents:
        raise FleetRefreshHostError(f"{label} must be durable and outside /tmp")
    if not path.is_dir() or path.is_symlink():
        raise FleetRefreshHostError(f"{label} is unavailable")
    return path


class _FleetProvider:
    _EXPECTED = {
        "checkpoint": "CHECKPOINTED", "quiesce": "QUIESCED", "rebuild": "REBUILT",
        "activate": "ACTIVATED", "restart": "RESTARTED", "health": "HEALTHY",
        "verify-actor": "VERIFIED", "resume": "RESUMED", "rollback": "ROLLED_BACK",
    }

    def __init__(self, value: Mapping[str, Any]):
        required = {"schema", "provider_id", "endpoint", "credential_env", "timeout_seconds"}
        endpoint = value.get("endpoint") if isinstance(value, Mapping) else None
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        local_http = parsed and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
        if (
            not isinstance(value, Mapping) or set(value) != required
            or value.get("schema") != "tgw-fleet-refresh-provider-binding/v1"
            or value.get("provider_id") != "tgw-fleet-refresh-provider@1"
            or not parsed or (parsed.scheme != "https" and not local_http) or not parsed.netloc
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not isinstance(value.get("credential_env"), str) or _ENV.fullmatch(value["credential_env"]) is None
            or isinstance(value.get("timeout_seconds"), bool) or not isinstance(value.get("timeout_seconds"), int)
            or not 1 <= value["timeout_seconds"] <= 120
        ):
            raise FleetRefreshHostError("fleet refresh provider binding is invalid")
        self.endpoint = endpoint.rstrip("/")
        self.credential_env = value["credential_env"]
        self.timeout = value["timeout_seconds"]

    def call(self, step: str, arguments: list[Any]) -> Mapping[str, Any]:
        expected = self._EXPECTED.get(step)
        if expected is None:
            raise FleetRefreshHostError("fleet provider step is not allowlisted")
        credential = os.environ.get(self.credential_env)
        if not credential:
            raise FleetRefreshHostError("fleet provider credential is unavailable")
        invocation = {"schema": "tgw-fleet-refresh-provider-invocation/v1", "step": step, "arguments": arguments}
        invocation_hash = _hash(invocation)
        request = Request(
            f"{self.endpoint}/v1/fleet-refresh/{step}", data=_canonical({**invocation, "invocation_hash": invocation_hash}),
            headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.timeout) as response:  # nosec: fixed provider binding
                raw = response.read(1024 * 1024 + 1)
                if response.status != 200 or len(raw) > 1024 * 1024:
                    raise FleetRefreshHostError("fleet provider returned an invalid response")
        except (HTTPError, URLError, OSError) as exc:
            raise FleetRefreshHostError("fleet provider is unavailable") from exc
        try:
            result = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise FleetRefreshHostError("fleet provider response is invalid") from exc
        if (
            not isinstance(result, Mapping)
            or set(result) != {"schema", "provider_id", "step", "invocation_hash", "result"}
            or result.get("schema") != "tgw-fleet-refresh-provider-response/v1"
            or result.get("provider_id") != "tgw-fleet-refresh-provider@1"
            or result.get("step") != step or result.get("invocation_hash") != invocation_hash
            or not isinstance(result.get("result"), Mapping)
            or result["result"].get("status") not in {
                expected, "FAILED",
                *( {"RESTART_REQUIRED"} if step in {"restart", "rollback"} else set() ),
            }
        ):
            raise FleetRefreshHostError("fleet provider response is invalid")
        return dict(result["result"])


def run_configured_fleet_refresh(config: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    if not isinstance(request_id, str) or _ID.fullmatch(request_id) is None:
        raise FleetRefreshHostError("fleet refresh request identity is invalid")
    raw = config.get("fleet_refresh")
    required = {"schema", "request_root", "receipt_root", "lease_path", "provider"}
    if not isinstance(raw, Mapping) or set(raw) != required or raw.get("schema") != "tgw-fleet-refresh-host/v1":
        raise FleetRefreshHostError("fleet refresh configuration is invalid")
    request_root = _directory(raw["request_root"], "fleet refresh request root")
    receipt_root = _directory(raw["receipt_root"], "fleet refresh receipt root")
    lease_path = Path(raw["lease_path"]) if isinstance(raw["lease_path"], str) else Path()
    if not lease_path.is_absolute() or lease_path == Path("/tmp") or Path("/tmp") in lease_path.parents:
        raise FleetRefreshHostError("fleet refresh lease path must be durable and outside /tmp")
    request_path = request_root / f"{request_id}.json"
    if request_path.is_symlink() or not request_path.is_file():
        raise FleetRefreshHostError("fleet refresh request is unavailable")
    try:
        value = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetRefreshHostError("fleet refresh request is invalid") from exc
    if not isinstance(value, Mapping) or value.get("transaction_id") != request_id:
        raise FleetRefreshHostError("fleet refresh request identity mismatch")
    provider = _FleetProvider(raw["provider"])

    def invoke(step: str):
        return lambda *arguments: provider.call(step, [dict(arg) if isinstance(arg, Mapping) else arg for arg in arguments])

    def verify_actor(actor: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = provider.call("verify-actor", [actor, dict(request)])
        if result.get("actor") != actor or result.get("generation") != request.get("successor_generation"):
            raise FleetRefreshHostError("fleet actor verification response mismatch")
        return result

    return run_fleet_refresh_transaction(
        value, receipt_root=receipt_root, lease_path=lease_path,
        checkpoint=invoke("checkpoint"), quiesce=invoke("quiesce"), rebuild=invoke("rebuild"),
        activate=invoke("activate"), restart=invoke("restart"), health=invoke("health"),
        verify_actor=verify_actor, resume=invoke("resume"), rollback=invoke("rollback"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-fleet-refresh-controller")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    try:
        receipt = run_configured_fleet_refresh(load_operational_config(args.config), args.request_id)
    except (OSError, ValueError, FleetActivationError) as exc:
        print(json.dumps({"schema": "tgw-fleet-refresh-controller-result/v1", "status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "VERIFIED_AND_RESUMED" else 3


if __name__ == "__main__":
    raise SystemExit(main())

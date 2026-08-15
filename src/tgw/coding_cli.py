"""HTTP client for receipt-addressed coding provision requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class CodingCLIError(RuntimeError):
    pass


def _call(endpoint: str, api_key: str, path: str, method: str = "GET", body: dict | None = None) -> dict:
    if not endpoint.startswith(("http://", "https://")) or not api_key:
        raise CodingCLIError("configured TGW client endpoint and credential are required")
    payload = None if body is None else json.dumps(body).encode()
    request = Request(endpoint.rstrip("/") + path, data=payload, method=method)
    request.add_header("Authorization", f"Bearer {api_key}")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=15) as response:  # nosec: endpoint is explicit operator input
            result = json.loads(response.read().decode())
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise CodingCLIError(str(exc)) from exc
    if not isinstance(result, dict):
        raise CodingCLIError("server returned a non-object response")
    return result


def _configured_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """Use the ordinary TGW client credentials, with explicit overrides.

    The coding client never discovers a provider or obtains credentials; it
    uses the endpoint/credential already supplied by the validated TGW config.
    """
    from tgw.config import DEFAULT_CONFIG, load_config

    cfg = load_config(Path(getattr(args, "config", None) or DEFAULT_CONFIG))
    coding = cfg.get("coding")
    endpoint = getattr(args, "endpoint", None) or (coding.get("api_endpoint") if isinstance(coding, dict) else None)
    api_key = getattr(args, "api_key", None) or cfg.get("api_key")
    if not isinstance(endpoint, str) or not isinstance(api_key, str):
        raise CodingCLIError("configured TGW client endpoint or credential is unavailable")
    return endpoint, api_key


def run(args: argparse.Namespace) -> int:
    try:
        endpoint, api_key = _configured_credentials(args)
        if args.coding_op == "start":
            body = {"todo_id": args.todo_id, "object_generation": args.object_generation}
            if getattr(args, "source_commit", None):
                body["source_commit"] = args.source_commit
            result = _call(endpoint, api_key, "/api/coding/requests", "POST", body)
        elif args.coding_op == "status":
            result = _call(endpoint, api_key, f"/api/coding/requests/{args.request_id}")
        elif args.coding_op == "log":
            result = _call(endpoint, api_key, f"/api/coding/requests/{args.request_id}")
            result = result.get("receipt") or {"receipt_source": "unknown"}
        elif args.coding_op == "stop":
            result = _call(endpoint, api_key, f"/api/coding/requests/{args.request_id}/stop", "POST")
        else:
            path = "/api/coding/access-status"
            if args.request_id:
                path = f"{path}?{urlencode({'request_id': args.request_id})}"
            result = _call(endpoint, api_key, path)
        print(json.dumps(result, sort_keys=True))
        return 0
    except CodingCLIError as exc:
        print(f"tgw coding: {exc}", file=__import__("sys").stderr)
        return 1

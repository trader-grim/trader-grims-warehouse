"""Standalone host for the separately authenticated W17 recovery console.

The normal operator console is deliberately not imported.  The process reads
only exact recovery cards and sends a validated platform-only invocation to one
fixed provider endpoint; neither the card nor the browser can select a URL,
command, path, service, or handler.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import uvicorn
from fastapi import FastAPI

from tgw.config import DEFAULT_CONFIG, load_operational_config
from tgw.recovery_console import RecoveryConsoleMount, create_recovery_console_router

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


class RecoveryHostError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _durable_directory(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise RecoveryHostError(f"{label} is invalid")
    path = Path(value)
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents:
        raise RecoveryHostError(f"{label} must be durable and outside /tmp")
    if not path.is_dir() or path.is_symlink():
        raise RecoveryHostError(f"{label} is unavailable")
    return path


def _persist_exclusive(root: Path, name: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
    path = root / name
    encoded = _canonical(value) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    except FileExistsError as exc:
        raise RecoveryHostError("recovery record already exists") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"path": str(path), "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest()}


class _ConfiguredRecoveryProvider:
    def __init__(self, value: Mapping[str, Any]):
        required = {"schema", "provider_id", "endpoint", "credential_env", "timeout_seconds"}
        if not isinstance(value, Mapping) or set(value) != required:
            raise RecoveryHostError("recovery provider binding is invalid")
        endpoint = value.get("endpoint")
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        local_http = parsed and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
        if (
            value.get("schema") != "tgw-platform-recovery-provider-binding/v1"
            or value.get("provider_id") != "tgw-platform-recovery-provider@1"
            or not parsed or (parsed.scheme != "https" and not local_http) or not parsed.netloc
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not isinstance(value.get("credential_env"), str) or _ENV.fullmatch(value["credential_env"]) is None
            or isinstance(value.get("timeout_seconds"), bool) or not isinstance(value.get("timeout_seconds"), int)
            or not 1 <= value["timeout_seconds"] <= 60
        ):
            raise RecoveryHostError("recovery provider binding is invalid")
        self.endpoint = endpoint.rstrip("/")
        self.credential_env = value["credential_env"]
        self.timeout = value["timeout_seconds"]

    def invoke(self, invocation: Mapping[str, Any]) -> Mapping[str, Any]:
        decision = invocation.get("decision")
        if decision not in {"diagnose-platform", "rollback-platform", "repair-tool-environment"}:
            raise RecoveryHostError("recovery provider effect is not allowlisted")
        credential = os.environ.get(self.credential_env)
        if not credential:
            raise RecoveryHostError("recovery provider credential is unavailable")
        invocation_hash = _hash(invocation)
        request = Request(
            f"{self.endpoint}/v1/platform-recovery/{decision}",
            data=_canonical({"invocation": dict(invocation), "invocation_hash": invocation_hash}),
            headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.timeout) as response:  # nosec: fixed provider binding
                raw = response.read(1024 * 1024 + 1)
                if response.status != 200 or len(raw) > 1024 * 1024:
                    raise RecoveryHostError("recovery provider returned an invalid response")
        except (HTTPError, URLError, OSError) as exc:
            raise RecoveryHostError("recovery provider is unavailable") from exc
        try:
            result = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RecoveryHostError("recovery provider response is invalid") from exc
        if (
            not isinstance(result, Mapping)
            or set(result) != {"schema", "provider_id", "invocation_hash", "status", "evidence", "rollback_receipt"}
            or result.get("schema") != "tgw-platform-recovery-provider-response/v1"
            or result.get("provider_id") != "tgw-platform-recovery-provider@1"
            or result.get("invocation_hash") != invocation_hash
            or result.get("status") not in {"DIAGNOSED", "ROLLED_BACK", "REPAIRED", "HELD", "FAILED"}
            or not isinstance(result.get("evidence"), list)
            or not all(isinstance(item, str) and _HASH.fullmatch(item) for item in result["evidence"])
            or (result.get("rollback_receipt") is not None and not isinstance(result["rollback_receipt"], str))
        ):
            raise RecoveryHostError("recovery provider response is invalid")
        return dict(result)


def configured_recovery_mount(config: Mapping[str, Any]) -> RecoveryConsoleMount:
    raw = config.get("platform_recovery")
    required = {
        "schema", "token_sha256", "card_root", "receipt_root", "refusal_root",
        "receipt_sink_hash", "renderer_sha256", "provider",
    }
    if not isinstance(raw, Mapping) or set(raw) != required or raw.get("schema") != "tgw-platform-recovery-host/v1":
        raise RecoveryHostError("platform recovery configuration is invalid")
    for field in ("token_sha256", "receipt_sink_hash", "renderer_sha256"):
        if not isinstance(raw.get(field), str) or _HASH.fullmatch(raw[field]) is None:
            raise RecoveryHostError(f"platform recovery {field} is invalid")
    card_root = _durable_directory(raw["card_root"], "recovery card root")
    receipt_root = _durable_directory(raw["receipt_root"], "recovery receipt root")
    refusal_root = _durable_directory(raw["refusal_root"], "recovery refusal root")
    renderer = Path(__file__).with_name("dynamic_surface.py")
    if renderer.is_symlink() or _file_hash(renderer) != raw["renderer_sha256"]:
        raise RecoveryHostError("pinned recovery renderer is unavailable")
    provider = _ConfiguredRecoveryProvider(raw["provider"])

    def load_card(recovery_id: str) -> Mapping[str, Any]:
        if not isinstance(recovery_id, str) or _IDENTITY.fullmatch(recovery_id) is None:
            raise RecoveryHostError("recovery identity is invalid")
        path = card_root / f"{recovery_id}.json"
        if path.is_symlink() or not path.is_file():
            raise RecoveryHostError("recovery card is unavailable")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RecoveryHostError("recovery card is invalid") from exc
        if not isinstance(value, Mapping):
            raise RecoveryHostError("recovery card is invalid")
        return value

    def claim(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
        claim_hash = _hash(invocation)
        value = {"schema": "tgw-w17-recovery-claim/v1", "status": "CLAIMED", "claim_hash": claim_hash}
        _persist_exclusive(receipt_root, claim_hash.removeprefix("sha256:") + ".claim.json", value)
        return value

    def persist_receipt(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
        identity = receipt.get("receipt_hash")
        if not isinstance(identity, str) or _HASH.fullmatch(identity) is None:
            raise RecoveryHostError("recovery receipt identity is invalid")
        return _persist_exclusive(receipt_root, identity.removeprefix("sha256:") + ".json", receipt)

    def persist_refusal(refusal: Mapping[str, Any]) -> None:
        identity = _hash({"refusal": refusal, "time_ns": time.time_ns()})
        _persist_exclusive(refusal_root, identity.removeprefix("sha256:") + ".json", refusal)

    return RecoveryConsoleMount(
        token_sha256=raw["token_sha256"], receipt_sink_hash=raw["receipt_sink_hash"],
        load_card=load_card, renderer_version=lambda: raw["renderer_sha256"],
        handler_contracts={"platform-recovery": {"decisions": [
            "diagnose-platform", "rollback-platform", "repair-tool-environment",
        ]}},
        handlers={"platform-recovery": provider.invoke}, persist_receipt=persist_receipt,
        persist_refusal=persist_refusal, claim_submission=claim,
    )


def create_recovery_app(config: Mapping[str, Any]) -> FastAPI:
    application = FastAPI(
        title="TGW platform recovery", docs_url=None, redoc_url=None, openapi_url=None,
    )
    application.include_router(create_recovery_console_router(configured_recovery_mount(config)))
    return application


def main() -> None:
    config_path = Path(os.environ.get("TGW_CONFIG", str(DEFAULT_CONFIG)))
    application = create_recovery_app(load_operational_config(config_path))
    host = os.environ.get("TGW_RECOVERY_HOST", "127.0.0.1")
    port = int(os.environ.get("TGW_RECOVERY_PORT", "7374"))
    uvicorn.run(application, host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()

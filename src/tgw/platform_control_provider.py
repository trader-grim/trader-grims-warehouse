"""Bounded W17/W18 provider for fleet refresh and platform recovery.

This process is the privileged side of the existing unprivileged console and
fleet clients.  It exposes a closed operation set, accepts no command, path,
URL, service name, or account from an HTTP request, and journals every step
under one configured durable root.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from tgw.config import DEFAULT_CONFIG, load_operational_config
from tgw.lifecycle_snapshot import LifecycleSnapshotSource

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_UNIT = re.compile(r"[A-Za-z0-9_.@-]+\.(?:service|timer)\Z")
_COLLECTIONS = ("live_requests", "role_leases", "rendered_surfaces", "continuations")


class PlatformControlError(ValueError):
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
        raise PlatformControlError(f"{label} is invalid")
    path = Path(value)
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents:
        raise PlatformControlError(f"{label} must be durable and outside /tmp")
    if not path.is_dir() or path.is_symlink():
        raise PlatformControlError(f"{label} is unavailable")
    return path


def _regular(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise PlatformControlError(f"{label} is invalid")
    path = Path(value)
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents:
        raise PlatformControlError(f"{label} must be outside /tmp")
    if not path.is_file() or path.is_symlink():
        raise PlatformControlError(f"{label} is unavailable")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlatformControlError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise PlatformControlError(f"{label} is invalid")
    return value


class _ActorFleetClient:
    """Exact client for the separately hosted ``tgw-lib`` actor provider."""

    _EXPECTED = {
        "quiesce": "QUIESCED", "rebuild": "REBUILT", "activate": "ACTIVATED",
        "restart": "RESTARTED", "health": "HEALTHY", "verify-actor": "VERIFIED",
        "rollback": "ROLLED_BACK", "repair": "REPAIRED",
    }

    def __init__(self, value: Mapping[str, Any]):
        required = {
            "schema", "provider_id", "endpoint", "transport", "expected_host",
            "credential_env", "timeout_seconds",
        }
        endpoint = value.get("endpoint") if isinstance(value, Mapping) else None
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        local = parsed and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
        tailscale = (
            parsed and value.get("transport") == "tailscale-http"
            and parsed.scheme == "http" and parsed.hostname == value.get("expected_host")
        )
        if (
            not isinstance(value, Mapping) or set(value) != required
            or value.get("schema") != "tgw-actor-fleet-provider-binding/v1"
            or value.get("provider_id") != "tgw-actor-fleet-provider@1"
            or not parsed or not parsed.netloc or not (local or tailscale)
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not isinstance(value.get("credential_env"), str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value["credential_env"]) is None
            or isinstance(value.get("timeout_seconds"), bool) or not isinstance(value.get("timeout_seconds"), int)
            or not 1 <= value["timeout_seconds"] <= 120
        ):
            raise PlatformControlError("actor fleet provider binding is invalid")
        self.endpoint = endpoint.rstrip("/")
        self.credential_env = value["credential_env"]
        self.timeout = value["timeout_seconds"]

    def call(self, step: str, arguments: list[Any]) -> dict[str, Any]:
        expected = self._EXPECTED.get(step)
        if expected is None:
            raise PlatformControlError("actor fleet provider step is not allowlisted")
        credential = os.environ.get(self.credential_env)
        if not credential:
            raise PlatformControlError("actor fleet provider credential is unavailable")
        invocation = {"schema": "tgw-actor-fleet-provider-invocation/v1", "step": step, "arguments": arguments}
        invocation_hash = _hash(invocation)
        request = Request(
            f"{self.endpoint}/v1/actor-fleet/{step}", data=_canonical({**invocation, "invocation_hash": invocation_hash}),
            headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.timeout) as response:  # nosec: exact root-owned provider binding
                raw = response.read(1024 * 1024 + 1)
                if response.status != 200 or len(raw) > 1024 * 1024:
                    raise PlatformControlError("actor fleet provider returned an invalid response")
        except (HTTPError, URLError, OSError) as exc:
            raise PlatformControlError("actor fleet provider is unavailable") from exc
        try:
            result = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PlatformControlError("actor fleet provider response is invalid") from exc
        if (
            not isinstance(result, Mapping)
            or set(result) != {"schema", "provider_id", "step", "invocation_hash", "result"}
            or result.get("schema") != "tgw-actor-fleet-provider-response/v1"
            or result.get("provider_id") != "tgw-actor-fleet-provider@1"
            or result.get("step") != step or result.get("invocation_hash") != invocation_hash
            or not isinstance(result.get("result"), Mapping) or result["result"].get("status") != expected
        ):
            raise PlatformControlError("actor fleet provider response is invalid")
        return dict(result["result"])


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    stage = path.with_name(f".{path.name}.next")
    if stage.exists() or stage.is_symlink():
        raise PlatformControlError(f"stale provider staging path exists: {stage}")
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if stage.exists() and not stage.is_symlink():
            stage.unlink()


class PlatformControlProvider:
    """Execute only the configured platform-control state machine."""

    def __init__(
        self, value: Mapping[str, Any], *,
        service_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        actor_client: Any | None = None,
        snapshot_source: Any | None = None,
    ):
        required = {
            "schema", "token_sha256", "state_root", "lifecycle_snapshot_source",
            "actor_provider", "systemctl_path", "managed_services",
        }
        if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != "tgw-platform-control-provider/v1":
            raise PlatformControlError("platform control provider configuration is invalid")
        if not isinstance(value.get("token_sha256"), str) or _HASH.fullmatch(value["token_sha256"]) is None:
            raise PlatformControlError("platform control provider token binding is invalid")
        self.token_sha256 = value["token_sha256"]
        self.state_root = _directory(value["state_root"], "platform control state root")
        self.systemctl = _regular(value["systemctl_path"], "systemctl executable")
        services = value.get("managed_services")
        if not isinstance(services, list) or not services or services != sorted(set(services)) or any(not isinstance(unit, str) or _UNIT.fullmatch(unit) is None for unit in services):
            raise PlatformControlError("managed platform service set is invalid")
        self.services = list(services)
        self._run = service_runner or self._run_service
        self.actor = actor_client or _ActorFleetClient(value["actor_provider"])
        self.snapshot_source = snapshot_source or LifecycleSnapshotSource(value["lifecycle_snapshot_source"])

    def authorized(self, authorization: str | None) -> bool:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return False
        return "sha256:" + hashlib.sha256(authorization[7:].encode()).hexdigest() == self.token_sha256

    def _run_service(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.systemctl), *arguments], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )

    def _service(self, operation: str, *, expected: str) -> None:
        for unit in self.services:
            result = self._run([operation, unit])
            if result.returncode != 0:
                raise PlatformControlError(f"managed service {operation} failed: {unit}")
        for unit in self.services:
            result = self._run(["is-active", unit])
            observed = result.stdout.strip()
            if observed != expected:
                raise PlatformControlError(f"managed service state mismatch: {unit}:{observed}")

    def _journal_path(self, transaction_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", transaction_id):
            raise PlatformControlError("platform transaction identity is invalid")
        return self.state_root / f"{transaction_id}.provider.json"

    def _journal(self, transaction_id: str) -> dict[str, Any]:
        path = self._journal_path(transaction_id)
        return _read_json(path, "platform provider journal") if path.is_file() and not path.is_symlink() else {
            "schema": "tgw-platform-control-journal/v1", "transaction_id": transaction_id,
            "status": "NEW", "request": None, "steps": [], "candidate_release": None,
            "materialization": None,
        }

    def _save(self, journal: dict[str, Any]) -> None:
        _atomic(self._journal_path(journal["transaction_id"]), journal)

    def _gate(self, *, status: str, request: Mapping[str, Any]) -> None:
        _atomic(self.state_root / "fleet-transition-gate.json", {
            "schema": "tgw-w18-fleet-transition-gate/v1", "status": status,
            "transaction_id": request["transaction_id"],
            "predecessor_generation": request["predecessor_generation"],
            "successor_generation": request["successor_generation"],
        })

    def _request(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or value.get("schema") != "tgw-w18-fleet-refresh-request/v1":
            raise PlatformControlError("fleet provider request is invalid")
        revisions = value.get("revisions")
        required_revisions = {"plan", "solution", "source", "catalog", "bootstrap", "broker_policy", "admission"}
        if not isinstance(revisions, Mapping) or set(revisions) != required_revisions:
            raise PlatformControlError("fleet provider revisions are incomplete")
        if _COMMIT.fullmatch(str(revisions["plan"])) is None or _COMMIT.fullmatch(str(revisions["source"])) is None:
            raise PlatformControlError("fleet provider Git revisions are invalid")
        for field in required_revisions - {"plan", "source"}:
            if not isinstance(revisions[field], str) or _HASH.fullmatch(revisions[field]) is None:
                raise PlatformControlError("fleet provider content revisions are invalid")
        return dict(value)

    def checkpoint(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        self._gate(status="SUSPENDING", request=value)
        try:
            snapshot = self.snapshot_source.snapshot(value["predecessor_generation"])
        except Exception:
            self._gate(status="ACTIVE", request=value)
            raise
        unsigned = dict(snapshot)
        claimed = unsigned.pop("snapshot_hash", None)
        if (
            snapshot.get("schema") != "tgw-w18-lifecycle-snapshot/v1"
            or claimed != _hash(unsigned)
            or snapshot.get("generation") != value["predecessor_generation"]
            or set(snapshot.get("collections", {})) != set(_COLLECTIONS)
        ):
            raise PlatformControlError("lifecycle snapshot is stale or invalid")
        result: dict[str, Any] = {
            "status": "CHECKPOINTED", "transaction_id": value["transaction_id"],
            "snapshot_hash": claimed, "observed_at": snapshot.get("observed_at"),
        }
        for name in _COLLECTIONS:
            records = snapshot["collections"][name]
            if not isinstance(records, list) or not all(isinstance(item, Mapping) for item in records):
                raise PlatformControlError(f"lifecycle snapshot collection is invalid: {name}")
            result[name] = [{**dict(item), "checkpoint_identity": _hash(item)} for item in records]
        journal = self._journal(value["transaction_id"])
        if journal["request"] not in (None, value):
            raise PlatformControlError("platform transaction request changed")
        journal["request"], journal["status"] = value, "CHECKPOINTED"
        journal["steps"].append({"name": "checkpoint", "receipt_hash": _hash(result)})
        self._gate(status="SUSPENDED", request=value)
        self._save(journal)
        return result

    def quiesce(self, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
        transaction_id = checkpoint.get("transaction_id")
        journal = self._journal(str(transaction_id))
        if journal["status"] != "CHECKPOINTED":
            raise PlatformControlError("platform refresh is not checkpointed")
        actor_receipt = self.actor.call("quiesce", [journal["request"]])
        try:
            self._service("stop", expected="inactive")
        except Exception:
            self.actor.call("rollback", [journal["request"]])
            raise
        journal["status"] = "QUIESCED"
        journal["steps"].append({"name": "actor-quiesce", "receipt_hash": _hash(actor_receipt)})
        self._gate(status="QUIESCED", request=journal["request"])
        self._save(journal)
        return {"status": "QUIESCED", "transaction_id": transaction_id, "services": self.services, "actor_receipt_hash": _hash(actor_receipt)}

    def rebuild(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] != "QUIESCED":
            raise PlatformControlError("platform fleet is not quiesced")
        prepared = self.actor.call("rebuild", [value])
        journal["status"] = "REBUILT"
        journal["steps"].append({"name": "actor-rebuild", "receipt_hash": _hash(prepared)})
        self._save(journal)
        return {"status": "REBUILT", "transaction_id": value["transaction_id"], "candidate_commit": value["revisions"]["source"], "actor_receipt_hash": _hash(prepared)}

    def activate(self, request: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] != "REBUILT" or rebuilt.get("candidate_commit") != value["revisions"]["source"]:
            raise PlatformControlError("platform actor activation is not bound to rebuild")
        applied = self.actor.call("activate", [value, rebuilt])
        journal["status"] = "ACTIVATED"
        journal["steps"].append({"name": "actor-activate", "receipt_hash": _hash(applied)})
        self._save(journal)
        return {"status": "ACTIVATED", "transaction_id": value["transaction_id"], "generation": value["successor_generation"], "materialization_hash": _hash(applied)}

    def restart(self, activated: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(activated.get("transaction_id")))
        if journal["status"] != "ACTIVATED" or activated.get("generation") != journal["request"]["successor_generation"]:
            raise PlatformControlError("platform restart is not bound to activation")
        self._service("restart", expected="active")
        actor_receipt = self.actor.call("restart", [activated])
        journal["status"] = "RESTARTED"
        journal["steps"].append({"name": "actor-restart", "receipt_hash": _hash(actor_receipt)})
        self._save(journal)
        return {"status": "RESTARTED", "transaction_id": journal["transaction_id"], "services": self.services, "actor_receipt_hash": _hash(actor_receipt)}

    def health(self, restarted: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(restarted.get("transaction_id")))
        if journal["status"] != "RESTARTED":
            raise PlatformControlError("platform health check is not bound to restart")
        self._service("is-active", expected="active")
        actor_receipt = self.actor.call("health", [restarted])
        journal["status"] = "HEALTHY"
        journal["steps"].append({"name": "actor-health", "receipt_hash": _hash(actor_receipt)})
        self._save(journal)
        return {"status": "HEALTHY", "transaction_id": journal["transaction_id"], "services": self.services, "actor_receipt_hash": _hash(actor_receipt)}

    def verify_actor(self, actor: str, request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] not in {"HEALTHY", "VERIFYING"} or actor not in value["actors"]:
            raise PlatformControlError("actor verification is not legal")
        verified = self.actor.call("verify-actor", [actor, value])
        if verified.get("actor") != actor or verified.get("generation") != value["successor_generation"]:
            raise PlatformControlError("actor fleet verification response mismatch")
        journal["status"] = "VERIFYING"
        journal["steps"].append({"name": "verify-actor", "actor": actor, "receipt_hash": _hash(verified)})
        self._save(journal)
        return verified

    def resume(self, checkpoint: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] not in {"VERIFYING", "ROLLED_BACK"}:
            raise PlatformControlError("platform resume is not legal")
        dispositions = {
            name: [{"checkpoint_identity": item["checkpoint_identity"], "disposition": "reconcile"} for item in checkpoint[name]]
            for name in _COLLECTIONS
        }
        journal["status"] = "RESUMED"
        self._gate(status="ACTIVE", request=value)
        self._save(journal)
        return {"status": "RESUMED", "transaction_id": value["transaction_id"], "generation": value["successor_generation"], "dispositions": dispositions}

    def rollback(self, request: Mapping[str, Any], _controller_journal: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        journal = self._journal(value["transaction_id"])
        actor_receipt = self.actor.call("rollback", [value])
        self._service("restart", expected="active")
        journal["status"] = "ROLLED_BACK"
        journal["steps"].append({"name": "actor-rollback", "receipt_hash": _hash(actor_receipt)})
        self._gate(status="QUIESCED", request=value)
        self._save(journal)
        return {"status": "ROLLED_BACK", "transaction_id": value["transaction_id"], "generation": value["predecessor_generation"], "actor_receipt_hash": _hash(actor_receipt)}

    def fleet(self, step: str, arguments: list[Any]) -> Mapping[str, Any]:
        dispatch = {
            "checkpoint": (self.checkpoint, 1), "quiesce": (self.quiesce, 1),
            "rebuild": (self.rebuild, 1), "activate": (self.activate, 2),
            "restart": (self.restart, 1), "health": (self.health, 1),
            "verify-actor": (self.verify_actor, 2), "resume": (self.resume, 2),
            "rollback": (self.rollback, 2),
        }
        target = dispatch.get(step)
        if target is None or len(arguments) != target[1]:
            raise PlatformControlError("fleet provider step or arguments are not allowlisted")
        return target[0](*arguments)

    def recover(self, decision: str, invocation: Mapping[str, Any]) -> Mapping[str, Any]:
        recovery = invocation.get("recovery") if isinstance(invocation, Mapping) else None
        if not isinstance(recovery, Mapping) or decision not in {"diagnose-platform", "rollback-platform", "repair-tool-environment"}:
            raise PlatformControlError("platform recovery invocation is invalid")
        gate_path = self.state_root / "fleet-transition-gate.json"
        gate = _read_json(gate_path, "fleet transition gate") if gate_path.is_file() and not gate_path.is_symlink() else {"status": "UNINITIALIZED"}
        evidence = [_hash({"gate": gate})]
        if decision == "diagnose-platform":
            return {"status": "DIAGNOSED", "evidence": evidence, "rollback_receipt": None}
        candidates = sorted(self.state_root.glob("*.provider.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        if not candidates:
            raise PlatformControlError("no platform transaction is available for recovery")
        journal = _read_json(candidates[0], "platform provider journal")
        request = journal.get("request")
        if not isinstance(request, Mapping) or request.get("revisions", {}).get("source") != recovery.get("candidate_commit"):
            raise PlatformControlError("recovery candidate differs from the repair transaction")
        if decision == "rollback-platform":
            result = self.rollback(request, journal)
            return {"status": "ROLLED_BACK", "evidence": evidence + [_hash(result)], "rollback_receipt": _hash(result)}
        repaired = self.actor.call("repair", [dict(request)])
        return {"status": "REPAIRED", "evidence": evidence + [_hash(repaired)], "rollback_receipt": None}


def create_platform_control_app(config: Mapping[str, Any], **provider_kwargs: Any) -> FastAPI:
    raw = config.get("platform_control_provider")
    provider = PlatformControlProvider(raw, **provider_kwargs)
    app = FastAPI(title="TGW platform control provider", docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(authorization: str | None) -> None:
        if not provider.authorized(authorization):
            raise HTTPException(status_code=401, detail="platform control authentication failed")

    @app.post("/v1/fleet-refresh/{step}")
    def fleet(step: str, body: Mapping[str, Any], authorization: str | None = Header(default=None)) -> Mapping[str, Any]:
        authenticate(authorization)
        invocation = {key: body[key] for key in ("schema", "step", "arguments") if key in body}
        if set(body) != {"schema", "step", "arguments", "invocation_hash"} or step != body.get("step") or body.get("invocation_hash") != _hash(invocation):
            raise HTTPException(status_code=409, detail="fleet provider invocation binding is invalid")
        try:
            result = provider.fleet(step, list(body["arguments"]))
        except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema": "tgw-fleet-refresh-provider-response/v1", "provider_id": "tgw-fleet-refresh-provider@1", "step": step, "invocation_hash": body["invocation_hash"], "result": result}

    @app.post("/v1/platform-recovery/{decision}")
    def recovery(decision: str, body: Mapping[str, Any], authorization: str | None = Header(default=None)) -> Mapping[str, Any]:
        authenticate(authorization)
        invocation = body.get("invocation")
        if set(body) != {"invocation", "invocation_hash"} or not isinstance(invocation, Mapping) or body.get("invocation_hash") != _hash(invocation) or invocation.get("decision") != decision:
            raise HTTPException(status_code=409, detail="recovery provider invocation binding is invalid")
        try:
            result = provider.recover(decision, invocation)
        except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "schema": "tgw-platform-recovery-provider-response/v1", "provider_id": "tgw-platform-recovery-provider@1",
            "invocation_hash": body["invocation_hash"], "status": result["status"],
            "evidence": result["evidence"], "rollback_receipt": result["rollback_receipt"],
        }

    return app


def main() -> None:
    config = load_operational_config(Path(os.environ.get("TGW_CONFIG", str(DEFAULT_CONFIG))))
    app = create_platform_control_app(config)
    uvicorn.run(
        app, host=os.environ.get("TGW_PLATFORM_CONTROL_HOST", "127.0.0.1"),
        port=int(os.environ.get("TGW_PLATFORM_CONTROL_PORT", "7555")), access_log=False,
    )


if __name__ == "__main__":
    main()

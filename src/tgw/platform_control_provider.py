"""Bounded W17/W18 provider for fleet refresh and platform recovery.

This process is the privileged side of the existing unprivileged console and
fleet clients.  It exposes a closed operation set, accepts no command, path,
URL, service name, or account from an HTTP request, and journals every step
under one configured durable root.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from tgw.config import DEFAULT_CONFIG, load_operational_config

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_UNIT = re.compile(r"[A-Za-z0-9_.@-]+\.service\Z")
_COLLECTIONS = ("live_requests", "role_leases", "rendered_surfaces", "continuations")


class PlatformControlError(ValueError):
    pass


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
        materializer_loader: Callable[[Path], Any] | None = None,
    ):
        required = {
            "schema", "token_sha256", "state_root", "lifecycle_snapshot_path",
            "release_root", "admission_root", "actor_generation_root",
            "contract_public_key", "systemctl_path",
            "managed_services",
        }
        if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != "tgw-platform-control-provider/v1":
            raise PlatformControlError("platform control provider configuration is invalid")
        if not isinstance(value.get("token_sha256"), str) or _HASH.fullmatch(value["token_sha256"]) is None:
            raise PlatformControlError("platform control provider token binding is invalid")
        self.token_sha256 = value["token_sha256"]
        self.state_root = _directory(value["state_root"], "platform control state root")
        self.snapshot_path = _regular(value["lifecycle_snapshot_path"], "lifecycle snapshot")
        self.release_root = _directory(value["release_root"], "platform release root")
        self.admission_root = _directory(value["admission_root"], "platform admission root")
        self.actor_generation_root = _directory(value["actor_generation_root"], "actor generation root")
        self.systemctl = _regular(value["systemctl_path"], "systemctl executable")
        services = value.get("managed_services")
        if not isinstance(services, list) or not services or services != sorted(set(services)) or any(not isinstance(unit, str) or _UNIT.fullmatch(unit) is None for unit in services):
            raise PlatformControlError("managed platform service set is invalid")
        self.services = list(services)
        if not isinstance(value.get("contract_public_key"), str) or not value["contract_public_key"]:
            raise PlatformControlError("platform contract signer is invalid")
        self.contract_public_key = value["contract_public_key"]
        self._run = service_runner or self._run_service
        self._load_materializer = materializer_loader or self._materializer

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

    def _candidate(self, request: Mapping[str, Any]) -> Path:
        admission_hash = request["revisions"]["admission"]
        admission = _read_json(self.admission_root / (admission_hash.removeprefix("sha256:") + ".json"), "fleet admission receipt")
        unsigned = dict(admission)
        claimed = unsigned.pop("receipt_hash", None)
        candidate = admission.get("candidate") if isinstance(admission.get("candidate"), Mapping) else {}
        plan = admission.get("plan") if isinstance(admission.get("plan"), Mapping) else {}
        if (
            claimed != admission_hash or _hash(unsigned) != admission_hash
            or admission.get("status") != "ADMITTED"
            or candidate.get("commit") != request["revisions"]["source"]
            or plan.get("commit") != request["revisions"]["plan"]
            or plan.get("solution_hash") != request["revisions"]["solution"]
        ):
            raise PlatformControlError("fleet admission receipt is not exact")
        matches = []
        for child in self.release_root.iterdir():
            manifest_path = child / ".release-manifest.json"
            if child.is_dir() and not child.is_symlink() and manifest_path.is_file() and not manifest_path.is_symlink():
                manifest = _read_json(manifest_path, "release manifest")
                if manifest.get("commit") == request["revisions"]["source"]:
                    matches.append(child.resolve())
        if len(matches) != 1:
            raise PlatformControlError("exact admitted fleet candidate release is unavailable or ambiguous")
        return matches[0]

    @staticmethod
    def _materializer(release: Path) -> Any:
        source = release / "agent-services/installers/materialize.py"
        if not source.is_file() or source.is_symlink():
            raise PlatformControlError("candidate actor materializer is unavailable")
        spec = importlib.util.spec_from_file_location("tgw_candidate_actor_materializer", source)
        if spec is None or spec.loader is None:
            raise PlatformControlError("candidate actor materializer cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _actor_inputs(self, release: Path, request: Mapping[str, Any]) -> tuple[Any, dict[str, Any], dict[str, dict[str, Any]]]:
        materializer = self._load_materializer(release)
        generation_root = self.actor_generation_root / request["successor_generation"].removeprefix("sha256:")
        if not generation_root.is_dir() or generation_root.is_symlink():
            raise PlatformControlError("complete actor generation is unavailable")
        bundle = _read_json(generation_root / "bundle.json", "complete actor bundle")
        if bundle.get("generation") != request["successor_generation"] or sorted(bundle.get("actors", {})) != sorted(request["actors"]):
            raise PlatformControlError("complete actor bundle generation or actor set mismatch")
        contracts: dict[str, dict[str, Any]] = {}
        for actor in request["actors"]:
            contracts[actor] = _read_json(generation_root / "contracts" / f"{actor}.json", f"actor contract {actor}")
            if contracts[actor].get("catalog_hash") != request["revisions"]["catalog"]:
                raise PlatformControlError(f"actor contract catalog mismatch: {actor}")
        return materializer, bundle, contracts

    def _generation_root(self, request: Mapping[str, Any]) -> Path:
        root = self.actor_generation_root / request["successor_generation"].removeprefix("sha256:")
        if not root.is_dir() or root.is_symlink():
            raise PlatformControlError("complete actor generation is unavailable")
        return root

    def checkpoint(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        snapshot = _read_json(self.snapshot_path, "lifecycle snapshot")
        unsigned = dict(snapshot)
        claimed = unsigned.pop("snapshot_hash", None)
        if (
            snapshot.get("schema") != "tgw-w18-lifecycle-snapshot/v1"
            or claimed != _hash(unsigned)
            or snapshot.get("generation") != value["predecessor_generation"]
            or set(snapshot.get("collections", {})) != set(_COLLECTIONS)
        ):
            raise PlatformControlError("lifecycle snapshot is stale or invalid")
        result: dict[str, Any] = {"status": "CHECKPOINTED", "transaction_id": value["transaction_id"]}
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
        self._service("stop", expected="inactive")
        journal["status"] = "QUIESCED"
        self._gate(status="QUIESCED", request=journal["request"])
        self._save(journal)
        return {"status": "QUIESCED", "transaction_id": transaction_id, "services": self.services}

    def rebuild(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] != "QUIESCED":
            raise PlatformControlError("platform fleet is not quiesced")
        release = self._candidate(value)
        materializer, bundle, contracts = self._actor_inputs(release, value)
        prepared = materializer.materialize_complete_actor_contracts(
            bundle, source_root=release, contracts=contracts,
            trusted_contract_public_key=self.contract_public_key,
            additional_source_roots=(self._generation_root(value),),
        )
        if prepared.get("status") != "PREPARED":
            raise PlatformControlError("complete actor bundle did not prepare")
        journal["status"], journal["candidate_release"] = "REBUILT", str(release)
        journal["steps"].append({"name": "rebuild", "receipt_hash": _hash(prepared)})
        self._save(journal)
        return {"status": "REBUILT", "transaction_id": value["transaction_id"], "candidate_commit": value["revisions"]["source"], "preflight_hash": _hash(prepared)}

    def activate(self, request: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] != "REBUILT" or rebuilt.get("candidate_commit") != value["revisions"]["source"]:
            raise PlatformControlError("platform actor activation is not bound to rebuild")
        release = Path(journal["candidate_release"])
        materializer, bundle, contracts = self._actor_inputs(release, value)
        applied = materializer.materialize_complete_actor_contracts(
            bundle, source_root=release, contracts=contracts,
            trusted_contract_public_key=self.contract_public_key,
            apply=True, replace_existing=True,
            additional_source_roots=(self._generation_root(value),),
        )
        if applied.get("status") != "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED":
            raise PlatformControlError("complete actor bundle did not activate")
        journal["status"], journal["materialization"] = "ACTIVATED", applied
        self._save(journal)
        return {"status": "ACTIVATED", "transaction_id": value["transaction_id"], "generation": value["successor_generation"], "materialization_hash": _hash(applied)}

    def restart(self, activated: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(activated.get("transaction_id")))
        if journal["status"] != "ACTIVATED" or activated.get("generation") != journal["request"]["successor_generation"]:
            raise PlatformControlError("platform restart is not bound to activation")
        self._service("restart", expected="active")
        journal["status"] = "RESTARTED"
        self._save(journal)
        return {"status": "RESTARTED", "transaction_id": journal["transaction_id"], "services": self.services}

    def health(self, restarted: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(restarted.get("transaction_id")))
        if journal["status"] != "RESTARTED":
            raise PlatformControlError("platform health check is not bound to restart")
        self._service("is-active", expected="active")
        journal["status"] = "HEALTHY"
        self._save(journal)
        return {"status": "HEALTHY", "transaction_id": journal["transaction_id"], "services": self.services}

    def verify_actor(self, actor: str, request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] not in {"HEALTHY", "VERIFYING"} or actor not in value["actors"]:
            raise PlatformControlError("actor verification is not legal")
        bindings = [item for item in (journal.get("materialization") or {}).get("bindings", []) if item.get("actor") == actor]
        if not bindings:
            raise PlatformControlError("actor has no materialized contract")
        account = pwd.getpwnam(actor)
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:  # pragma: no cover - assertions are reported to parent
            os.close(read_fd)
            try:
                if os.geteuid() == 0:
                    os.initgroups(actor, account.pw_gid)
                    os.setgid(account.pw_gid)
                    os.setuid(account.pw_uid)
                elif os.geteuid() != account.pw_uid:
                    raise PermissionError("provider cannot assume actor identity")
                for binding in bindings:
                    destination, source = Path(binding["destination"]), Path(binding["source"])
                    if not destination.is_symlink() or destination.resolve(strict=False) != source:
                        raise PlatformControlError("actor contract binding changed")
                    with destination.open("rb") as handle:
                        handle.read(1)
                payload = {"status": "PASS", "uid": os.geteuid()}
            except Exception as exc:
                payload = {"status": "FAIL", "reason": str(exc)}
            os.write(write_fd, _canonical(payload))
            os.close(write_fd)
            os._exit(0 if payload["status"] == "PASS" else 1)
        os.close(write_fd)
        raw = b""
        while True:
            chunk = os.read(read_fd, 65536)
            if not chunk:
                break
            raw += chunk
        os.close(read_fd)
        _pid, wait_status = os.waitpid(child, 0)
        try:
            actor_proof = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PlatformControlError(f"actor contract verification failed: {actor}") from exc
        if wait_status != 0 or actor_proof != {"status": "PASS", "uid": account.pw_uid}:
            raise PlatformControlError(f"actor contract verification failed: {actor}")
        journal["status"] = "VERIFYING"
        journal["steps"].append({"name": "verify-actor", "actor": actor, "uid": account.pw_uid, "bindings_hash": _hash(bindings), "actor_proof_hash": _hash(actor_proof)})
        self._save(journal)
        return {"status": "VERIFIED", "actor": actor, "uid": account.pw_uid, "generation": value["successor_generation"], "bindings_hash": _hash(bindings), "actor_proof_hash": _hash(actor_proof)}

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
        applied = journal.get("materialization")
        if isinstance(applied, dict):
            release = Path(str(journal.get("candidate_release")))
            self._load_materializer(release).rollback_complete_actor_contracts(applied)
        self._service("restart", expected="active")
        journal["status"] = "ROLLED_BACK"
        self._gate(status="QUIESCED", request=value)
        self._save(journal)
        return {"status": "ROLLED_BACK", "transaction_id": value["transaction_id"], "generation": value["predecessor_generation"]}

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
        release = Path(str(journal.get("candidate_release")))
        materializer, bundle, contracts = self._actor_inputs(release, request)
        repaired = materializer.materialize_complete_actor_contracts(
            bundle, source_root=release, contracts=contracts,
            trusted_contract_public_key=self.contract_public_key,
            apply=True, replace_existing=True,
            additional_source_roots=(self._generation_root(request),),
        )
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

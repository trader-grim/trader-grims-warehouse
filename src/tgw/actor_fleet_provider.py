"""Privileged, closed W18 actor-fleet provider for the ``tgw-lib`` host.

The Plan/API coordinator runs on ``tgw-prod``.  Actor accounts, canonical
source, harness worktrees and actor-local MCP registrations run on ``tgw-lib``.
This provider is therefore deliberately separate from the production
platform-control provider.  It accepts only the fixed actor refresh state
machine and never accepts a command, path, account, service or candidate
selector from its caller.
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
_UNIT = re.compile(r"[A-Za-z0-9_.@-]+\.(?:service|timer)\Z")


class ActorFleetError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _directory(value: Any, label: str) -> Path:
    path = Path(value) if isinstance(value, str) else Path()
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents or not path.is_dir() or path.is_symlink():
        raise ActorFleetError(f"{label} must be a durable directory outside /tmp")
    return path


def _regular(value: Any, label: str) -> Path:
    path = Path(value) if isinstance(value, str) else Path()
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents or not path.is_file() or path.is_symlink():
        raise ActorFleetError(f"{label} is unavailable")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorFleetError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ActorFleetError(f"{label} is invalid")
    return value


def _binding_digest(path: Path) -> str:
    """Hash one post-activation binding using the materializer's rules."""
    if path.is_symlink():
        raise ActorFleetError("actor binding source cannot be a symlink")
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    elif path.is_dir():
        files = [
            item for item in path.rglob("*")
            if item.is_file() and not item.is_symlink() and "__pycache__" not in item.parts
        ]
        for item in sorted(files, key=lambda value: value.relative_to(path).as_posix()):
            digest.update(item.relative_to(path).as_posix().encode())
            digest.update(b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
    else:
        raise ActorFleetError("actor binding source is unavailable")
    return "sha256:" + digest.hexdigest()


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    stage = path.with_name(f".{path.name}.next")
    if stage.exists() or stage.is_symlink():
        raise ActorFleetError(f"stale actor-provider staging path exists: {stage}")
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


def _request(value: Any) -> dict[str, Any]:
    fields = {
        "schema", "transaction_id", "idempotency_key", "predecessor_generation",
        "successor_generation", "revisions", "actors",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != "tgw-w18-fleet-refresh-request/v1":
        raise ActorFleetError("actor fleet request is invalid")
    revisions = value.get("revisions")
    required = {"plan", "solution", "source", "catalog", "bootstrap", "broker_policy", "admission"}
    if not isinstance(revisions, Mapping) or set(revisions) != required:
        raise ActorFleetError("actor fleet revisions are incomplete")
    if _COMMIT.fullmatch(str(revisions["plan"])) is None or _COMMIT.fullmatch(str(revisions["source"])) is None:
        raise ActorFleetError("actor fleet Git revisions are invalid")
    if any(not isinstance(revisions[name], str) or _HASH.fullmatch(revisions[name]) is None for name in required - {"plan", "source"}):
        raise ActorFleetError("actor fleet content revisions are invalid")
    actors = value.get("actors")
    if not isinstance(actors, list) or not actors or actors != sorted(set(actors)) or any(not isinstance(actor, str) or not actor for actor in actors):
        raise ActorFleetError("actor fleet actor set is invalid")
    return dict(value)


class ActorFleetProvider:
    """Materialize and verify one admitted actor generation on ``tgw-lib``."""

    def __init__(
        self, value: Mapping[str, Any], *,
        service_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        materializer_loader: Callable[[Path], Any] | None = None,
    ):
        required = {
            "schema", "token_sha256", "state_root", "release_root", "admission_root",
            "actor_generation_root", "contract_public_key", "systemctl_path", "managed_services",
            "quiescence_units",
        }
        if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != "tgw-actor-fleet-provider/v1":
            raise ActorFleetError("actor fleet provider configuration is invalid")
        if not isinstance(value.get("token_sha256"), str) or _HASH.fullmatch(value["token_sha256"]) is None:
            raise ActorFleetError("actor fleet provider token binding is invalid")
        self.token_sha256 = value["token_sha256"]
        self.state_root = _directory(value["state_root"], "actor fleet state root")
        self.release_root = _directory(value["release_root"], "actor release root")
        self.admission_root = _directory(value["admission_root"], "actor admission root")
        self.actor_generation_root = _directory(value["actor_generation_root"], "actor generation root")
        self.systemctl = _regular(value["systemctl_path"], "systemctl executable")
        services = value.get("managed_services")
        if not isinstance(services, list) or not services or services != sorted(set(services)) or any(not isinstance(unit, str) or _UNIT.fullmatch(unit) is None for unit in services):
            raise ActorFleetError("managed actor service set is invalid")
        self.services = list(services)
        quiescence = value.get("quiescence_units")
        if not isinstance(quiescence, list) or quiescence != sorted(set(quiescence)) or any(not isinstance(unit, str) or _UNIT.fullmatch(unit) is None for unit in quiescence):
            raise ActorFleetError("actor quiescence unit set is invalid")
        self.quiescence_units = list(quiescence)
        if not isinstance(value.get("contract_public_key"), str) or not value["contract_public_key"]:
            raise ActorFleetError("actor contract signer is invalid")
        self.contract_public_key = value["contract_public_key"]
        self._run = service_runner or self._run_service
        self._load_materializer = materializer_loader or self._materializer

    def authorized(self, authorization: str | None) -> bool:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return False
        return "sha256:" + hashlib.sha256(authorization[7:].encode()).hexdigest() == self.token_sha256

    def _run_service(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.systemctl), *arguments], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

    def _service(self, operation: str, *, expected: str) -> None:
        for unit in self.services:
            result = self._run([operation, unit])
            if result.returncode != 0:
                raise ActorFleetError(f"managed actor service {operation} failed: {unit}")
        for unit in self.services:
            result = self._run(["is-active", unit])
            if result.returncode not in {0, 3} or result.stdout.strip() != expected:
                raise ActorFleetError(f"managed actor service state mismatch: {unit}:{result.stdout.strip()}")

    def _journal_path(self, transaction_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", transaction_id):
            raise ActorFleetError("actor transaction identity is invalid")
        return self.state_root / f"{transaction_id}.actor-provider.json"

    def _journal(self, transaction_id: str) -> dict[str, Any]:
        path = self._journal_path(transaction_id)
        return _read_json(path, "actor provider journal") if path.is_file() and not path.is_symlink() else {
            "schema": "tgw-actor-fleet-journal/v1", "transaction_id": transaction_id,
            "status": "NEW", "request": None, "candidate_release": None, "materialization": None,
        }

    def _save(self, journal: Mapping[str, Any]) -> None:
        _atomic(self._journal_path(str(journal["transaction_id"])), journal)

    @staticmethod
    def _materializer(release: Path) -> Any:
        source = release / "agent-services/installers/materialize.py"
        if not source.is_file() or source.is_symlink():
            raise ActorFleetError("candidate actor materializer is unavailable")
        spec = importlib.util.spec_from_file_location("tgw_candidate_actor_materializer", source)
        if spec is None or spec.loader is None:
            raise ActorFleetError("candidate actor materializer cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _candidate(self, request: Mapping[str, Any]) -> Path:
        admission_hash = request["revisions"]["admission"]
        admission = _read_json(self.admission_root / (admission_hash.removeprefix("sha256:") + ".json"), "actor admission receipt")
        unsigned = dict(admission)
        claimed = unsigned.pop("receipt_hash", None)
        candidate = admission.get("candidate") if isinstance(admission.get("candidate"), Mapping) else {}
        plan = admission.get("plan") if isinstance(admission.get("plan"), Mapping) else {}
        if (
            claimed != admission_hash or _hash(unsigned) != admission_hash or admission.get("status") != "ADMITTED"
            or candidate.get("commit") != request["revisions"]["source"]
            or plan.get("commit") != request["revisions"]["plan"]
            or plan.get("solution_hash") != request["revisions"]["solution"]
        ):
            raise ActorFleetError("actor admission receipt is not exact")
        matches = []
        for child in self.release_root.iterdir():
            manifest_path = child / ".release-manifest.json"
            if child.is_dir() and not child.is_symlink() and manifest_path.is_file() and not manifest_path.is_symlink():
                manifest = _read_json(manifest_path, "actor release manifest")
                if manifest.get("commit") == request["revisions"]["source"]:
                    matches.append(child.resolve())
        if len(matches) != 1:
            raise ActorFleetError("exact admitted actor release is unavailable or ambiguous")
        return matches[0]

    def _generation_root(self, request: Mapping[str, Any]) -> Path:
        root = self.actor_generation_root / request["successor_generation"].removeprefix("sha256:")
        if not root.is_dir() or root.is_symlink():
            raise ActorFleetError("complete actor generation is unavailable")
        return root

    def _actor_inputs(self, release: Path, request: Mapping[str, Any]) -> tuple[Any, dict[str, Any], dict[str, dict[str, Any]]]:
        root = self._generation_root(request)
        bundle = _read_json(root / "bundle.json", "complete actor bundle")
        receipt = _read_json(root / "generation-receipt.json", "actor generation receipt")
        unsigned_receipt = dict(receipt)
        claimed_receipt = unsigned_receipt.pop("receipt_hash", None)
        identity = receipt.get("generation_identity") if isinstance(receipt.get("generation_identity"), Mapping) else {}
        if (
            bundle.get("generation") != request["successor_generation"]
            or sorted(bundle.get("actors", {})) != request["actors"]
            or claimed_receipt != _hash(unsigned_receipt)
            or receipt.get("status") != "PREPARED"
            or receipt.get("generation") != request["successor_generation"]
            or receipt.get("actors") != request["actors"]
            or receipt.get("bundle_hash") != _hash(bundle)
            or receipt.get("signer_public_key") != self.contract_public_key
            or identity.get("catalog_hash") != request["revisions"]["catalog"]
            or identity.get("plan_commit") != request["revisions"]["plan"]
            or identity.get("solution_hash") != request["revisions"]["solution"]
            or identity.get("source_commit") != request["revisions"]["source"]
        ):
            raise ActorFleetError("actor generation receipt is not exact")
        contracts: dict[str, dict[str, Any]] = {}
        for actor in request["actors"]:
            contract = _read_json(root / "contracts" / f"{actor}.json", f"actor contract {actor}")
            if (
                contract.get("actor") != actor
                or contract.get("catalog_hash") != request["revisions"]["catalog"]
                or contract.get("plan") != {"commit": request["revisions"]["plan"], "solution_hash": request["revisions"]["solution"]}
                or contract.get("code_graph", {}).get("commit") != request["revisions"]["source"]
            ):
                raise ActorFleetError(f"actor contract revision mismatch: {actor}")
            contracts[actor] = contract
        return self._load_materializer(release), bundle, contracts

    def quiesce(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["request"] not in (None, value) or journal["status"] not in {"NEW", "QUIESCED"}:
            raise ActorFleetError("actor fleet quiesce is not legal")
        self._service("stop", expected="inactive")
        for unit in self.quiescence_units:
            result = self._run(["stop", unit])
            if result.returncode != 0:
                raise ActorFleetError(f"actor quiescence stop failed: {unit}")
            observed = self._run(["is-active", unit])
            if observed.returncode not in {0, 3} or observed.stdout.strip() != "inactive":
                raise ActorFleetError(f"actor quiescence state mismatch: {unit}:{observed.stdout.strip()}")
        journal.update({"request": value, "status": "QUIESCED"})
        self._save(journal)
        return {
            "status": "QUIESCED", "transaction_id": value["transaction_id"],
            "services": self.services, "quiescence_units": self.quiescence_units,
        }

    def rebuild(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] != "QUIESCED":
            raise ActorFleetError("actor fleet is not quiesced")
        release = self._candidate(value)
        materializer, bundle, contracts = self._actor_inputs(release, value)
        prepared = materializer.materialize_complete_actor_contracts(
            bundle, source_root=release, contracts=contracts,
            trusted_contract_public_key=self.contract_public_key,
            additional_source_roots=(self._generation_root(value),),
        )
        if prepared.get("status") != "PREPARED":
            raise ActorFleetError("complete actor bundle did not prepare")
        journal.update({"status": "REBUILT", "candidate_release": str(release)})
        self._save(journal)
        return {"status": "REBUILT", "transaction_id": value["transaction_id"], "candidate_commit": value["revisions"]["source"], "preflight_hash": _hash(prepared)}

    def activate(self, request: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] != "REBUILT" or rebuilt.get("candidate_commit") != value["revisions"]["source"]:
            raise ActorFleetError("actor activation is not bound to rebuild")
        release = Path(str(journal["candidate_release"]))
        materializer, bundle, contracts = self._actor_inputs(release, value)
        applied = materializer.materialize_complete_actor_contracts(
            bundle, source_root=release, contracts=contracts,
            trusted_contract_public_key=self.contract_public_key, apply=True, replace_existing=True,
            additional_source_roots=(self._generation_root(value),),
        )
        if applied.get("status") != "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED":
            raise ActorFleetError("complete actor bundle did not activate")
        journal.update({"status": "ACTIVATED", "materialization": applied})
        self._save(journal)
        return {"status": "ACTIVATED", "transaction_id": value["transaction_id"], "generation": value["successor_generation"], "materialization_hash": _hash(applied)}

    def restart(self, activated: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(activated.get("transaction_id")))
        if journal["status"] != "ACTIVATED" or activated.get("generation") != journal["request"]["successor_generation"]:
            raise ActorFleetError("actor restart is not bound to activation")
        self._service("restart", expected="active")
        journal["status"] = "RESTARTED"
        self._save(journal)
        return {"status": "RESTARTED", "transaction_id": journal["transaction_id"], "services": self.services}

    def health(self, restarted: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(restarted.get("transaction_id")))
        if journal["status"] != "RESTARTED":
            raise ActorFleetError("actor health is not bound to restart")
        self._service("is-active", expected="active")
        journal["status"] = "HEALTHY"
        self._save(journal)
        return {"status": "HEALTHY", "transaction_id": journal["transaction_id"], "services": self.services}

    def verify_actor(self, actor: str, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] not in {"HEALTHY", "VERIFYING"} or actor not in value["actors"]:
            raise ActorFleetError("actor verification is not legal")
        bindings = [item for item in (journal.get("materialization") or {}).get("bindings", []) if item.get("actor") == actor]
        if not bindings:
            raise ActorFleetError("actor has no materialized contract")
        _materializer, bundle, contracts = self._actor_inputs(Path(str(journal["candidate_release"])), value)
        contract = contracts[actor]
        local = contract.get("local") if isinstance(contract.get("local"), Mapping) else {}
        specification = bundle["actors"][actor]
        declared = {
            (item["kind"], item["name"], item["destination"]): item["sha256"]
            for item in specification["bindings"]
        }
        observed = {
            (item.get("kind"), item.get("name"), item.get("destination")): item.get("sha256")
            for item in bindings
        }
        if observed != declared:
            raise ActorFleetError("actor materialization differs from its complete bundle")
        account = pwd.getpwnam(actor)
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:  # pragma: no cover - result is asserted in the parent
            os.close(read_fd)
            try:
                if os.geteuid() == 0:
                    os.initgroups(actor, account.pw_gid)
                    os.setgid(account.pw_gid)
                    os.setuid(account.pw_uid)
                elif os.geteuid() != account.pw_uid:
                    raise PermissionError("actor provider cannot assume actor identity")
                for binding in bindings:
                    destination, source = Path(binding["destination"]), Path(binding["source"])
                    if not destination.is_symlink() or destination.resolve(strict=False) != source:
                        raise ActorFleetError("actor contract binding changed")
                    if _binding_digest(source) != binding["sha256"]:
                        raise ActorFleetError("actor contract binding content changed")
                by_kind: dict[str, dict[str, Mapping[str, Any]]] = {}
                for binding in bindings:
                    by_kind.setdefault(binding["kind"], {})[binding["name"]] = binding
                mcp_bindings = [{
                    "endpoint": name,
                    "source_sha256": binding["sha256"],
                    "destination": binding["destination"],
                } for name, binding in sorted(by_kind.get("mcp", {}).items())]
                environment_binding = by_kind.get("environment", {}).get("environment-catalog")
                if environment_binding is None:
                    raise ActorFleetError("actor environment catalog binding is missing")
                environment = _read_json(Path(environment_binding["source"]), "actor environment catalog")
                profile = environment.get("profiles", {}).get(contract.get("profile"), {})
                actor_declaration = environment.get("actors", {}).get(actor, {})
                if (
                    _hash(environment) != contract.get("catalog_hash")
                    or contract.get("plan") != {
                        "commit": value["revisions"]["plan"],
                        "solution_hash": value["revisions"]["solution"],
                    }
                    or contract.get("code_graph", {}).get("commit") != value["revisions"]["source"]
                    or set(by_kind.get("skill", {})) != set(local.get("skills", {}))
                    or set(by_kind.get("hook", {})) != set(local.get("hooks", {}))
                    or set(by_kind.get("mcp", {})) != set(local.get("mcp", {}).get("endpoints", []))
                    or _hash(mcp_bindings) != local.get("mcp", {}).get("binding_hash")
                    or by_kind.get("launcher", {}).get("launcher", {}).get("destination") != local.get("launcher", {}).get("path")
                    or by_kind.get("launcher", {}).get("launcher", {}).get("sha256") != local.get("launcher", {}).get("sha256")
                    or by_kind.get("bootstrap", {}).get("bootstrap-receipt", {}).get("sha256") != local.get("bootstrap_receipt_hash")
                    or actor_declaration.get("enabled") is not True
                    or contract.get("profile") not in actor_declaration.get("permitted_profiles", [])
                    or profile.get("state") != "ready-for-preflight"
                ):
                    raise ActorFleetError("actor startup binding is stale or mixed")
                payload = {
                    "status": "PASS", "uid": os.geteuid(),
                    "plan": value["revisions"]["plan"],
                    "solution": value["revisions"]["solution"],
                    "source": value["revisions"]["source"],
                    "catalog": value["revisions"]["catalog"],
                    "generation": value["successor_generation"],
                    "profile": contract["profile"],
                    "required_capabilities": sorted(profile.get("broker_capabilities", [])),
                }
            except Exception as exc:
                payload = {"status": "FAIL", "reason": str(exc)}
            os.write(write_fd, _canonical(payload))
            os.close(write_fd)
            os._exit(0 if payload["status"] == "PASS" else 1)
        os.close(write_fd)
        raw = b""
        while chunk := os.read(read_fd, 65536):
            raw += chunk
        os.close(read_fd)
        _pid, wait_status = os.waitpid(child, 0)
        try:
            proof = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ActorFleetError(f"actor contract verification failed: {actor}") from exc
        if wait_status != 0 or proof.get("status") != "PASS" or proof.get("uid") != account.pw_uid:
            raise ActorFleetError(f"actor contract verification failed: {actor}")
        journal["status"] = "VERIFYING"
        self._save(journal)
        return {
            "status": "VERIFIED", "actor": actor, "uid": account.pw_uid,
            "generation": value["successor_generation"],
            "plan": proof["plan"], "solution": proof["solution"],
            "source": proof["source"], "catalog": proof["catalog"],
            "profile": proof["profile"],
            "required_capabilities": proof["required_capabilities"],
            "bindings_hash": _hash(bindings), "actor_proof_hash": _hash(proof),
        }

    def rollback(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        applied = journal.get("materialization")
        if isinstance(applied, dict):
            release = Path(str(journal.get("candidate_release")))
            self._load_materializer(release).rollback_complete_actor_contracts(applied)
        self._service("restart", expected="active")
        journal["status"] = "ROLLED_BACK"
        self._save(journal)
        return {"status": "ROLLED_BACK", "transaction_id": value["transaction_id"], "generation": value["predecessor_generation"]}

    def repair(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Re-apply only the admitted generation already bound to a transaction."""
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal.get("request") != value or not isinstance(journal.get("candidate_release"), str):
            raise ActorFleetError("actor repair is not bound to an admitted transaction")
        release = Path(journal["candidate_release"])
        if release != self._candidate(value):
            raise ActorFleetError("actor repair candidate changed")
        materializer, bundle, contracts = self._actor_inputs(release, value)
        repaired = materializer.materialize_complete_actor_contracts(
            bundle, source_root=release, contracts=contracts,
            trusted_contract_public_key=self.contract_public_key, apply=True, replace_existing=True,
            additional_source_roots=(self._generation_root(value),),
        )
        if repaired.get("status") != "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED":
            raise ActorFleetError("complete actor bundle did not repair")
        journal.update({"status": "REPAIRED", "materialization": repaired})
        self._save(journal)
        return {
            "status": "REPAIRED", "transaction_id": value["transaction_id"],
            "generation": value["successor_generation"], "materialization_hash": _hash(repaired),
        }

    def dispatch(self, step: str, arguments: list[Any]) -> Mapping[str, Any]:
        operations = {
            "quiesce": (self.quiesce, 1), "rebuild": (self.rebuild, 1),
            "activate": (self.activate, 2), "restart": (self.restart, 1),
            "health": (self.health, 1), "verify-actor": (self.verify_actor, 2),
            "rollback": (self.rollback, 1), "repair": (self.repair, 1),
        }
        target = operations.get(step)
        if target is None or len(arguments) != target[1]:
            raise ActorFleetError("actor provider step or arguments are not allowlisted")
        return target[0](*arguments)


def create_actor_fleet_app(config: Mapping[str, Any], **provider_kwargs: Any) -> FastAPI:
    provider = ActorFleetProvider(config.get("actor_fleet_provider"), **provider_kwargs)
    app = FastAPI(title="TGW actor fleet provider", docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/v1/actor-fleet/{step}")
    def actor_fleet(step: str, body: Mapping[str, Any], authorization: str | None = Header(default=None)) -> Mapping[str, Any]:
        if not provider.authorized(authorization):
            raise HTTPException(status_code=401, detail="actor fleet authentication failed")
        invocation = {key: body[key] for key in ("schema", "step", "arguments") if key in body}
        if set(body) != {"schema", "step", "arguments", "invocation_hash"} or body.get("schema") != "tgw-actor-fleet-provider-invocation/v1" or body.get("step") != step or body.get("invocation_hash") != _hash(invocation):
            raise HTTPException(status_code=409, detail="actor provider invocation binding is invalid")
        try:
            result = provider.dispatch(step, list(body["arguments"]))
        except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema": "tgw-actor-fleet-provider-response/v1", "provider_id": "tgw-actor-fleet-provider@1", "step": step, "invocation_hash": body["invocation_hash"], "result": result}

    return app


def main() -> int:
    config = load_operational_config(Path(os.environ.get("TGW_CONFIG", str(DEFAULT_CONFIG))))
    host = os.environ.get("TGW_ACTOR_FLEET_HOST", "127.0.0.1")
    port = int(os.environ.get("TGW_ACTOR_FLEET_PORT", "7556"))
    uvicorn.run(create_actor_fleet_app(config), host=host, port=port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

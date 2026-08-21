"""W16 argument-constrained runner for registered production procedures.

The registry is declarative.  This module is the effect boundary: it accepts
one exact signed deployment approval, substitutes only declared placeholders,
writes a durable prepared receipt before execution, and refuses replay.  It
does not accept shell text, environment overrides, working directories, or
executables from a request.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tgw.procedure_registry import load_procedure_registry, resolve_procedure


class ProcedureRunnerError(ValueError):
    """The request cannot cross the registered procedure boundary."""


_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}\Z")
_VALUE = re.compile(r"[A-Za-z0-9_./:@+=,-]{1,1024}\Z")
_PLACEHOLDER = re.compile(r":[a-z][a-z0-9_]{0,63}\Z")
_RESERVED_GENERATIONS = frozenset({"current", "releases", "operations", "receipts", "refusals"})
_RESERVED_GENERATION_PREFIXES = (".stage-", ".current-")
_REQUEST_FIELDS = {
    "schema", "request_id", "procedure_id", "registry_revision", "plan_commit",
    "solution_hash", "card_hash", "parameters", "precondition_evidence",
    "bindings", "request_hash", "approval",
}
_APP_PATH_ROOTS = {
    "archive": (Path("/opt/TGW/incoming"),),
    "admission_receipt": (Path("/opt/TGW/incoming"), Path("/opt/TGW/var")),
    "environment_preflight_receipt": (Path("/opt/TGW/incoming"), Path("/opt/TGW/var")),
    "admission_public_key": (Path("/etc/tgw/trust"),),
    "environment_public_key": (Path("/etc/tgw/trust"),),
    "receipt": (Path("/opt/TGW/receipts"),),
}
_EVIDENCE_ROOTS = (Path("/opt/TGW/incoming"), Path("/opt/TGW/var"), Path("/opt/TGW/receipts"))
_NIX_STORE = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[^/]+\Z")
_BINDING_KEYS = {
    "nixos-prod-switch/v1": {"flake_commit", "flake_tree", "expected_current_system", "target_system", "rollback_system"},
    "nixos-prod-rollback/v1": {"expected_current_system", "target_system"},
    "app-release-install/v1": {"candidate_commit", "candidate_tree", "archive_sha256", "expected_current", "target_generation", "admission_receipt_sha256", "environment_preflight_receipt_sha256"},
    "app-release-rollback/v1": {"expected_current", "target_generation", "source_receipt_sha256"},
}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ProcedureRunnerError("procedure value is not canonical JSON data") from exc


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProcedureRunnerError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProcedureRunnerError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProcedureRunnerError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _signature(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ProcedureRunnerError("deployment approval signature is invalid")
    try:
        raw = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise ProcedureRunnerError("deployment approval signature is invalid") from exc
    if len(raw) != 64 or base64.b64encode(raw).decode() != value:
        raise ProcedureRunnerError("deployment approval signature is invalid")
    return raw


def issue_deployment_approval(
    *, request_hash: str, procedure_id: str, plan_commit: str,
    solution_hash: str, card_hash: str, operator_id: str, signer_key_id: str,
    issued_at: str, expires_at: str, nonce: str,
    signing_private_key: Ed25519PrivateKey | bytes,
) -> dict[str, Any]:
    """Create an operator approval; production keeps this private key offline."""
    if isinstance(signing_private_key, bytes):
        try:
            signing_private_key = Ed25519PrivateKey.from_private_bytes(signing_private_key)
        except ValueError as exc:
            raise ProcedureRunnerError("deployment approval private key is invalid") from exc
    unsigned = {
        "schema": "tgw-deployment-approval/v1", "request_hash": request_hash,
        "procedure_id": procedure_id, "plan_commit": plan_commit,
        "solution_hash": solution_hash, "card_hash": card_hash,
        "operator_id": operator_id, "signer_key_id": signer_key_id,
        "issued_at": issued_at, "expires_at": expires_at, "nonce": nonce,
    }
    approval_hash = _hash(unsigned)
    signature = base64.b64encode(signing_private_key.sign(_canonical({**unsigned, "approval_hash": approval_hash}))).decode()
    return {**unsigned, "approval_hash": approval_hash, "signature": signature}


def _validate_approval(
    value: Any, *, request: Mapping[str, Any], public_key: Ed25519PublicKey,
    signer_key_id: str, now: datetime,
) -> dict[str, Any]:
    fields = {
        "schema", "request_hash", "procedure_id", "plan_commit", "solution_hash",
        "card_hash", "operator_id", "signer_key_id", "issued_at", "expires_at",
        "nonce", "approval_hash", "signature",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != "tgw-deployment-approval/v1":
        raise ProcedureRunnerError("deployment approval fields are invalid")
    approval = dict(value)
    for key in ("request_hash", "solution_hash", "card_hash", "approval_hash"):
        if not isinstance(approval[key], str) or _HASH.fullmatch(approval[key]) is None:
            raise ProcedureRunnerError("deployment approval hash is invalid")
    if (
        approval["request_hash"] != request["request_hash"]
        or approval["procedure_id"] != request["procedure_id"]
        or approval["plan_commit"] != request["plan_commit"]
        or approval["solution_hash"] != request["solution_hash"]
        or approval["card_hash"] != request["card_hash"]
        or approval["signer_key_id"] != signer_key_id
        or not isinstance(approval["operator_id"], str)
        or _ID.fullmatch(approval["operator_id"]) is None
        or not isinstance(approval["nonce"], str)
        or _ID.fullmatch(approval["nonce"]) is None
    ):
        raise ProcedureRunnerError("deployment approval binding is invalid")
    issued, expires = _utc(approval["issued_at"], "approval issued_at"), _utc(approval["expires_at"], "approval expires_at")
    if not issued <= now < expires or (expires - issued).total_seconds() > 900:
        raise ProcedureRunnerError("deployment approval is stale or outside its maximum lifetime")
    unsigned = {key: approval[key] for key in fields - {"approval_hash", "signature"}}
    if approval["approval_hash"] != _hash(unsigned):
        raise ProcedureRunnerError("deployment approval hash is invalid")
    try:
        public_key.verify(_signature(approval["signature"]), _canonical({**unsigned, "approval_hash": approval["approval_hash"]}))
    except InvalidSignature as exc:
        raise ProcedureRunnerError("deployment approval signature is invalid") from exc
    return approval


def compile_procedure_request(
    *, request_id: str, procedure_id: str, registry_revision: str,
    plan_commit: str, solution_hash: str, card_hash: str,
    parameters: Mapping[str, str], precondition_evidence: Mapping[str, Mapping[str, str]],
    bindings: Mapping[str, str], approval: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    unsigned = {
        "schema": "tgw-procedure-request/v1", "request_id": request_id,
        "procedure_id": procedure_id, "registry_revision": registry_revision,
        "plan_commit": plan_commit, "solution_hash": solution_hash,
        "card_hash": card_hash, "parameters": dict(parameters),
        "precondition_evidence": {key: dict(value) for key, value in precondition_evidence.items()},
        "bindings": dict(bindings),
    }
    request_hash = _hash(unsigned)
    return {**unsigned, "request_hash": request_hash, "approval": dict(approval) if approval is not None else None}


def _contained(path: Path, roots: tuple[Path, ...]) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return path.is_absolute() and not path.is_symlink() and any(resolved == root or resolved.is_relative_to(root) for root in roots)


def _argv(procedure: Mapping[str, Any], parameters: Any) -> list[str]:
    if not isinstance(parameters, Mapping) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in parameters.items()):
        raise ProcedureRunnerError("procedure parameters are invalid")
    placeholders = [arg[1:] for arg in procedure["argv"] if _PLACEHOLDER.fullmatch(arg)]
    if set(parameters) != set(placeholders):
        raise ProcedureRunnerError("procedure parameters do not exactly match registered placeholders")
    result: list[str] = []
    for arg in procedure["argv"]:
        if not _PLACEHOLDER.fullmatch(arg):
            result.append(arg)
            continue
        name = arg[1:]
        value = parameters[name]
        if _VALUE.fullmatch(value) is None or ".." in Path(value).parts:
            raise ProcedureRunnerError(f"procedure parameter is unsafe: {name}")
        if name in {"generation", "expected_current"} and (
            value in _RESERVED_GENERATIONS
            or value.startswith(_RESERVED_GENERATION_PREFIXES)
        ):
            raise ProcedureRunnerError(f"procedure parameter is unsafe: {name}")
        roots = _APP_PATH_ROOTS.get(name)
        if roots is not None and not _contained(Path(value), roots):
            raise ProcedureRunnerError(f"procedure path is outside its registered root: {name}")
        result.append(value)
    return result


def _validate_evidence(value: Any, required: list[str]) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != set(required):
        raise ProcedureRunnerError("procedure precondition evidence is incomplete")
    result: dict[str, dict[str, str]] = {}
    for name, pointer in value.items():
        if not isinstance(pointer, Mapping) or set(pointer) != {"path", "sha256"}:
            raise ProcedureRunnerError("procedure precondition evidence pointer is invalid")
        path, digest = Path(str(pointer["path"])), pointer["sha256"]
        if not _contained(path, _EVIDENCE_ROOTS):
            raise ProcedureRunnerError("procedure precondition evidence is outside durable roots")
        if not isinstance(digest, str) or _HASH.fullmatch(digest) is None or _file_hash(path) != digest:
            raise ProcedureRunnerError("procedure precondition evidence hash differs")
        result[str(name)] = {"path": str(path), "sha256": digest}
    return result


def _validate_bindings(procedure_id: str, value: Any, parameters: Mapping[str, str], evidence: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    required = _BINDING_KEYS.get(procedure_id)
    if required is None or not isinstance(value, Mapping) or set(value) != required or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise ProcedureRunnerError("procedure request bindings are invalid")
    bindings = dict(value)
    if procedure_id == "nixos-prod-switch/v1":
        commits_valid = all(_COMMIT.fullmatch(bindings[key]) is not None for key in ("flake_commit", "flake_tree"))
        closures_valid = all(
            _NIX_STORE.fullmatch(bindings[key]) is not None
            for key in ("expected_current_system", "target_system", "rollback_system")
        )
        if not commits_valid or not closures_valid:
            raise ProcedureRunnerError("Nix procedure bindings are invalid")
        if len({bindings["expected_current_system"], bindings["target_system"], bindings["rollback_system"]}) != 3:
            raise ProcedureRunnerError("Nix procedure closure bindings are not distinct")
    elif procedure_id == "nixos-prod-rollback/v1":
        if any(_NIX_STORE.fullmatch(bindings[key]) is None for key in bindings) or bindings["expected_current_system"] == bindings["target_system"]:
            raise ProcedureRunnerError("Nix rollback bindings are invalid")
    elif procedure_id == "app-release-install/v1":
        if (
            bindings["candidate_commit"] != parameters.get("commit")
            or bindings["candidate_tree"] != parameters.get("tree")
            or bindings["archive_sha256"].removeprefix("sha256:") != parameters.get("archive_sha256")
            or bindings["expected_current"] != parameters.get("expected_current")
            or bindings["target_generation"] != parameters.get("generation")
            or bindings["admission_receipt_sha256"] != evidence.get("exact-independent-review-and-admission-receipt-verified", {}).get("sha256")
            or bindings["environment_preflight_receipt_sha256"] != evidence.get("exact-environment-preflight-receipt-verified", {}).get("sha256")
            or any(_COMMIT.fullmatch(bindings[key]) is None for key in ("candidate_commit", "candidate_tree"))
            or any(_HASH.fullmatch(bindings[key]) is None for key in ("archive_sha256", "admission_receipt_sha256", "environment_preflight_receipt_sha256"))
        ):
            raise ProcedureRunnerError("application install bindings differ")
    else:
        if bindings["expected_current"] != parameters.get("expected_current") or _HASH.fullmatch(bindings["source_receipt_sha256"]) is None:
            raise ProcedureRunnerError("application rollback bindings differ")
    return bindings


def validate_procedure_request(
    value: Any, *, registry: Mapping[str, Any], public_key: Ed25519PublicKey,
    signer_key_id: str, now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not isinstance(value, Mapping) or set(value) != _REQUEST_FIELDS or value.get("schema") != "tgw-procedure-request/v1":
        raise ProcedureRunnerError("procedure request fields are invalid")
    request = dict(value)
    if (
        not isinstance(request["request_id"], str) or _ID.fullmatch(request["request_id"]) is None
        or not isinstance(request["plan_commit"], str) or _COMMIT.fullmatch(request["plan_commit"]) is None
        or any(not isinstance(request[key], str) or _HASH.fullmatch(request[key]) is None for key in ("registry_revision", "solution_hash", "card_hash", "request_hash"))
        or request["registry_revision"] != registry["revision"]
    ):
        raise ProcedureRunnerError("procedure request identity is invalid")
    unsigned = {key: request[key] for key in _REQUEST_FIELDS - {"request_hash", "approval"}}
    if request["request_hash"] != _hash(unsigned):
        raise ProcedureRunnerError("procedure request hash is invalid")
    procedure = resolve_procedure(registry, str(request["procedure_id"]))
    argv = _argv(procedure, request["parameters"])
    request["precondition_evidence"] = _validate_evidence(request["precondition_evidence"], procedure["preconditions"])
    request["bindings"] = _validate_bindings(request["procedure_id"], request["bindings"], request["parameters"], request["precondition_evidence"])
    _validate_approval(request["approval"], request=request, public_key=public_key, signer_key_id=signer_key_id, now=now)
    return request, procedure, argv


def _atomic(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    stage = path.with_name(f".{path.name}.next")
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
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


class ProcedureRunner:
    def __init__(
        self, *, registry: Mapping[str, Any], public_key: Ed25519PublicKey,
        signer_key_id: str, receipt_root: Path,
        run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        observe: Callable[[str, str, Mapping[str, Any]], Mapping[str, str]] | None = None,
    ) -> None:
        self.registry, self.public_key, self.signer_key_id = registry, public_key, signer_key_id
        self.receipt_root, self.run, self.clock = receipt_root, run, clock
        self.observe = observe or self._observe
        if not receipt_root.is_dir() or receipt_root.is_symlink():
            raise ProcedureRunnerError("procedure receipt root is unavailable")

    @staticmethod
    def _observe(procedure_id: str, phase: str, request: Mapping[str, Any]) -> Mapping[str, str]:
        if procedure_id == "nixos-prod-switch/v1":
            if phase == "before":
                cwd = "/home/db/tgw-flake"
                commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
                tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
                dirty = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, check=True, capture_output=True, text=True).stdout
                return {"flake_commit": commit, "flake_tree": tree, "worktree": "clean" if not dirty else "dirty", "system": str(Path("/run/current-system").resolve(strict=True))}
            return {"system": str(Path("/run/current-system").resolve(strict=True))}
        if procedure_id == "nixos-prod-rollback/v1":
            return {"system": str(Path("/run/current-system").resolve(strict=True))}
        current = Path("/opt/TGW/current")
        target = os.readlink(current) if current.is_symlink() else "none"
        return {"generation": target.removeprefix("releases/")}

    @staticmethod
    def _check_observation(procedure_id: str, phase: str, request: Mapping[str, Any], observed: Mapping[str, str]) -> None:
        bindings = request["bindings"]
        if procedure_id == "nixos-prod-switch/v1":
            expected = (
                {
                    "flake_commit": bindings["flake_commit"],
                    "flake_tree": bindings["flake_tree"],
                    "worktree": "clean",
                    "system": bindings["expected_current_system"],
                }
                if phase == "before"
                else {"system": bindings["target_system"]}
            )
        elif procedure_id == "nixos-prod-rollback/v1":
            expected = {"system": bindings["expected_current_system"] if phase == "before" else bindings["target_system"]}
        else:
            expected = {"generation": bindings["expected_current"] if phase == "before" else bindings["target_generation"]}
        if dict(observed) != expected:
            raise ProcedureRunnerError(f"procedure {phase} observation differs from exact bindings")

    def execute(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        request_id = raw.get("request_id") if isinstance(raw, Mapping) else None
        if not isinstance(request_id, str) or _ID.fullmatch(request_id) is None:
            raise ProcedureRunnerError("procedure request identity is invalid")
        receipt_path = self.receipt_root / f"{request_id}.json"
        refusal_path = self.receipt_root / f"{request_id}.refusal.json"
        if receipt_path.exists() or refusal_path.exists():
            raise ProcedureRunnerError("procedure request is a replay")
        try:
            request, procedure, argv = validate_procedure_request(
                raw, registry=self.registry, public_key=self.public_key,
                signer_key_id=self.signer_key_id, now=self.clock(),
            )
        except ProcedureRunnerError as exc:
            refusal = {"schema": "tgw-procedure-refusal/v1", "request_id": request_id, "status": "REFUSED", "reason": str(exc)}
            refusal = {**refusal, "receipt_hash": _hash(refusal)}
            _atomic(refusal_path, refusal)
            raise
        prepared = {
            "schema": "tgw-procedure-receipt/v1", "request_id": request_id,
            "request_hash": request["request_hash"], "procedure_id": request["procedure_id"],
            "registry_revision": request["registry_revision"], "plan_commit": request["plan_commit"],
            "solution_hash": request["solution_hash"], "card_hash": request["card_hash"],
            "approval_hash": request["approval"]["approval_hash"], "state": "prepared",
            "argv_sha256": "sha256:" + hashlib.sha256(b"\0".join(item.encode() for item in argv)).hexdigest(),
            "precondition_evidence": request["precondition_evidence"],
        }
        prepared = {**prepared, "receipt_hash": _hash(prepared)}
        _atomic(receipt_path, prepared)
        try:
            before = dict(self.observe(request["procedure_id"], "before", request))
            self._check_observation(request["procedure_id"], "before", request, before)
            if request["procedure_id"] == "nixos-prod-switch/v1":
                dry_argv = [argv[0], "dry-activate", *argv[2:]]
                dry = self.run(
                    dry_argv, cwd=procedure["working_directory"], check=False,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800,
                    env={"PATH": "/run/current-system/sw/bin:/run/wrappers/bin", "LANG": "C", "LC_ALL": "C"},
                )
                if dry.returncode != 0:
                    raise ProcedureRunnerError("registered Nix dry activation failed")
            result = self.run(
                argv, cwd=procedure["working_directory"], check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800,
                env={"PATH": "/run/current-system/sw/bin:/run/wrappers/bin", "LANG": "C", "LC_ALL": "C"},
            )
            after = dict(self.observe(request["procedure_id"], "after", request))
            if result.returncode == 0:
                self._check_observation(request["procedure_id"], "after", request, after)
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError, ProcedureRunnerError) as exc:
            failed = {**prepared, "state": "ambiguous", "failure": type(exc).__name__}
            failed.pop("receipt_hash")
            failed = {**failed, "receipt_hash": _hash(failed)}
            _atomic(receipt_path, failed)
            raise ProcedureRunnerError("procedure execution is ambiguous") from exc
        stdout, stderr = bytes(result.stdout), bytes(result.stderr)
        completed = {
            **prepared, "state": "completed" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "stdout_sha256": "sha256:" + hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": "sha256:" + hashlib.sha256(stderr).hexdigest(),
            "output_complete": len(stdout) <= 4 * 1024 * 1024 and len(stderr) <= 1024 * 1024,
            "before": before, "after": after,
        }
        completed.pop("receipt_hash")
        completed = {**completed, "receipt_hash": _hash(completed)}
        _atomic(receipt_path, completed)
        if result.returncode != 0 or not completed["output_complete"]:
            raise ProcedureRunnerError("registered procedure failed")
        return completed


def _protected_file(path: Path, *, owner: int = 0, modes: set[int]) -> bytes:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) not in modes:
        raise ProcedureRunnerError(f"protected runner file is invalid: {path}")
    return path.read_bytes()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw-procedure-runner")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--approval-public-key", type=Path, required=True)
    parser.add_argument("--approval-key-id", required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--expected-host", default="tgw-prod")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0 or socket.gethostname() != args.expected_host:
            raise ProcedureRunnerError("procedure runner requires its exact production root identity")
        _protected_file(args.registry, modes={0o444})
        public_raw = _protected_file(args.approval_public_key, modes={0o444})
        if len(public_raw) != 32:
            raise ProcedureRunnerError("deployment approval public key is invalid")
        request = json.loads(_protected_file(args.request, owner=os.getuid(), modes={0o400, 0o600}))
        runner = ProcedureRunner(
            registry=load_procedure_registry(args.registry),
            public_key=Ed25519PublicKey.from_public_bytes(public_raw),
            signer_key_id=args.approval_key_id, receipt_root=args.receipt_root,
        )
        result = runner.execute(request)
    except (OSError, json.JSONDecodeError, ProcedureRunnerError, ValueError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 2
    print(_canonical(result).decode())
    return 0

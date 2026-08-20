"""Harness adapters for provider-neutral governed development role cards.

The card names a durable role and a selected provider.  This module is only a
receiver adapter for two admitted provider implementations: an isolated Codex
process and the native deterministic controller.  It never chooses a role or
provider and it cannot deploy or commit a candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    HTTPRegisteredResourceResolver,
    ResourceVerificationError,
)


class GovernedRoleRunnerError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _verified_handoff(
    value: Mapping[str, Any], *, provider: str,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any], str]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "card", "resource_receipt", "resource_service", "instruction", "receipt", "handoff_hash",
    }:
        raise GovernedRoleRunnerError("governed role handoff fields are invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("handoff_hash")
    if claimed != _hash(unsigned):
        raise GovernedRoleRunnerError("governed role handoff hash is invalid")
    card = value.get("card")
    if not isinstance(card, Mapping) or card.get("selected_provider") != provider:
        raise GovernedRoleRunnerError("governed role provider differs from the selected card provider")
    card_unsigned = dict(card)
    card_hash = card_unsigned.pop("card_hash", None)
    if card_hash != _hash(card_unsigned):
        raise GovernedRoleRunnerError("governed role card hash is invalid")
    bindings = card.get("bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != CARD_RESOURCE_NAMES:
        raise GovernedRoleRunnerError("governed role card resources are incomplete")
    receipt = value.get("resource_receipt")
    if not isinstance(receipt, Mapping) or receipt.get("card_hash") != card_hash:
        raise GovernedRoleRunnerError("governed role resource receipt is invalid")
    receipt_unsigned = dict(receipt)
    receipt_hash = receipt_unsigned.pop("receipt_hash", None)
    if receipt_hash != _hash(receipt_unsigned):
        raise GovernedRoleRunnerError("governed role resource receipt hash is invalid")
    descriptor = value.get("resource_service")
    if not isinstance(descriptor, Mapping):
        raise GovernedRoleRunnerError("governed role resource service is invalid")
    promptcraft_receipt = value.get("receipt")
    instruction = value.get("instruction")
    if not isinstance(promptcraft_receipt, Mapping) or not isinstance(instruction, str) or not instruction:
        raise GovernedRoleRunnerError("governed role Promptcraft binding is invalid")
    promptcraft_unsigned = dict(promptcraft_receipt)
    promptcraft_hash = promptcraft_unsigned.pop("receipt_hash", None)
    if (
        promptcraft_hash != _hash(promptcraft_unsigned)
        or promptcraft_receipt.get("card_hash") != card_hash
        or promptcraft_receipt.get("resource_receipt_hash") != receipt_hash
        or promptcraft_receipt.get("rendered_instruction_hash")
        != "sha256:" + hashlib.sha256(instruction.encode()).hexdigest()
        or not isinstance(promptcraft_receipt.get("receiver_identity"), str)
        or not promptcraft_receipt["receiver_identity"]
    ):
        raise GovernedRoleRunnerError("governed role Promptcraft receipt is invalid")
    try:
        resolver = HTTPRegisteredResourceResolver.from_descriptor(descriptor)
        run = resolver.begin_harness_run(
            card_hash=card_hash,
            role=str(card["role"]),
            execution_identity=str(promptcraft_receipt["receiver_identity"]),
            handoff_hash=str(value["handoff_hash"]),
            resource_receipt_hash=str(receipt_hash),
            resources=bindings,
        )
        scoped = resolver.for_harness_run(run)
        resources: dict[str, bytes] = {}
        for name in sorted(CARD_RESOURCE_NAMES):
            resource = scoped.fetch(str(bindings[name]["ref"]))
            content = resource.value
            if not isinstance(content, bytes) or resource.content_hash() != bindings[name]["hash"]:
                raise ResourceVerificationError(f"registered resource mismatch: {name}")
            resources[name] = content
        attestation = resolver.complete_harness_run(run)
    except ResourceVerificationError as exc:
        raise GovernedRoleRunnerError(str(exc)) from exc
    return dict(card), resources, attestation, instruction


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=False, text=True, capture_output=True)
    if result.returncode:
        raise GovernedRoleRunnerError(f"governed role Git probe failed: {result.stderr[-400:]}")
    return result.stdout.strip()


def _codex_binary() -> str:
    configured = os.environ.get("TGW_CODEX_BIN")
    candidate = Path(configured) if configured else Path.home() / ".local/bin/codex"
    if candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    resolved = shutil.which("codex")
    if not resolved:
        raise GovernedRoleRunnerError("selected Codex provider executable is unavailable")
    return str(Path(resolved).resolve())


def _codex(
    *, cwd: Path, prompt: str, schema: Mapping[str, Any], read_only: bool,
) -> tuple[subprocess.CompletedProcess[str], Mapping[str, Any] | None]:
    with tempfile.TemporaryDirectory(prefix="tgw-governed-codex-") as temporary:
        root = Path(temporary)
        codex_home = root / "codex-home"
        codex_home.mkdir(mode=0o700)
        source_auth = Path.home() / ".codex/auth.json"
        if not source_auth.is_file():
            raise GovernedRoleRunnerError("selected Codex provider credential is unavailable")
        shutil.copyfile(source_auth, codex_home / "auth.json")
        (codex_home / "auth.json").chmod(0o600)
        schema_path, result_path = root / "schema.json", root / "result.json"
        schema_path.write_text(json.dumps(schema, sort_keys=True), encoding="utf-8")
        command = [
            _codex_binary(), "exec", "--ephemeral", "--ignore-user-config",
            "--sandbox", "read-only" if read_only else "workspace-write",
            "-C", str(cwd), "--output-schema", str(schema_path), "-o", str(result_path), "-",
        ]
        completed = subprocess.run(
            command, cwd=cwd, input=prompt, text=True, capture_output=True, check=False,
            env={**os.environ, "CODEX_HOME": str(codex_home)},
        )
        try:
            result = json.loads(result_path.read_text(encoding="utf-8")) if completed.returncode == 0 else None
        except (OSError, json.JSONDecodeError):
            result = None
        return completed, result if isinstance(result, Mapping) else None


def _intent(resources: Mapping[str, bytes]) -> Mapping[str, Any]:
    try:
        value = json.loads(resources["plan_input"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GovernedRoleRunnerError("registered Plan input is invalid") from exc
    if not isinstance(value, Mapping):
        raise GovernedRoleRunnerError("registered Plan input is invalid")
    return value


def _codex_role(
    card: Mapping[str, Any], resources: Mapping[str, bytes], cwd: Path, instruction: str,
) -> dict[str, Any]:
    role = card.get("role")
    intent = _intent(resources)
    request = intent.get("request") if isinstance(intent.get("request"), Mapping) else {}
    if role == "implementation":
        schema = {
            "type": "object", "additionalProperties": False,
            "required": ["status", "summary", "tests"],
            "properties": {
                "status": {"enum": ["implemented", "blocked"]},
                "summary": {"type": "string", "minLength": 1},
                "tests": {"type": "array", "items": {"type": "string"}},
            },
        }
        prompt = f"""You are the selected implementation provider for a TGW governed role card.
Work only in the current request-bound worktree. Do not commit, deploy, change
configuration, contact production, or broaden the card authority.

Original request: {request.get('original_request', '')}
Scope: {request.get('scope', '')}
Constraints: {json.dumps(request.get('constraints', []))}
Card instruction:\n{card.get('card_id')}\n
Canonical Promptcraft instruction:\n{instruction}\n
Implement the bounded request, run proportionate tests, and return the required JSON.
"""
        before_head = _git(cwd, "rev-parse", "HEAD")
        before_status = _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
        completed, report = _codex(cwd=cwd, prompt=prompt, schema=schema, read_only=False)
        after_head = _git(cwd, "rev-parse", "HEAD")
        after_status = _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
        changed = after_status != before_status and bool(after_status)
        valid = (
            completed.returncode == 0 and report is not None
            and report.get("status") == "implemented" and changed and before_head == after_head
        )
        return {
            "outcome": "satisfied" if valid else "failed",
            "established_conditions": ["implemented"] if valid else [],
            "artifacts": [{"kind": "provider-report", "report": dict(report or {})}, {"kind": "git-diff", "detail": _git(cwd, "diff", "--stat")}],
        }
    if role == "independent-review":
        schema = {
            "type": "object", "additionalProperties": False,
            "required": ["verdict", "summary", "findings"],
            "properties": {
                "verdict": {"enum": ["PASS", "FAIL"]},
                "summary": {"type": "string", "minLength": 1},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
        }
        prompt = f"""Independently review the exact candidate in this read-only worktree
against the registered request below. Do not edit, commit, deploy, or infer
authority from prior agent claims. Inspect source and run bounded read-only
checks as useful. Return PASS only if the request is actually satisfied.

Original request: {request.get('original_request', '')}
Scope: {request.get('scope', '')}
Constraints: {json.dumps(request.get('constraints', []))}
Canonical Promptcraft instruction:\n{instruction}\n
"""
        before = _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
        completed, report = _codex(cwd=cwd, prompt=prompt, schema=schema, read_only=True)
        unchanged = _git(cwd, "status", "--porcelain=v1", "--untracked-files=all") == before
        passed = completed.returncode == 0 and report is not None and report.get("verdict") == "PASS" and unchanged
        return {
            "outcome": "satisfied" if passed else "failed",
            "established_conditions": ["reviewed"] if passed else [],
            "artifacts": [{"kind": "independent-review", "report": dict(report or {})}],
        }
    raise GovernedRoleRunnerError("selected Codex provider does not implement this role")


def _controller_role(card: Mapping[str, Any], cwd: Path) -> dict[str, Any]:
    if card.get("role") != "controller-verification":
        raise GovernedRoleRunnerError("native controller received another role")
    artifacts: list[dict[str, Any]] = []
    commands = (
        ("pytest", [os.environ.get("TGW_CONTROLLER_PYTHON", os.sys.executable), "-m", "pytest", "-q"]),
        ("ruff", [os.environ.get("TGW_CONTROLLER_PYTHON", os.sys.executable), "-m", "ruff", "check", "."]),
    )
    for name, command in commands:
        completed = subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=True)
        artifacts.append({
            "kind": "check", "name": name,
            "status": "passed" if completed.returncode == 0 else "failed",
            "detail": "" if completed.returncode == 0 else (completed.stderr or completed.stdout)[-1000:],
        })
        if completed.returncode:
            return {"outcome": "failed", "established_conditions": [], "artifacts": artifacts}
    return {
        "outcome": "satisfied",
        "established_conditions": ["tested", "linted", "controller_verified"],
        "artifacts": artifacts,
    }


def run(handoff: Mapping[str, Any], *, provider: str, cwd: Path) -> dict[str, Any]:
    card, resources, attestation, instruction = _verified_handoff(handoff, provider=provider)
    if provider in {"codex-local-runner", "codex-isolated-review-runner"}:
        result = _codex_role(card, resources, cwd, instruction)
    elif provider == "controller-local-runner":
        result = _controller_role(card, cwd)
    else:
        raise GovernedRoleRunnerError("provider adapter is not registered")
    return {
        **result,
        "resource_receipt_hash": handoff["resource_receipt"]["receipt_hash"],
        "resource_retrieval_attestation": attestation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw-governed-role-runner")
    parser.add_argument("--provider", required=True)
    args = parser.parse_args(argv)
    try:
        handoff = json.load(os.sys.stdin)
        if not isinstance(handoff, Mapping):
            raise GovernedRoleRunnerError("governed role runner input is invalid")
        result = run(handoff, provider=args.provider, cwd=Path.cwd())
    except Exception as exc:
        result = {
            "outcome": "failed", "established_conditions": [],
            "artifacts": [{"kind": "runner-failure", "detail": str(exc)}],
            "resource_receipt_hash": None, "resource_retrieval_attestation": None,
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

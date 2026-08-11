"""Read-only, requirement-by-requirement TGW environment recovery audit."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from tgw.agent_contract import load_agent_contract
from tgw.environment_registry import load_registry
from tgw.procedure_registry import load_procedure_registry
from tgw.satellite_evidence import validate_satellite_evidence_package
from tgw.satellite_review import validate_satellite_review
from tgw.task_context import load_task, resolve_task_context


class RecoveryAcceptanceError(ValueError):
    """The acceptance audit inputs are malformed or internally contradictory."""


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryAcceptanceError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RecoveryAcceptanceError(f"artifact is not a mapping: {path}")
    return value


def _timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise RecoveryAcceptanceError("observed_at must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecoveryAcceptanceError("observed_at must be a timezone-aware timestamp")


def _check(check_id: str, evidence: str, verifier: Callable[[], str]) -> dict[str, str]:
    try:
        detail = verifier()
    except FileNotFoundError:
        return {"id": check_id, "status": "missing", "evidence": evidence, "detail": "required artifact is absent"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"id": check_id, "status": "failed", "evidence": evidence, "detail": str(exc)}
    return {"id": check_id, "status": "proved", "evidence": evidence, "detail": detail}


def audit_environment_recovery(root: Path, *, observed_at: str) -> dict[str, Any]:
    """Audit the full program without mutating, contacting, or executing anything."""
    _timestamp(observed_at)
    root = root.resolve(strict=True)
    reports = root / "docs/TGW-Plan-Vault/reports/environment-recovery"
    registry_path = root / "config/environment/registry.yaml"
    contract_path = root / "config/environment/actors/tgw-steward.json"

    def registry_check() -> str:
        registry = load_registry(registry_path)
        content = registry["content"]
        if set(content["hosts"]) != {"production", "development"}:
            raise RecoveryAcceptanceError("current host roles are incomplete")
        if content["hosts"]["production"]["canonical_name"] != "tgw-prod" or content["hosts"]["development"]["canonical_name"] != "tgw-lib":
            raise RecoveryAcceptanceError("current canonical host names are wrong")
        if "a1131" not in content["retired_hosts"] or content["retired_hosts"]["a1131"]["behavior"] != "fail":
            raise RecoveryAcceptanceError("retired host refusal is missing")
        return registry["revision"]

    def task_context_check() -> str:
        registry = load_registry(registry_path)
        task = load_task(root / "config/environment/tasks/environment-recovery.json")
        actual = resolve_task_context(task, registry)
        expected = _json(root / "config/environment/resolved/environment-recovery.codex.json")
        if actual != expected:
            raise RecoveryAcceptanceError("resolved task context is not reproducible")
        return actual["context_id"]

    def steward_check() -> str:
        contract = load_agent_contract(contract_path)
        verification = _json(reports / "steward-context-verification-20260811.json")
        if verification.get("result") != "verified" or verification.get("history_lookup") != "disabled":
            raise RecoveryAcceptanceError("clean steward canary is not verified with history disabled")
        if contract["effects"]["production"] != "none" or contract["satellite_runtime_dependency"] is not False:
            raise RecoveryAcceptanceError("clean steward effect or satellite boundary is unsafe")
        return "clean contract and cited current-context canary verified"

    def procedures_check() -> str:
        procedures = load_procedure_registry(root / "config/environment/procedures.json")
        verification = _json(reports / "procedure-registry-verification-v4-20260811.json")
        if verification.get("direct_mutable_deploy_findings") != 0 or verification.get("deployment_executed") is not False:
            raise RecoveryAcceptanceError("procedure migration evidence is incomplete or effectful")
        held = sorted(
            procedure_id
            for procedure_id, procedure in procedures["procedures"].items()
            if procedure["status"] != "active"
        )
        if held:
            raise RecoveryAcceptanceError(f"required procedures remain held: {', '.join(held)}")
        return procedures["revision"]

    server_checks = [
        _check("server-registry-current", "config/environment/registry.yaml", registry_check),
        _check("task-context-reproducible", "config/environment/resolved/environment-recovery.codex.json", task_context_check),
        _check("clean-steward-boundary", "docs/TGW-Plan-Vault/reports/environment-recovery/steward-context-verification-20260811.json", steward_check),
        _check("registered-procedures", "config/environment/procedures.json", procedures_check),
    ]

    satellite_checks: list[dict[str, str]] = []
    for host in ("catnanny", "helicrew"):
        directory = reports / "satellites" / host
        manifest_path = directory / "manifest.json"
        review_path = directory / "review.json"
        disposition_path = directory / "machine-disposition.json"

        def manifest_check(path: Path = manifest_path, expected_host: str = host) -> str:
            package = validate_satellite_evidence_package(_json(path))
            if package["source_host"] != expected_host:
                raise RecoveryAcceptanceError("satellite package host binding mismatch")
            return package["package_id"]

        def review_check(manifest: Path = manifest_path, review: Path = review_path) -> str:
            validated = validate_satellite_review(_json(review), _json(manifest))
            return validated["review_id"]

        def disposition_check(path: Path = disposition_path, expected_host: str = host) -> str:
            value = _json(path)
            required = {
                "schema", "source_host", "decision", "human_authority_id",
                "decided_at", "evidence_package_id", "destructive_action_authorized",
            }
            if set(value) != required or value.get("schema") != "tgw-satellite-machine-disposition/v1":
                raise RecoveryAcceptanceError("machine disposition contract is invalid")
            if value.get("source_host") != expected_host or value.get("decision") not in {"retain-offline", "sanitize-rebuild", "dispose"}:
                raise RecoveryAcceptanceError("machine disposition host or decision is invalid")
            if not str(value.get("human_authority_id", "")).startswith("human:"):
                raise RecoveryAcceptanceError("machine disposition requires human authority")
            _timestamp(value["decided_at"])
            if not isinstance(value.get("evidence_package_id"), str) or not value["evidence_package_id"].startswith("sha256:"):
                raise RecoveryAcceptanceError("machine disposition evidence binding is invalid")
            if value.get("destructive_action_authorized") is not False:
                raise RecoveryAcceptanceError("recovery disposition cannot itself authorize destruction")
            return value["decision"]

        satellite_checks.extend([
            _check(f"{host}-evidence-package", manifest_path.relative_to(root).as_posix(), manifest_check),
            _check(f"{host}-review-complete", review_path.relative_to(root).as_posix(), review_check),
            _check(f"{host}-human-machine-disposition", disposition_path.relative_to(root).as_posix(), disposition_check),
        ])

    def final_acceptance_check() -> str:
        value = _json(reports / "final-human-acceptance.json")
        required = {"schema", "plan_id", "candidate_id", "human_authority_id", "accepted_at", "accepted"}
        if set(value) != required or value.get("schema") != "tgw-environment-recovery-human-acceptance/v1":
            raise RecoveryAcceptanceError("final human acceptance contract is invalid")
        if value.get("plan_id") != "PLAN-environment-cleanup-program" or value.get("accepted") is not True:
            raise RecoveryAcceptanceError("final program acceptance is not affirmative")
        if not str(value.get("human_authority_id", "")).startswith("human:"):
            raise RecoveryAcceptanceError("final acceptance requires human authority")
        _timestamp(value["accepted_at"])
        return value["candidate_id"]

    final_check = _check(
        "human-final-acceptance",
        "docs/TGW-Plan-Vault/reports/environment-recovery/final-human-acceptance.json",
        final_acceptance_check,
    )
    checks = [*server_checks, *satellite_checks, final_check]
    counts = {status: sum(item["status"] == status for item in checks) for status in ("proved", "missing", "failed")}
    ready_for_human_acceptance = all(item["status"] == "proved" for item in [*server_checks, *satellite_checks])
    complete = ready_for_human_acceptance and final_check["status"] == "proved"
    return {
        "schema": "tgw-environment-recovery-acceptance-audit/v1",
        "observed_at": observed_at,
        "program_plan_id": "PLAN-environment-cleanup-program",
        "checks": checks,
        "counts": counts,
        "ready_for_human_acceptance": ready_for_human_acceptance,
        "complete": complete,
        "external_actions_performed": False,
        "history_or_memory_granted_authority": False,
    }

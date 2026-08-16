"""Execute one hash-bound integrated candidate review without installing it."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.candidate_review import (
    create_review_result,
    generate_review_packet,
    validate_review_report,
    validate_review_result,
)
from tgw.execution_resources import HTTPRegisteredResourceResolver
from tgw.qualified_execution_service import QualifiedExecutionClient
from tgw.governed_coding import dispatch_role
from tgw.harness_registry import load_registry, observe_health
from tgw.review_configuration import configured_review_command
from tgw.review_runner import snapshot_hash

REVIEW_LEASE_SECONDS = 15 * 60


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _extract_archive(data: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if destination.resolve() not in (target, *target.parents):
                raise ValueError("candidate archive contains an escaping path")
            if member.issym() or member.islnk():
                raise ValueError("candidate review archive cannot contain links")
        archive.extractall(destination, filter="data")


def _review_lease(source_commit: str, *, observed_at: datetime) -> dict[str, str]:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("candidate review observation time must include a timezone")
    issued = observed_at.astimezone(timezone.utc)
    expires = issued + timedelta(seconds=REVIEW_LEASE_SECONDS)
    issued_text = issued.isoformat(timespec="microseconds").replace("+00:00", "Z")
    expires_text = expires.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return {
        "id": f"review:{source_commit}:{_hash(issued_text)[7:19]}",
        "expires_at": expires_text,
        "stop_policy": "hold",
    }


def _card(
    manifest: Mapping[str, Any],
    packet: Mapping[str, Any],
    *,
    observed_at: datetime,
    candidate_evidence_binding: Mapping[str, Any],
    receipt_sink_binding: Mapping[str, Any],
    codegraph_binding: Mapping[str, Any],
    execution_environment_binding: Mapping[str, Any],
    review_input_binding: Mapping[str, Any],
) -> dict[str, Any]:
    source = manifest["source"]
    plan = manifest["plan"]
    for label, binding in (
        ("candidate evidence", candidate_evidence_binding),
        ("execution receipt sink", receipt_sink_binding),
        ("CodeGraph", codegraph_binding),
        ("execution environment", execution_environment_binding),
        ("qualified review input", review_input_binding),
    ):
        if not isinstance(binding, Mapping) or set(binding) != {"ref", "hash"}:
            raise ValueError(f"candidate review {label} binding is invalid")
        if not isinstance(binding["ref"], str) or not binding["ref"] or not isinstance(binding["hash"], str) or not binding["hash"].startswith("sha256:"):
            raise ValueError(f"candidate review {label} binding is invalid")
    if codegraph_binding["ref"] == packet["snapshot"]["ref"] or codegraph_binding["hash"] == source["archive_sha256"]:
        raise ValueError("candidate review CodeGraph cannot be substituted by the source snapshot")
    if execution_environment_binding["hash"] == _hash(packet["runner_argv"]):
        raise ValueError("candidate review environment cannot be substituted by runner argv")
    return {
        "card_id": "candidate-review-" + str(source["commit"])[:12],
        "solution_id": plan["solution_hash"],
        "plan_commit": plan["commit"],
        "bindings": {
            "plan_input": dict(review_input_binding),
            "plan_commit": {"ref": "git-plan:" + plan["commit"], "hash": _hash({"commit": plan["commit"]})},
            "plan_graph": {"ref": "tgw-plan:solution", "hash": plan["solution_hash"]},
            "codegraph_snapshot": dict(codegraph_binding),
            "source_tree": dict(packet["snapshot"]),
            "execution_environment": dict(execution_environment_binding),
            "authority_conditions": {"ref": "tgw-plan:closure", "hash": plan["closure_hash"]},
            "candidate_evidence": dict(candidate_evidence_binding),
            "receipt_sink": dict(receipt_sink_binding),
        },
        "authority": ["read-only semantic and security review of the bound snapshot"],
        "exclusions": ["source mutation", "deployment", "installation", "authority broadening"],
        "acceptance": ["validated semantic and security verdict bound to the candidate"],
        "lease": _review_lease(str(source["commit"]), observed_at=observed_at),
    }


def execute(
    manifest_path: Path,
    repository: Path,
    *,
    observed_at: datetime | None = None,
    candidate_evidence_binding: Mapping[str, Any],
    receipt_sink_binding: Mapping[str, Any],
    resource_resolver: HTTPRegisteredResourceResolver,
    resource_service: Mapping[str, Any],
    resource_service_catalog: Mapping[str, Any],
    qualified_execution_client: QualifiedExecutionClient,
    qualified_review_profile: str,
    base_commit: str,
    base_tree: str,
    publish_review_input: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    codegraph_binding: Mapping[str, Any],
    execution_environment_binding: Mapping[str, Any],
) -> dict[str, Any]:
    execution_observed_at = observed_at or datetime.now(timezone.utc)
    if execution_observed_at.tzinfo is None or execution_observed_at.utcoffset() is None:
        raise ValueError("candidate review observation time must include a timezone")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = subprocess.run(
        ["git", "-c", f"safe.directory={repository}", "archive", manifest["source"]["commit"]],
        cwd=repository,
        capture_output=True,
        check=True,
    ).stdout
    if "sha256:" + hashlib.sha256(archive).hexdigest() != manifest["source"]["archive_sha256"]:
        raise ValueError("candidate git archive hash does not match manifest")
    configuration = configured_review_command()
    if configuration["status"] != "AVAILABLE":
        raise ValueError("isolated review backend is unavailable")
    adapters = {
        "tgw-plan": repository / "agent-services/skills/tgw-plan",
        "promptcraft": repository / "agent-services/providers/promptcraft",
        "promptcraft-card-handoff": repository / "agent-services/providers/promptcraft/bin/promptcraft-handoff",
    }
    registry = load_registry(repository / "agent-services/catalogs/harness-providers-v1.json")
    health = observe_health(
        registry,
        coding_config={"commands": {"harness-review": configuration["command"]}},
        adapters=adapters,
    )
    with tempfile.TemporaryDirectory(prefix="tgw-candidate-review-") as temporary:
        snapshot = Path(temporary) / "snapshot"
        snapshot.mkdir()
        _extract_archive(archive, snapshot)
        packet = generate_review_packet(
            manifest,
            registry,
            health,
            adapters=adapters,
            snapshot_ref=snapshot.resolve().as_uri(),
            snapshot_hash=snapshot_hash(snapshot),
        )
        if packet["status"] != "EXECUTABLE":
            return {"packet": packet, "result": None, "validation": None}
        qualified = qualified_execution_client.execute(
            candidate_commit=str(manifest["source"]["commit"]),
            candidate_tree=str(manifest["source"]["tree"]),
            base_commit=base_commit,
            base_tree=base_tree,
            plan_commit=str(manifest["plan"]["commit"]),
            profiles=[qualified_review_profile],
            review_packet=packet,
        )
        results = qualified.get("results")
        if not isinstance(results, list) or len(results) != 1:
            raise ValueError("qualified review did not return exactly one signed report")
        qualified_result = results[0]
        if not isinstance(qualified_result, Mapping) or not all(
            isinstance(qualified_result.get(name), Mapping)
            for name in ("review_report", "proof", "transcript")
        ):
            raise ValueError("qualified review report/proof/transcript is incomplete")
        report = validate_review_report(packet, qualified_result["review_report"])
        proof_hash = qualified_result["proof"].get("proof_hash")
        if not isinstance(proof_hash, str) or not proof_hash.startswith("sha256:"):
            raise ValueError("qualified review proof identity is invalid")
        review_input = {
            "schema": "tgw-qualified-review-governed-input/v1",
            "packet_hash": packet["packet_hash"],
            "review_report_hash": report["report_hash"],
            "qualified_execution_proof_hash": proof_hash,
        }
        review_input_binding = publish_review_input(review_input)
        receipt = dispatch_role(
            registry,
            health,
            role="independent-review",
            adapters=adapters,
            card_template=_card(
                manifest, packet, observed_at=execution_observed_at,
                candidate_evidence_binding=candidate_evidence_binding,
                receipt_sink_binding=receipt_sink_binding,
                codegraph_binding=codegraph_binding,
                execution_environment_binding=execution_environment_binding,
                review_input_binding=review_input_binding,
            ),
            execution_identity="isolated-review:" + str(manifest["source"]["commit"]),
            required_capabilities=("isolated-snapshot-review",),
            resource_resolver=resource_resolver,
            resource_service=resource_service,
            resource_service_catalog=resource_service_catalog,
        )
    if receipt.get("selected_provider") != packet["selected_provider"]:
        raise ValueError("governed review receipt provider differs from qualified report")
    if not any(
        artifact == {"kind": "qualified_review_report", "report_hash": report["report_hash"]}
        for artifact in receipt.get("artifacts", [])
    ):
        raise ValueError("governed review receipt does not bind the qualified runner report")
    result = create_review_result(
        packet, report, receipt,
        qualified_execution_proof_hash=proof_hash,
    )
    return {
        "packet": packet,
        "report": report,
        "result": result,
        "validation": validate_review_result(packet, report, result),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    parser.error(
        "candidate review requires externally pinned QES, D, resource-service, CodeGraph, "
        "environment, and X publisher bindings; the uninstalled template is a HOLD"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

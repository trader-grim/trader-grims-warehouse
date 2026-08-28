"""Hash-bound independent semantic/security review packets for candidates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from tgw.governed_coding import validate_receipt
from tgw.harness_registry import ProviderHealth, select_provider

PACKET_SCHEMA = "tgw-integrated-candidate-review-packet/v1"
REPORT_SCHEMA = "tgw-integrated-candidate-review-report/v1"
RESULT_SCHEMA = "tgw-integrated-candidate-review-result/v2"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")


class CandidateReviewError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def candidate_identity(manifest: Mapping[str, Any]) -> str:
    if manifest.get("schema") != "tgw-integrated-candidate-manifest/v1":
        raise CandidateReviewError("candidate manifest schema is invalid")
    source = manifest.get("source")
    plan = manifest.get("plan")
    if not isinstance(source, Mapping) or not isinstance(plan, Mapping):
        raise CandidateReviewError("candidate source and Plan bindings are required")
    if not _GIT.fullmatch(str(source.get("commit", ""))) or not _GIT.fullmatch(
        str(source.get("tree", ""))
    ):
        raise CandidateReviewError("candidate source commit/tree is invalid")
    if not _SHA256.fullmatch(str(source.get("archive_sha256", ""))):
        raise CandidateReviewError("candidate archive hash is invalid")
    if not isinstance(plan.get("commit"), str) or not plan["commit"]:
        raise CandidateReviewError("candidate Plan commit is required")
    for field in ("solution_hash", "closure_hash"):
        if not _SHA256.fullmatch(str(plan.get(field, ""))):
            raise CandidateReviewError(f"candidate Plan {field} is invalid")
    if manifest.get("candidate_closed") is not True or manifest.get("installed") is not False:
        raise CandidateReviewError("review requires a closed, uninstalled candidate")
    claimed = manifest.get("manifest_hash")
    if claimed is not None:
        unsigned = dict(manifest)
        unsigned.pop("manifest_hash")
        if claimed != _hash(unsigned):
            raise CandidateReviewError("candidate manifest hash mismatch")
        return str(claimed)
    return _hash(manifest)


def generate_review_packet(
    manifest: Mapping[str, Any],
    registry: Mapping[str, Any],
    health: Mapping[str, ProviderHealth],
    *,
    adapters: Mapping[str, str | Path],
    snapshot_ref: str,
    snapshot_hash: str,
    independent_from: Sequence[str] = (),
    required_capabilities: Sequence[str] = ("isolated-snapshot-review",),
) -> dict[str, Any]:
    """Generate an executable packet or an exact provider-selection HOLD."""

    selection = select_provider(
        registry,
        health,
        role="independent-review",
        adapters=adapters,
        required_capabilities=required_capabilities,
        independent_from=independent_from,
    )
    executable = selection["status"] == "SELECTED"
    return build_executable_review_packet(
        manifest,
        snapshot_ref=snapshot_ref,
        snapshot_hash=snapshot_hash,
        selected_provider=selection.get("selected_provider"),
        receiver_profile=selection.get("receiver_profile"),
        runner_argv=selection.get("runner_argv", []),
        hold=(
            None
            if executable
            else {
                "code": "REVIEW_PROVIDER_UNAVAILABLE",
                "considered": selection["considered"],
            }
        ),
    )


def build_executable_review_packet(
    manifest: Mapping[str, Any],
    *,
    snapshot_ref: str,
    snapshot_hash: str,
    selected_provider: str | None,
    receiver_profile: Mapping[str, Any] | None,
    runner_argv: Sequence[str],
    hold: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the existing provider-neutral packet from an already bound selection.

    Protected local-review preparation uses the same packet contract after
    Doctor has pinned the provider identity.  Provider selection and request
    execution remain separate; this helper only performs the common immutable
    packet construction.
    """

    identity = candidate_identity(manifest)
    if not snapshot_ref.startswith("file:") or not _SHA256.fullmatch(snapshot_hash):
        raise CandidateReviewError("candidate review snapshot binding is invalid")
    executable = hold is None
    if executable and (
        not isinstance(selected_provider, str)
        or not selected_provider
        or not isinstance(receiver_profile, Mapping)
        or not runner_argv
        or not all(isinstance(item, str) for item in runner_argv)
    ):
        raise CandidateReviewError("candidate review provider binding is invalid")
    unsigned = {
        "schema": PACKET_SCHEMA,
        "status": "EXECUTABLE" if executable else "HOLD",
        "candidate_manifest_hash": identity,
        "candidate_source": {
            "commit": manifest["source"]["commit"],
            "tree": manifest["source"]["tree"],
            "archive_sha256": manifest["source"]["archive_sha256"],
        },
        "plan": {
            "commit": manifest["plan"]["commit"],
            "solution_hash": manifest["plan"]["solution_hash"],
            "closure_hash": manifest["plan"]["closure_hash"],
        },
        "snapshot": {"ref": snapshot_ref, "hash": snapshot_hash},
        "required_dimensions": ["semantic", "security"],
        "review_contract": {
            "schema": RESULT_SCHEMA,
            "pass_requires": "both dimensions PASS with zero findings",
            "source_mutation": "forbidden",
            "authority_broadening": "forbidden",
        },
        "selected_provider": selected_provider,
        "receiver_profile": dict(receiver_profile) if receiver_profile else None,
        "runner_argv": list(runner_argv),
        "hold": dict(hold) if hold is not None else None,
    }
    return {**unsigned, "packet_hash": _hash(unsigned)}


def _validate_dimension(name: str, value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"verdict", "findings"}:
        raise CandidateReviewError(f"{name} review dimension is invalid")
    if value["verdict"] not in {"PASS", "FAIL"} or not isinstance(value["findings"], list):
        raise CandidateReviewError(f"{name} review verdict/findings are invalid")
    for finding in value["findings"]:
        if not isinstance(finding, Mapping) or set(finding) != {
            "severity",
            "path",
            "line",
            "message",
        }:
            raise CandidateReviewError(f"{name} review finding fields are invalid")
        if finding["severity"] not in {"critical", "high", "medium", "low"}:
            raise CandidateReviewError(f"{name} review finding severity is invalid")
        if not isinstance(finding["path"], str) or not finding["path"]:
            raise CandidateReviewError(f"{name} review finding path is invalid")
        if not isinstance(finding["line"], int) or finding["line"] < 1:
            raise CandidateReviewError(f"{name} review finding line is invalid")
        if not isinstance(finding["message"], str) or not finding["message"].strip():
            raise CandidateReviewError(f"{name} review finding message is invalid")
    if value["verdict"] == "PASS" and value["findings"]:
        raise CandidateReviewError(f"passing {name} review cannot contain findings")
    if value["verdict"] == "FAIL" and not value["findings"]:
        raise CandidateReviewError(f"failed {name} review requires findings")


def _validate_packet(packet: Mapping[str, Any]) -> None:
    packet_unsigned = dict(packet)
    packet_claimed = packet_unsigned.pop("packet_hash", None)
    if packet.get("schema") != PACKET_SCHEMA or packet_claimed != _hash(packet_unsigned):
        raise CandidateReviewError("review packet hash/schema is invalid")
    if packet.get("status") != "EXECUTABLE":
        raise CandidateReviewError("a held review packet cannot accept a result")


def create_review_report(
    packet: Mapping[str, Any], dimensions: Mapping[str, Any]
) -> dict[str, Any]:
    """Create the runner-produced report that precedes all governed receipts."""

    _validate_packet(packet)
    if not isinstance(dimensions, Mapping) or set(dimensions) != {"semantic", "security"}:
        raise CandidateReviewError("review report requires semantic and security dimensions")
    for name in ("semantic", "security"):
        _validate_dimension(name, dimensions[name])
    passed = all(dimensions[name]["verdict"] == "PASS" for name in dimensions)
    unsigned = {
        "schema": REPORT_SCHEMA,
        "packet_hash": packet["packet_hash"],
        "candidate_manifest_hash": packet["candidate_manifest_hash"],
        "selected_provider": packet["selected_provider"],
        "dimensions": dict(dimensions),
        "overall": "PASS" if passed else "FAIL",
    }
    return {**unsigned, "report_hash": _hash(unsigned)}


def validate_review_report(
    packet: Mapping[str, Any], report: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate the exact semantic/security report emitted by the qualified runner."""

    _validate_packet(packet)
    required = {
        "schema", "packet_hash", "candidate_manifest_hash", "selected_provider",
        "dimensions", "overall", "report_hash",
    }
    if not isinstance(report, Mapping) or set(report) != required or report.get("schema") != REPORT_SCHEMA:
        raise CandidateReviewError("review report contract is invalid")
    unsigned = dict(report)
    claimed = unsigned.pop("report_hash")
    if claimed != _hash(unsigned):
        raise CandidateReviewError("review report hash mismatch")
    for field in ("packet_hash", "candidate_manifest_hash", "selected_provider"):
        expected = packet["packet_hash"] if field == "packet_hash" else packet[field]
        if report[field] != expected:
            raise CandidateReviewError(f"review report {field} mismatch")
    dimensions = report["dimensions"]
    if not isinstance(dimensions, Mapping) or set(dimensions) != {"semantic", "security"}:
        raise CandidateReviewError("review report requires semantic and security dimensions")
    for name in ("semantic", "security"):
        _validate_dimension(name, dimensions[name])
    expected_overall = "PASS" if all(dimensions[name]["verdict"] == "PASS" for name in dimensions) else "FAIL"
    if report["overall"] != expected_overall:
        raise CandidateReviewError("review report overall verdict is inconsistent")
    return dict(report)


def create_review_result(
    packet: Mapping[str, Any],
    report: Mapping[str, Any],
    governed_review_receipt: Mapping[str, Any],
    *,
    qualified_execution_proof_hash: str | None = None,
    governed_review_execution_hash: str | None = None,
) -> dict[str, Any]:
    """Finalize a report after one governed execution path and receipt exist.

    QES remains a supported execution path.  A qualified interactive provider
    instead supplies a root-captured governed execution record.  The
    two bindings are deliberately mutually exclusive so an admission cannot
    silently downgrade or combine their trust models.
    """

    normalized = validate_review_report(packet, report)
    if (qualified_execution_proof_hash is None) == (governed_review_execution_hash is None):
        raise CandidateReviewError("review result requires exactly one execution evidence binding")
    execution_binding = (
        {"qualified_execution_proof_hash": qualified_execution_proof_hash}
        if qualified_execution_proof_hash is not None
        else {"governed_review_execution_hash": governed_review_execution_hash}
    )
    unsigned = {
        "schema": RESULT_SCHEMA,
        "packet_hash": packet["packet_hash"],
        "candidate_manifest_hash": packet["candidate_manifest_hash"],
        "selected_provider": packet["selected_provider"],
        "review_report_hash": normalized["report_hash"],
        **execution_binding,
        "governed_review_receipt": dict(governed_review_receipt),
        "overall": normalized["overall"],
    }
    result = {**unsigned, "result_hash": _hash(unsigned)}
    validate_review_result(packet, report, result)
    return result


def validate_review_result(
    packet: Mapping[str, Any], report: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate the post-proof result against its packet and runner report."""

    normalized_report = validate_review_report(packet, report)
    common_fields = {
        "schema",
        "packet_hash",
        "candidate_manifest_hash",
        "selected_provider",
        "review_report_hash",
        "governed_review_receipt",
        "overall",
        "result_hash",
    }
    execution_fields = set(result) - common_fields
    if (
        result.get("schema") != RESULT_SCHEMA
        or not common_fields <= set(result)
        or execution_fields not in (
            {"qualified_execution_proof_hash"},
            {"governed_review_execution_hash"},
        )
    ):
        raise CandidateReviewError("review result contract is invalid")
    unsigned = dict(result)
    claimed = unsigned.pop("result_hash")
    if claimed != _hash(unsigned):
        raise CandidateReviewError("review result hash mismatch")
    for field in ("packet_hash", "candidate_manifest_hash", "selected_provider"):
        expected = packet["packet_hash"] if field == "packet_hash" else packet[field]
        if result[field] != expected:
            raise CandidateReviewError(f"review result {field} mismatch")
    if result["review_report_hash"] != normalized_report["report_hash"]:
        raise CandidateReviewError("review result report binding mismatch")
    execution_field = next(iter(execution_fields))
    if not _SHA256.fullmatch(str(result[execution_field])):
        label = (
            "qualified execution proof"
            if execution_field == "qualified_execution_proof_hash"
            else "governed review execution"
        )
        raise CandidateReviewError(f"review result {label} binding is invalid")
    receipt = result["governed_review_receipt"]
    if not isinstance(receipt, Mapping):
        raise CandidateReviewError("governed review receipt is required")
    validate_receipt(receipt)
    if receipt.get("role") != "independent-review" or receipt.get(
        "selected_provider"
    ) != packet["selected_provider"]:
        raise CandidateReviewError("governed review receipt role/provider mismatch")
    passed = normalized_report["overall"] == "PASS"
    if passed and (
        receipt.get("status") != "PASS"
        or receipt.get("established_conditions") != ["reviewed"]
    ):
        raise CandidateReviewError("passing governed review did not establish reviewed")
    if not passed and (
        receipt.get("status") != "FAIL" or receipt.get("established_conditions") != []
    ):
        raise CandidateReviewError("failed governed review claimed reviewed evidence")
    expected_overall = normalized_report["overall"]
    if result["overall"] != expected_overall:
        raise CandidateReviewError("review result overall verdict is inconsistent")
    validation = {
        "schema": "tgw-integrated-candidate-review-validation/v1",
        "status": expected_overall,
        "candidate_manifest_hash": packet["candidate_manifest_hash"],
        "packet_hash": packet["packet_hash"],
        "review_receipt_hash": receipt["receipt_hash"],
        "review_report_hash": normalized_report["report_hash"],
        "review_execution_kind": (
            "qualified-execution"
            if execution_field == "qualified_execution_proof_hash"
            else "governed-interactive"
        ),
        "review_execution_provider": packet["selected_provider"],
        "review_execution_evidence_hash": result[execution_field],
        "result_hash": result["result_hash"],
    }
    if execution_field == "qualified_execution_proof_hash":
        validation["qualified_execution_proof_hash"] = result[execution_field]
    else:
        validation["governed_review_execution_hash"] = result[execution_field]
    return validation

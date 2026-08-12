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
RESULT_SCHEMA = "tgw-integrated-candidate-review-result/v1"
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
) -> dict[str, Any]:
    """Generate an executable packet or an exact provider-selection HOLD."""

    identity = candidate_identity(manifest)
    if not snapshot_ref.startswith("file:") or not _SHA256.fullmatch(snapshot_hash):
        raise CandidateReviewError("candidate review snapshot binding is invalid")
    selection = select_provider(
        registry,
        health,
        role="independent-review",
        adapters=adapters,
        required_capabilities=("isolated-snapshot-review",),
        independent_from=independent_from,
    )
    executable = selection["status"] == "SELECTED"
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
        "selected_provider": selection.get("selected_provider"),
        "receiver_profile": selection.get("receiver_profile"),
        "runner_argv": selection.get("runner_argv", []),
        "hold": None
        if executable
        else {"code": "REVIEW_PROVIDER_UNAVAILABLE", "considered": selection["considered"]},
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


def validate_review_result(
    packet: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate one independent review result against its executable packet."""

    packet_unsigned = dict(packet)
    packet_claimed = packet_unsigned.pop("packet_hash", None)
    if packet.get("schema") != PACKET_SCHEMA or packet_claimed != _hash(packet_unsigned):
        raise CandidateReviewError("review packet hash/schema is invalid")
    if packet.get("status") != "EXECUTABLE":
        raise CandidateReviewError("a held review packet cannot accept a result")
    if result.get("schema") != RESULT_SCHEMA or set(result) != {
        "schema",
        "packet_hash",
        "candidate_manifest_hash",
        "selected_provider",
        "governed_review_receipt",
        "dimensions",
        "overall",
        "result_hash",
    }:
        raise CandidateReviewError("review result contract is invalid")
    unsigned = dict(result)
    claimed = unsigned.pop("result_hash")
    if claimed != _hash(unsigned):
        raise CandidateReviewError("review result hash mismatch")
    for field in ("packet_hash", "candidate_manifest_hash", "selected_provider"):
        expected = packet["packet_hash"] if field == "packet_hash" else packet[field]
        if result[field] != expected:
            raise CandidateReviewError(f"review result {field} mismatch")
    receipt = result["governed_review_receipt"]
    if not isinstance(receipt, Mapping):
        raise CandidateReviewError("governed review receipt is required")
    validate_receipt(receipt)
    if receipt.get("role") != "independent-review" or receipt.get(
        "selected_provider"
    ) != packet["selected_provider"]:
        raise CandidateReviewError("governed review receipt role/provider mismatch")
    dimensions = result["dimensions"]
    if not isinstance(dimensions, Mapping) or set(dimensions) != {"semantic", "security"}:
        raise CandidateReviewError("review result requires semantic and security dimensions")
    for name in ("semantic", "security"):
        _validate_dimension(name, dimensions[name])
    passed = all(dimensions[name]["verdict"] == "PASS" for name in dimensions)
    if passed and (
        receipt.get("status") != "PASS"
        or receipt.get("established_conditions") != ["reviewed"]
    ):
        raise CandidateReviewError("passing governed review did not establish reviewed")
    if not passed and (
        receipt.get("status") != "FAIL" or receipt.get("established_conditions") != []
    ):
        raise CandidateReviewError("failed governed review claimed reviewed evidence")
    expected_overall = "PASS" if passed else "FAIL"
    if result["overall"] != expected_overall:
        raise CandidateReviewError("review result overall verdict is inconsistent")
    return {
        "schema": "tgw-integrated-candidate-review-validation/v1",
        "status": expected_overall,
        "candidate_manifest_hash": packet["candidate_manifest_hash"],
        "packet_hash": packet["packet_hash"],
        "review_receipt_hash": receipt["receipt_hash"],
        "result_hash": result["result_hash"],
    }

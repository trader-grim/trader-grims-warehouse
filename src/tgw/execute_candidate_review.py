"""Execute one hash-bound integrated candidate review without installing it."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Mapping

from tgw.candidate_review import generate_review_packet, validate_review_result
from tgw.governed_coding import dispatch_role
from tgw.harness_registry import load_registry, observe_health
from tgw.review_configuration import configured_review_command
from tgw.review_runner import snapshot_hash


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


def _card(manifest: Mapping[str, Any], packet: Mapping[str, Any]) -> dict[str, Any]:
    source = manifest["source"]
    plan = manifest["plan"]
    return {
        "card_id": "candidate-review-" + str(source["commit"])[:12],
        "solution_id": plan["solution_hash"],
        "plan_commit": plan["commit"],
        "bindings": {
            "plan_input": {"ref": "candidate:" + packet["candidate_manifest_hash"], "hash": packet["candidate_manifest_hash"]},
            "plan_graph": {"ref": "tgw-plan:solution", "hash": plan["solution_hash"]},
            "codegraph_snapshot": {"ref": "git-tree:" + source["tree"], "hash": source["archive_sha256"]},
            "source_tree": dict(packet["snapshot"]),
            "execution_environment": {"ref": "provider-health:verified", "hash": _hash(packet["runner_argv"])},
            "authority_conditions": {"ref": "tgw-plan:closure", "hash": plan["closure_hash"]},
        },
        "authority": ["read-only semantic and security review of the bound snapshot"],
        "exclusions": ["source mutation", "deployment", "installation", "authority broadening"],
        "acceptance": ["validated semantic and security verdict bound to the candidate"],
        "receipt_sink": "candidate-review:" + str(source["commit"]),
        "lease": {"id": "review:" + str(source["commit"]), "expires_at": "2026-08-12T23:59:59Z", "stop_policy": "hold"},
    }


def execute(manifest_path: Path, repository: Path) -> dict[str, Any]:
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
        receipt = dispatch_role(
            registry,
            health,
            role="independent-review",
            adapters=adapters,
            card_template=_card(manifest, packet),
            execution_identity="isolated-review:" + str(manifest["source"]["commit"]),
            required_capabilities=("isolated-snapshot-review",),
        )
    reports = [
        artifact["report"]
        for artifact in receipt.get("artifacts", [])
        if artifact.get("kind") == "semantic_review" and isinstance(artifact.get("report"), Mapping)
    ]
    report = reports[0] if len(reports) == 1 else None
    passed = receipt["status"] == "PASS" and report is not None and report["verdict"] == "PASS"
    findings = [] if report is None else report["findings"]
    dimension = {"verdict": "PASS" if passed else "FAIL", "findings": findings}
    if not passed and not findings:
        raise ValueError(
            "failed review did not return validated findings: "
            + json.dumps(receipt, sort_keys=True)
        )
    unsigned = {
        "schema": "tgw-integrated-candidate-review-result/v1",
        "packet_hash": packet["packet_hash"],
        "candidate_manifest_hash": packet["candidate_manifest_hash"],
        "selected_provider": packet["selected_provider"],
        "governed_review_receipt": receipt,
        "dimensions": {"semantic": dimension, "security": dimension},
        "overall": "PASS" if passed else "FAIL",
    }
    result = {**unsigned, "result_hash": _hash(unsigned)}
    return {"packet": packet, "result": result, "validation": validate_review_result(packet, result)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(execute(args.manifest, args.repository.resolve()), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

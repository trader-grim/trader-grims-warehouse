#!/usr/bin/env python3
"""Independent verifier for the closed f3cefe5 evidence structure."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

SOURCE_COMMIT = "f3cefe544a9f81422b57707c4289f2974c6dca51"
SOURCE_TREE = "2c6cc6199827aa8ce87686c02cdccb1c0373cca3"
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def verify_self(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    claimed = value.pop("unsigned_sha256")
    assert claimed == sha(canonical(value)), f"self hash mismatch: {path}"
    value["unsigned_sha256"] = claimed
    return value


def verify_ref(repo: Path, ref: dict[str, Any]) -> dict[str, Any]:
    path = repo / ref["path"]
    assert sha(path.read_bytes()) == ref["file_sha256"], f"file hash mismatch: {path}"
    value = verify_self(path)
    assert value["unsigned_sha256"] == ref["unsigned_sha256"]
    return value


def held_metadata(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        observed = os.fstat(fd)
        digest = hashlib.sha256()
        while raw := os.read(fd, 1024 * 1024):
            digest.update(raw)
    finally:
        os.close(fd)
    return {
        "path": str(path), "sha256": "sha256:" + digest.hexdigest(), "size": observed.st_size,
        "dev": observed.st_dev, "inode": observed.st_ino, "uid": observed.st_uid,
        "gid": observed.st_gid, "mode": f"{stat.S_IMODE(observed.st_mode):04o}",
        "nlink": observed.st_nlink,
    }


def verify(repo: Path, store: Path) -> dict[str, Any]:
    catalog_path = repo / "agent-services/catalogs/f3cefe5-closed-freeze-evidence.json"
    candidate_path = repo / "agent-services/candidates/integrated-f3cefe5-CLOSED-FREEZE.json"
    descriptor_path = repo / "agent-services/candidates/platform-bootstrap-prerequisite-f3cefe5-CLOSED-NOT-EXECUTABLE.json"
    catalog = verify_self(catalog_path)
    candidate = json.loads(candidate_path.read_text())
    descriptor = verify_self(descriptor_path)
    assert catalog["source"]["commit"] == candidate["source"]["commit"] == SOURCE_COMMIT
    assert catalog["source"]["tree"] == candidate["source"]["tree"] == SOURCE_TREE
    assert catalog["plan"]["commit"] == candidate["plan"]["commit"] == PLAN_COMMIT
    assert candidate["status"] == descriptor["status"] == "PREPARED_NOT_EXECUTABLE"
    assert candidate["grant"] is None and candidate["request"] is None
    assert descriptor["grant"] is None and descriptor["request"] is None

    unsigned = dict(candidate)
    claimed_identity = unsigned.pop("candidate_identity")
    assert claimed_identity == "candidate:" + sha(canonical(unsigned))
    assert descriptor["candidate"]["candidate_identity"] == claimed_identity
    assert descriptor["candidate"]["file_sha256"] == sha(candidate_path.read_bytes())
    catalog_ref = candidate["evidence"]["catalog"]
    assert catalog_ref["file_sha256"] == sha(catalog_path.read_bytes())
    assert catalog_ref["unsigned_sha256"] == catalog["unsigned_sha256"]

    readiness = verify_ref(repo, catalog["protected_store_readiness"])
    audit = verify_ref(repo, catalog["source_audit"])
    assert readiness["status"] == "PASS" and audit["verdict"] == "PASS"
    assert audit["finding_count"] == 0
    assert audit["independent_runtime_pass"]["ref"] == "PASS:f3cefe5"
    independent = repo / audit["independent_runtime_pass"]["evidence_path"]
    assert sha(independent.read_bytes()) == audit["independent_runtime_pass"]["evidence_file_sha256"]

    on_disk = sorted(path.name for path in store.iterdir() if path.is_file())
    listed = sorted(item["sha256"].removeprefix("sha256:") for item in readiness["artifacts"])
    assert on_disk == listed
    assert readiness["artifact_count"] == len(on_disk) == len(catalog["protected_artifacts"])
    for item in readiness["artifacts"]:
        observed = held_metadata(store / item["sha256"].removeprefix("sha256:"))
        for key in ("path", "sha256", "size", "dev", "inode", "uid", "gid", "mode", "nlink"):
            assert observed[key] == item[key], f"metadata mismatch {item['path']}:{key}"
        assert item["uid"] == 0 and item["gid"] == 0 and item["mode"] == "0444" and item["nlink"] == 1
    assert readiness["artifacts"] == catalog["protected_artifacts"]

    for gate_id, ref in catalog["gate_records"].items():
        record = verify_ref(repo, ref)
        assert record["gate_id"] == gate_id and record["rc"] == 0
        assert record["semantic"]["status"] == "PASS"
        assert record["source"] == {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "status": "CLEAN_DETACHED"}
        assert record["environment"]["clear_inherited"] is True
        assert isinstance(record["argv"], list) and record["argv"][0] == record["executable"]["path"]
        assert record["cwd"].startswith("/") and all(isinstance(arg, str) for arg in record["argv"])
        assert record["started_at"].endswith("Z") and record["ended_at"].endswith("Z")
        for channel in ("stdout", "stderr"):
            artifact = store / record[channel]["sha256"].removeprefix("sha256:")
            assert artifact.stat().st_size == record[channel]["bytes"]
            assert sha(artifact.read_bytes()) == record[channel]["sha256"]

    source_root = repo if (repo / ".git").exists() else Path("/tmp/tgw-freeze-f3cefe5-source")
    for name, value in catalog["a3_source_identities"].items():
        del name
        raw = (source_root / value["path"]).read_bytes()
        assert len(raw) == value["size"] and sha(raw) == value["sha256"]
    assert candidate["history"]["prior_evidence_commits"] == [
        "26a9114312aefe6a11340a1108704d3997034083",
        "98b815c125f75e35b91ba8f92b22c653171464fc",
        "d11e1c00960ed151a4e04d213110e61bf7dd83d6",
    ]
    return {"status": "PASS", "artifact_count": len(on_disk),
            "gate_count": len(catalog["gate_records"]), "candidate_identity": claimed_identity}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.repo.resolve(), args.store.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

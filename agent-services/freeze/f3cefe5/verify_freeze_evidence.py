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
TREE_STORE = Path("/opt/TGW/evidence/codex/trees/sha256")


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
        "nlink": observed.st_nlink, "mtime_ns": observed.st_mtime_ns,
        "ctime_ns": observed.st_ctime_ns,
    }


def live_tree(path: Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    entries = []
    for entry in [path, *sorted(path.rglob("*"))]:
        observed = entry.lstat()
        assert not stat.S_ISLNK(observed.st_mode)
        relative = "." if entry == path else entry.relative_to(path).as_posix()
        common = {
            "path": relative, "dev": observed.st_dev, "inode": observed.st_ino,
            "uid": observed.st_uid, "gid": observed.st_gid,
            "mode": f"{stat.S_IMODE(observed.st_mode):04o}", "nlink": observed.st_nlink,
            "mtime_ns": observed.st_mtime_ns, "ctime_ns": observed.st_ctime_ns,
        }
        if entry.is_dir():
            assert common["uid"] == 0 and common["gid"] == 0 and common["mode"] == "0555"
            entries.append({**common, "type": "directory"})
        else:
            held = held_metadata(entry)
            assert held["uid"] == 0 and held["gid"] == 0 and held["mode"] == "0444"
            entries.append({**common, "type": "file", "sha256": held["sha256"],
                            "size": held["size"]})
    content_entries = [
        {key: item[key] for key in ("path", "type", "mode", "sha256", "size") if key in item}
        for item in entries
    ]
    tree_hash = sha(canonical({"schema": "tgw-protected-tree-content/v1",
                               "entries": content_entries}))
    return {"path": str(path), "tree_hash": tree_hash, "entries": entries,
            "content_entries": content_entries}


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
    assert readiness["protected_trees"] == catalog["protected_trees"]
    on_disk_trees = sorted(str(path.resolve()) for path in TREE_STORE.iterdir() if path.is_dir())
    listed_trees = sorted(item["path"] for item in readiness["protected_trees"])
    assert on_disk_trees == listed_trees
    assert readiness["protected_tree_count"] == len(listed_trees)
    for item in readiness["protected_trees"]:
        assert live_tree(Path(item["path"])) == item["manifest"]

    for gate_id, ref in catalog["gate_records"].items():
        record = verify_ref(repo, ref)
        assert record["gate_id"] == gate_id and record["rc"] == 0
        assert record["schema"] == "tgw-freeze-execution-record/v2"
        assert record["semantic"]["status"] == "PASS"
        assert record["source"] == {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "status": "CLEAN_DETACHED"}
        assert record["environment"]["clear_inherited"] is True
        executable = record["executable"]
        assert executable["component_safe_open"] is True and executable["unchanged"] is True
        assert executable["before"] == executable["after"] == executable["named_after"]
        assert record["logical_replay_argv"][0] == executable["logical_path"]
        assert record["actual_execve_argv"][0] == executable["actual_fd_path"]
        expected_procfd = (
            f"/proc/{record['descriptor_execution']['parent_pid']}/fd/"
            f"{record['descriptor_execution']['executable_fd']}"
        )
        assert executable["actual_fd_path"] == expected_procfd
        assert record["descriptor_execution"]["executable_fd"] in record["descriptor_execution"]["pass_fds"]
        observed_executable = held_metadata(Path(executable["logical_path"]))
        for key, expected in executable["before"].items():
            assert observed_executable[key] == expected
        assert record["cwd"].startswith("/")
        assert all(isinstance(arg, str) for arg in record["logical_replay_argv"])
        assert all(isinstance(arg, str) for arg in record["actual_execve_argv"])
        assert record["started_at"].endswith("Z") and record["ended_at"].endswith("Z")
        for item in record["inputs"]:
            assert item["unchanged"] is True
            assert item["before"] == item["after"] == item["named_after"]
            assert item["held_fd"] in record["descriptor_execution"]["pass_fds"]
            observed_input = held_metadata(Path(item["logical_path"]))
            for key, expected in item["before"].items():
                assert observed_input[key] == expected
            logical_present = item["logical_path"] in record["logical_replay_argv"]
            assert item["argv_substituted"] is logical_present
            if logical_present:
                index = record["logical_replay_argv"].index(item["logical_path"])
                assert record["actual_execve_argv"][index] == item["actual_fd_path"]
        for item in record["generated_artifacts"]:
            assert item["unchanged"] is True
            assert item["before"] == item["after"] == item["named_after"]
            assert item["before"]["sha256"] == item["sha256"]
            assert item["before"]["size"] == item["bytes"]
            observed_generated = held_metadata(Path(item["source_path"]))
            for key, expected in item["before"].items():
                assert observed_generated[key] == expected
        for tree in record["input_trees"]:
            assert tree["protected"] is True and tree["unchanged"] is True
            assert tree["argv_substituted"] is False
            assert tree["logical_path"] == tree["actual_argv_path"]
            argv_indexes = [
                index for index, argument in enumerate(record["logical_replay_argv"])
                if argument == tree["logical_path"] or argument.startswith(tree["logical_path"] + "/")
            ]
            env_consumed = tree["logical_path"] in record["environment"]["values"].values()
            assert argv_indexes or env_consumed
            for index in argv_indexes:
                assert record["actual_execve_argv"][index] == record["logical_replay_argv"][index]
            assert tree["before"] == tree["after"] == live_tree(Path(tree["logical_path"]))
        for channel in ("stdout", "stderr"):
            artifact = store / record[channel]["sha256"].removeprefix("sha256:")
            assert artifact.stat().st_size == record[channel]["bytes"]
            assert sha(artifact.read_bytes()) == record[channel]["sha256"]

    full = verify_ref(repo, catalog["gate_records"]["full_pytest_junit"])
    junit = full["semantic"]["junit"]
    assert {key: junit[key] for key in ("tests", "passed", "skipped", "failures", "errors")} == {
        "tests": 3730, "passed": 3725, "skipped": 5, "failures": 0, "errors": 0,
    }
    assert full["semantic"]["stdout_junit_cross_check"] is True
    integrity = verify_ref(repo, catalog["gate_records"]["freeze_integrity_adversarial_tests"])
    assert integrity["semantic"]["passed"] == 4 and integrity["semantic"]["skipped"] == 0

    source_root = repo if (repo / ".git").exists() else Path("/tmp/tgw-freeze-f3cefe5-source")
    for name, value in catalog["a3_source_identities"].items():
        del name
        raw = (source_root / value["path"]).read_bytes()
        assert len(raw) == value["size"] and sha(raw) == value["sha256"]
    assert candidate["history"]["prior_evidence_commits"] == [
        "26a9114312aefe6a11340a1108704d3997034083",
        "98b815c125f75e35b91ba8f92b22c653171464fc",
        "d11e1c00960ed151a4e04d213110e61bf7dd83d6",
        "a33bba3d0c3a7ff9cd151d05057043367dfbfc7c",
        "5f924377b2397e403b4c190ec81b2b9f319c2ccb",
    ]
    return {"status": "PASS", "artifact_count": len(on_disk),
            "protected_tree_count": len(listed_trees),
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

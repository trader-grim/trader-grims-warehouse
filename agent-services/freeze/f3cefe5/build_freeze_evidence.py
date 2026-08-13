#!/usr/bin/env python3
"""Build the closed f3cefe5 freeze documents from raw records and artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SOURCE = Path("/tmp/tgw-freeze-f3cefe5-source")
STORE = Path("/opt/TGW/evidence/codex/sha256")
TREE_STORE = Path("/opt/TGW/evidence/codex/trees/sha256")
SOURCE_COMMIT = "f3cefe544a9f81422b57707c4289f2974c6dca51"
SOURCE_TREE = "2c6cc6199827aa8ce87686c02cdccb1c0373cca3"
ARCHIVE = "72f3ed988e1fdc132d6da19d6332321389d41e22c114a7b4fa14e95755c5889f"
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
SOLUTION_HASH = "sha256:d28650c26c6a3d26d6c943597ccb7abd7c6670b1703d9ce941ac5ed7a2d73a4d"
CLOSURE_HASH = "sha256:bc0c53b2574fc359c629bd213e078fdd2824e5e1c4a98c0c7a347de869d9e6f8"
LUET = "c227742324a92eef4767961a9e49f687195b13356881336cc83d006e43d86c87"

A3_PATHS = {
    "module": "nix/a3-platform-bootstrap.nix",
    "package": "nix/a3-platform-bootstrap-package.nix",
    "native_transport_c": "src/native/tgw_nix_observer_render_transport.c",
    "helper": "src/tgw/nix_observer_render_helper.py",
    "remote_bootstrap": "src/tgw/nix_observer_render_remote.py",
    "controller": "src/tgw/nixos_observer_render_evaluation.py",
    "platform_bootstrap": "src/tgw/platform_bootstrap.py",
    "authority": "src/tgw/bootstrap_authority.py",
    "deployment_runtime": "src/tgw/deployment_runtime.py",
    "effect_handlers": "src/tgw/effect_handlers.py",
    "flake": "flake.nix",
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def write_self(path: Path, value: dict[str, Any]) -> dict[str, str]:
    value = dict(value)
    value["unsigned_hash_scheme"] = (
        "sha256 of canonical JSON excluding unsigned_sha256; final file sha256 is external in parent document"
    )
    value["unsigned_sha256"] = sha(canonical(value))
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return {"path": str(path.relative_to(REPO)), "file_sha256": file_sha(path),
            "unsigned_sha256": value["unsigned_sha256"]}


def record_ref(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text())
    unsigned = value.pop("unsigned_sha256")
    assert unsigned == sha(canonical(value))
    return {"path": str(path.relative_to(REPO)), "file_sha256": file_sha(path),
            "unsigned_sha256": unsigned}


def identity(path: Path) -> dict[str, Any]:
    held = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        observed = os.fstat(held)
        digest = hashlib.sha256()
        while raw := os.read(held, 1024 * 1024):
            digest.update(raw)
    finally:
        os.close(held)
    return {
        "path": str(path), "sha256": "sha256:" + digest.hexdigest(),
        "size": observed.st_size, "dev": observed.st_dev, "inode": observed.st_ino,
        "uid": observed.st_uid, "gid": observed.st_gid,
        "mode": f"{stat.S_IMODE(observed.st_mode):04o}", "nlink": observed.st_nlink,
    }


def directory_identity(path: Path) -> dict[str, Any]:
    held = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        observed = os.fstat(held)
    finally:
        os.close(held)
    return {
        "path": str(path), "dev": observed.st_dev, "inode": observed.st_ino,
        "uid": observed.st_uid, "gid": observed.st_gid,
        "mode": f"{stat.S_IMODE(observed.st_mode):04o}", "nlink": observed.st_nlink,
    }


def tree_identity(path: Path) -> dict[str, Any]:
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
            entries.append({**common, "type": "directory"})
        else:
            held = identity(entry)
            entries.append({**common, "type": "file", "sha256": held["sha256"],
                            "size": held["size"]})
    content_entries = [
        {key: item[key] for key in ("path", "type", "mode", "sha256", "size") if key in item}
        for item in entries
    ]
    return {"path": str(path),
            "tree_hash": sha(canonical({"schema": "tgw-protected-tree-content/v1",
                                         "entries": content_entries})),
            "entries": entries, "content_entries": content_entries}


def source_identities() -> dict[str, dict[str, Any]]:
    result = {}
    for name, relative in A3_PATHS.items():
        raw = (SOURCE / relative).read_bytes()
        result[name] = {"path": relative, "sha256": sha(raw), "size": len(raw)}
    return result


def main() -> int:
    records = {
        path.stem: record_ref(path)
        for path in sorted((HERE / "records").glob("*.json"))
        if path.stem != "freeze_independent_verifier"
    }
    required = {
        "full_pytest_junit", "focused_pytest", "ruff_explicit", "py_compile_explicit",
        "git_diff_check", "native_gcc_werror", "native_asan_ubsan_build_positive",
        "native_asan_ubsan_build_negative", "native_sanitizer_positive",
        "native_sanitizer_negative", "plan_graph_generation", "plan_solution_generation",
        "plan_solution_verification", "luet_version", "luet_raw_package_list",
        "luet_derived_tgw_receipt", "freeze_integrity_adversarial_tests",
    }
    assert required <= records.keys()
    for name in required:
        value = json.loads((HERE / "records" / f"{name}.json").read_text())
        assert value["rc"] == 0 and value["semantic"]["status"] == "PASS"
        assert value["source"] == {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "status": "CLEAN_DETACHED"}

    artifacts = []
    for path in sorted(STORE.iterdir()):
        if not path.is_file() or len(path.name) != 64:
            raise AssertionError(f"unexpected protected-store entry: {path}")
        item = identity(path)
        assert item["sha256"] == "sha256:" + path.name
        assert item["uid"] == 0 and item["gid"] == 0 and item["mode"] == "0444" and item["nlink"] == 1
        item["ref"] = "artifact:sha256:" + path.name
        item["roles"] = []
        artifacts.append(item)
    by_hash = {item["sha256"]: item for item in artifacts}
    assert "sha256:" + ARCHIVE in by_hash and "sha256:" + LUET in by_hash
    by_hash["sha256:" + ARCHIVE]["roles"].append("immutable_source_archive")
    by_hash["sha256:" + LUET]["roles"].append("pinned_luet_0.9.26_binary")
    for name, ref in records.items():
        value = json.loads((REPO / ref["path"]).read_text())
        for role in ("stdout", "stderr"):
            by_hash[value[role]["sha256"]]["roles"].append(f"{name}:{role}")
        for generated in value["generated_artifacts"]:
            by_hash[generated["sha256"]]["roles"].append(f"{name}:generated:{generated['role']}")
    for item in artifacts:
        if not item["roles"]:
            item["roles"].append("preserved_prior_raw_input")

    tree_records: dict[str, dict[str, Any]] = {}
    for name, ref in records.items():
        value = json.loads((REPO / ref["path"]).read_text())
        for tree in value.get("input_trees", []):
            assert tree["before"] == tree["after"] and tree["unchanged"] is True
            path = tree["logical_path"]
            prior = tree_records.setdefault(path, {"manifest": tree["before"], "roles": []})
            assert prior["manifest"] == tree["before"]
            prior["roles"].append(f"{name}:protected_input_tree")
    on_disk_trees = sorted(str(path.resolve()) for path in TREE_STORE.iterdir() if path.is_dir())
    for path in on_disk_trees:
        if path not in tree_records:
            tree_records[path] = {
                "manifest": tree_identity(Path(path)),
                "roles": ["preserved_prior_protected_tree_raw_input"],
            }
    protected_trees = [
        {"path": path, **tree_records[path]} for path in sorted(tree_records)
    ]

    a3 = source_identities()
    audit = {
        "schema": "tgw-f3cefe5-source-audit-matrix/v1",
        "source": {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
        "auditor": "codex-closed-freeze-reconciliation",
        "independent_runtime_pass": {
            "ref": "PASS:f3cefe5",
            "evidence_path": "agent-services/receipts/source-audit-f3cefe5.json",
            "evidence_file_sha256": file_sha(REPO / "agent-services/receipts/source-audit-f3cefe5.json"),
            "verdict": "PASS",
        },
        "audited_paths": list(a3.values()),
        "dimensions": [
            {"id": "closed-bootstrap-schema", "result": "PASS"},
            {"id": "expected-current-and-rollback-cas", "result": "PASS"},
            {"id": "single-use-durable-authority", "result": "PASS"},
            {"id": "native-config-and-packet-boundary", "result": "PASS"},
            {"id": "strict-ssh-and-sudo-identity", "result": "PASS"},
            {"id": "secret-exclusion", "result": "PASS"},
            {"id": "install-disabled-by-default", "result": "PASS"},
            {"id": "held-executable-rename-fail-closed-no-record", "result": "PASS"},
            {"id": "protected-tree-transient-replacement-impossible", "result": "PASS"},
        ],
        "exact_test_results": {
            "full": json.loads((HERE / "records/full_pytest_junit.json").read_text())["semantic"],
            "focused": json.loads((HERE / "records/focused_pytest.json").read_text())["semantic"],
            "integrity_adversarial": json.loads(
                (HERE / "records/freeze_integrity_adversarial_tests.json").read_text()
            )["semantic"],
        },
        "finding_count": 0, "verdict": "PASS",
        "gate_records": records,
        "external_live_gates": {
            "tgw_prod_flake_import_build": "NOT_EXECUTED_EXTERNAL_PREREQUISITE",
            "installed_sshd_T_effective_values": "NOT_EXECUTED_EXTERNAL_PREREQUISITE",
            "installed_root_native_netns": "NOT_EXECUTED_EXTERNAL_PREREQUISITE",
            "remote_install_activation_health_probe": "NOT_EXECUTED_EXTERNAL_PREREQUISITE",
        },
        "remote_executed": False, "production_build_executed": False,
        "native_test_build_executed": True, "install_executed": False,
        "key_generation_executed": False,
    }
    audit_ref = write_self(REPO / "agent-services/receipts/source-audit-f3cefe5-closed-freeze.json", audit)

    root = directory_identity(STORE)
    readiness = {
        "schema": "tgw-protected-store-readiness/v2", "artifact_root": root,
        "protected_tree_root": directory_identity(TREE_STORE),
        "policy": {"owner_uid": 0, "owner_gid": 0, "mode": "0444", "nlink": 1,
                   "filename": "lowercase_sha256", "overwrite": False},
        "artifact_count": len(artifacts), "artifacts": artifacts,
        "protected_tree_count": len(protected_trees), "protected_trees": protected_trees,
        "status": "PASS",
    }
    readiness_ref = write_self(REPO / "agent-services/receipts/f3cefe5-closed-store-readiness.json", readiness)

    catalog = {
        "schema": "tgw-f3cefe5-closed-evidence-catalog/v1",
        "source": {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE,
                   "archive": by_hash["sha256:" + ARCHIVE]},
        "plan": {"commit": PLAN_COMMIT, "solution_hash": SOLUTION_HASH, "closure_hash": CLOSURE_HASH},
        "a3_source_identities": a3, "gate_records": records,
        "protected_store_readiness": readiness_ref, "source_audit": audit_ref,
        "protected_artifacts": artifacts,
        "protected_trees": protected_trees,
        "constraints": {"remote": False, "production_build": False, "install": False,
                        "grant": False, "key_generation": False,
                        "native_test_compile_only": True},
    }
    catalog_ref = write_self(REPO / "agent-services/catalogs/f3cefe5-closed-freeze-evidence.json", catalog)

    candidate = {
        "schema": "tgw-integrated-candidate-freeze/v2",
        "identity_scheme": "candidate:sha256 of RFC8785-subset canonical JSON excluding candidate_identity; file sha256 is external in descriptor",
        "source": {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE,
                   "archive_ref": "artifact:sha256:" + ARCHIVE,
                   "archive_sha256": "sha256:" + ARCHIVE,
                   "archive_size": by_hash["sha256:" + ARCHIVE]["size"]},
        "plan": {"commit": PLAN_COMMIT, "solution_hash": SOLUTION_HASH, "closure_hash": CLOSURE_HASH},
        "evidence": {"catalog": catalog_ref, "store_readiness": readiness_ref,
                     "source_audit": audit_ref, "gate_records": records},
        "history": {"prior_evidence_commits": [
            "26a9114312aefe6a11340a1108704d3997034083",
            "98b815c125f75e35b91ba8f92b22c653171464fc",
            "d11e1c00960ed151a4e04d213110e61bf7dd83d6",
            "a33bba3d0c3a7ff9cd151d05057043367dfbfc7c",
            "5f924377b2397e403b4c190ec81b2b9f319c2ccb",
        ], "prior_manifests_are_raw_inputs_only": True},
        "descriptor_path": "agent-services/candidates/platform-bootstrap-prerequisite-f3cefe5-CLOSED-NOT-EXECUTABLE.json",
        "status": "PREPARED_NOT_EXECUTABLE", "dispatchable": False,
        "installed": False, "grant": None, "request": None,
    }
    candidate["candidate_identity"] = "candidate:" + sha(canonical(candidate))
    candidate_path = REPO / "agent-services/candidates/integrated-f3cefe5-CLOSED-FREEZE.json"
    candidate_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")

    descriptor = {
        "schema": "tgw-platform-bootstrap-prerequisite-descriptor/v2",
        "status": "PREPARED_NOT_EXECUTABLE",
        "candidate": {"path": str(candidate_path.relative_to(REPO)),
                      "candidate_identity": candidate["candidate_identity"],
                      "file_sha256": file_sha(candidate_path)},
        "source": candidate["source"], "plan": candidate["plan"],
        "evidence": {"catalog": catalog_ref, "store_readiness": readiness_ref, "source_audit": audit_ref,
                     "independent_verifier": "agent-services/freeze/f3cefe5/verify_freeze_evidence.py",
                     "literal_replay_test": "agent-services/freeze/f3cefe5/test_freeze_evidence.py"},
        "known_source_identities": a3,
        "unresolved_external_prerequisites": [
            {"id": "reviewed-tgw-flake-successor-closure", "required": "exact immutable build and closure membership records"},
            {"id": "fresh-current-host-cas", "required": "fresh expected-current host generation/profile observation"},
            {"id": "attestation-private-key", "required": "external root-owned 0400 reference/digest and exact public crossmatch"},
            {"id": "ssh-private-key", "required": "external root-owned 0400 reference/digest and authorized public crossmatch"},
            {"id": "tgw-prod-nix-build", "required": "live flake import/build and exact successor closure"},
            {"id": "installed-sshd-effective-policy", "required": "sshd -T -C user=... singleton authentication values"},
            {"id": "installed-root-native-netns", "required": "root/native/netns/prerequisite/health/probe evidence"},
        ],
        "secret_bytes_present": False, "grant": None, "request": None,
        "dispatchable": False, "installed": False,
    }
    descriptor_ref = write_self(
        REPO / "agent-services/candidates/platform-bootstrap-prerequisite-f3cefe5-CLOSED-NOT-EXECUTABLE.json",
        descriptor,
    )
    print(json.dumps({"audit": audit_ref, "readiness": readiness_ref, "catalog": catalog_ref,
                      "candidate": {"path": str(candidate_path.relative_to(REPO)),
                                    "file_sha256": file_sha(candidate_path),
                                    "candidate_identity": candidate["candidate_identity"]},
                      "descriptor": descriptor_ref, "artifact_count": len(artifacts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import copy
import io
import json
import os
import secrets
import stat
import subprocess
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.nixos_a3_successor_evaluation import (
    A3_SOURCE_IDENTITIES,
    INTEGRATION_PUBLIC_FILES,
    INTEGRATION_SCHEMA,
    PLAN_CLOSURE,
    PLAN_COMMIT,
    PLAN_SOLUTION,
    RENDERED_RELATIVE_PATHS,
    REQUEST_SCHEMA,
    SOURCE_ARCHIVE_SHA256,
    SOURCE_CANDIDATE,
    SOURCE_CATALOG,
    SOURCE_COMMIT,
    SOURCE_TREE,
    SUCCESS_SCHEMA,
    TARGET_ATTR,
    A3EvaluationAmbiguous,
    A3EvaluationComposition,
    A3EvaluationError,
    A3EvaluationFailure,
    A3KnownFailure,
    A3SuccessorEvaluationProvider,
    A3TestSuccessorEvaluationProvider,
    ImmutableEvaluationStore,
    canonical,
    digest,
    main,
    self_hash,
    terminal_receipt,
    validate_integration_contract,
    validate_request,
    validate_success,
    validate_terminal,
)
from tgw.nixos_a3_successor_remote import RENDERED_ARTIFACTS, Completed, _run_exact, execute, verify_git_archive
from tgw.nixos_a3_successor_transport import (
    A3LocalProductionComposition,
    A3LocalProductionTransport,
    A3TestTransport,
    DurableNonceReplayStore,
    StepFailure,
    _load_local_production_transport,
    _observe_current_cas,
    _open_held_executable,
    _stream_bounded,
    _terminate_group,
    validate_launcher_attestation,
)
from tgw.plan_authority import EffectKind, TypedEffect

STORE_HASH = "0" * 32
OUTPUT = f"/nix/store/{STORE_HASH}-nixos-system-tgw-prod-26.08"
DRV = f"/nix/store/{STORE_HASH}-nixos-system-tgw-prod-26.08.drv"
RENDERED_PATHS = RENDERED_RELATIVE_PATHS
PUBLIC_FIXTURE_VALUES = {
    "codex-authorized-key.txt": (
        'restrict,command="/run/wrappers/bin/sudo -n -- /run/current-system/sw/bin/tgw-nix-observer-render-wrapper" '
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFIXTUREONLY00000000000000000000000000000\n"
    ),
    "nix-observer-render-attestation.pub": "fixture-public-attestation\n",
    "nix-observer-render-composition.json": "{}\n",
    "nix-observer-render-prerequisite.json": "{}\n",
    "nix-observer-render-wrapper.conf": "fixture=true\n",
}
SSHD_SERVICE_FIXTURE = b"[Unit]\nDescription=OpenSSH server\n[Service]\nType=simple\nExecStart=/bin/true\n"
_FIXTURE_SIGNING_KEYS: dict[str, Ed25519PrivateKey] = {}


class MemoryStore:
    def __init__(self, *, wrong: bool = False):
        self.values: list[Mapping[str, Any]] = []
        self.wrong = wrong

    def persist(self, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
        self.values.append(receipt)
        return {
            "schema": "tgw-nixos-a3-successor-evaluation-store-ref/v1",
            "sha256": "sha256:" + "f" * 64 if self.wrong else receipt["receipt_sha256"],
            "size": len(canonical(receipt)),
        }


def _git(*argv: str, cwd: Path) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def _archive(repository: Path, prefix: str, commit: str = "HEAD") -> bytes:
    return subprocess.run(
        ["git", "archive", "--format=tar", f"--prefix={prefix}/", commit],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


def _integration(tmp_path: Path, *, attestation_public_key: bytes) -> tuple[dict[str, Any], bytes]:
    repository = tmp_path / "integration"
    repository.mkdir()
    _git("init", "-q", cwd=repository)
    _git("config", "user.name", "Fixture", cwd=repository)
    _git("config", "user.email", "fixture@example.invalid", cwd=repository)
    (repository / "flake.lock").write_text('{"version":7}\n')
    (repository / "flake.nix").write_text("{ inputs, ... }: { }\n")
    public = repository / "a3-public"
    public.mkdir()
    for name, content in PUBLIC_FIXTURE_VALUES.items():
        (public / name).write_bytes(attestation_public_key if name == "nix-observer-render-attestation.pub" else content.encode())
    module = repository / "hosts/tgw-prod"
    module.mkdir(parents=True)
    (module / "a3-platform-bootstrap.nix").write_text(Path("nix/review-fixtures/tgw-prod-a3-successor-integration.nix").read_text())
    _git("add", "-A", cwd=repository)
    _git("commit", "-qm", "reviewed fixture", cwd=repository)
    commit = _git("rev-parse", "HEAD", cwd=repository)
    tree = _git("rev-parse", "HEAD^{tree}", cwd=repository)
    raw = _archive(repository, "tgw-flake")
    contract = {
        "schema": INTEGRATION_SCHEMA,
        "status": "TEST_FIXTURE_NON_DEPLOYABLE",
        "repository_id": "tgw-flake",
        "target_host": "tgw-prod",
        "system": "x86_64-linux",
        "commit": commit,
        "tree": tree,
        "archive_ref": "artifact:" + digest(raw),
        "archive_sha256": digest(raw),
        "archive_size": len(raw),
        "flake_lock_sha256": digest((repository / "flake.lock").read_bytes()),
        "module_import": "inputs.tgw-lib.nixosModules.a3-platform-bootstrap",
        "exact_options": {
            "services.tgw-a3-platform-bootstrap.enable": True,
            "services.tgw-a3-platform-bootstrap.package": "inputs.tgw-lib.packages.x86_64-linux.a3-platform-bootstrap",
            "services.tgw-a3-platform-bootstrap.wrapperConfig": "../../a3-public/nix-observer-render-wrapper.conf",
            "services.tgw-a3-platform-bootstrap.composition": "../../a3-public/nix-observer-render-composition.json",
            "services.tgw-a3-platform-bootstrap.prerequisiteReceipt": "../../a3-public/nix-observer-render-prerequisite.json",
            "services.tgw-a3-platform-bootstrap.attestationPublicKey": "../../a3-public/nix-observer-render-attestation.pub",
            "services.tgw-a3-platform-bootstrap.sshAuthorizedPublicKey": "../../a3-public/codex-authorized-key.txt",
        },
        "public_files": {
            name: {
                "path": relative_path,
                "sha256": digest((repository / relative_path).read_bytes()),
                "size": len((repository / relative_path).read_bytes()),
            }
            for name, relative_path in INTEGRATION_PUBLIC_FILES.items()
        },
        "changed_paths": [
            "a3-public/codex-authorized-key.txt",
            "a3-public/nix-observer-render-attestation.pub",
            "a3-public/nix-observer-render-composition.json",
            "a3-public/nix-observer-render-prerequisite.json",
            "a3-public/nix-observer-render-wrapper.conf",
            "flake.lock",
            "flake.nix",
            "hosts/tgw-prod/a3-platform-bootstrap.nix",
        ],
        "unrelated_diff": False,
        "public_credentials_final": False,
        "closure_final": False,
        "live_gate": "external:tgw-prod-flake-import-build-and-sshd-T",
        "manifest_ref": "",
        "manifest_sha256": "",
    }
    contract["manifest_sha256"] = digest({key: item for key, item in contract.items() if key not in {"manifest_ref", "manifest_sha256"}})
    contract["manifest_ref"] = "manifest:" + contract["manifest_sha256"]
    return contract, raw


def _tool(path: Path) -> dict[str, Any]:
    path.write_text("#!/bin/sh\nexit 97\n")
    path.chmod(0o555)
    metadata = path.stat()
    return {
        "path": str(path),
        "sha256": digest(path.read_bytes()),
        "size": metadata.st_size,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": 0o555,
    }


def request_fixture(tmp_path: Path) -> tuple[dict[str, Any], bytes]:
    signing_key = Ed25519PrivateKey.generate()
    public_raw = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    integration, integration_archive = _integration(tmp_path, attestation_public_key=public_raw)
    public_path = tmp_path / "attestation-public.raw"
    public_path.write_bytes(public_raw)
    public_path.chmod(0o400)
    public_metadata = public_path.stat()
    replay_root = tmp_path / "replay"
    replay_root.mkdir(mode=0o700)
    replay_metadata = replay_root.stat()
    tools = {name: _tool(tmp_path / name.replace("_", "-")) for name in ("nix", "nix_store", "sshd", "systemd_analyze")}
    closure_paths = [{"path": f"/nix/store/{'1' * 32}-nixpkgs", "nar_sha256": "sha256:" + "2" * 64, "nar_size": 1024}]
    value: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "operation_id": "a3-successor-fixture-1",
        "plan": {"commit": PLAN_COMMIT, "solution_sha256": PLAN_SOLUTION, "closure_sha256": PLAN_CLOSURE},
        "source": {
            "commit": SOURCE_COMMIT,
            "tree": SOURCE_TREE,
            "archive_ref": "artifact:" + SOURCE_ARCHIVE_SHA256,
            "archive_sha256": SOURCE_ARCHIVE_SHA256,
            "archive_size": 9_594_880,
            "candidate_identity": SOURCE_CANDIDATE,
            "catalog_sha256": SOURCE_CATALOG,
            "a3_identities": A3_SOURCE_IDENTITIES,
        },
        "integration": integration,
        "target": {"host": "tgw-prod", "system": "x86_64-linux", "attribute": TARGET_ATTR, "expected_current": f"/nix/store/{'3' * 32}-nixos-system-tgw-prod-26.07", "expected_successor": OUTPUT},
        "input_closure": {"manifest_ref": "", "manifest_sha256": digest(closure_paths), "paths": closure_paths},
        "tools": tools,
        "expected_tool_versions": {name: {"stdout_sha256": digest(b""), "stderr_sha256": digest(b"")} for name in tools},
        "credentials": {
            "authorized_public_key_ref": "external:fixture-a3-authorized-public-key",
            "authorized_public_key_sha256": integration["public_files"]["authorized-key-codex"]["sha256"],
            "attestation_public_key_ref": "external:fixture-a3-attestation-public-key",
            "attestation_public_key_sha256": integration["public_files"]["attestation-public-key"]["sha256"],
            "final": False,
        },
        "expected_rendered": {
            name: {
                "relative_path": RENDERED_PATHS[name],
                "sha256": integration["public_files"][name]["sha256"] if name in INTEGRATION_PUBLIC_FILES else digest(SSHD_SERVICE_FIXTURE if name == "sshd-service" else (name + "\n").encode()),
                "size": integration["public_files"][name]["size"] if name in INTEGRATION_PUBLIC_FILES else len(SSHD_SERVICE_FIXTURE if name == "sshd-service" else (name + "\n").encode()),
            }
            for name in RENDERED_ARTIFACTS
        },
        "expected_verifiers": {
            "sshd": {"stdout_sha256": digest(b""), "stderr_sha256": digest(b"")},
            "systemd_analyze": {"stdout_sha256": digest(b""), "stderr_sha256": digest(b"")},
        },
        "validation_authority": {
            "attestation_public_key": {
                "path": str(public_path),
                "sha256": digest(public_raw),
                "size": len(public_raw),
                "uid": public_metadata.st_uid,
                "gid": public_metadata.st_gid,
                "mode": 0o400,
            },
            "replay_root": {
                "path": str(replay_root),
                "dev": replay_metadata.st_dev,
                "ino": replay_metadata.st_ino,
                "uid": replay_metadata.st_uid,
                "gid": replay_metadata.st_gid,
                "mode": 0o700,
            },
            "trusted_uid": os.getuid(),
            "child_uid": 1000,
            "child_gid": 1000,
        },
        "policy": {
            "offline": True,
            "nix_remote": "local",
            "substituters": [],
            "builders": [],
            "use_substitutes": False,
            "allow_ifd": False,
            "write_lock_file": False,
            "no_link": True,
            "max_seconds": 60,
            "max_output_bytes": 1_048_576,
            "max_archive_bytes": 16_777_216,
            "max_unpacked_bytes": 33_554_432,
            "max_files": 20_000,
        },
        "request_sha256": "",
    }
    value["input_closure"]["manifest_ref"] = "manifest:" + value["input_closure"]["manifest_sha256"]
    value["request_sha256"] = self_hash(value, "request_sha256")
    _FIXTURE_SIGNING_KEYS[value["request_sha256"]] = signing_key
    return value, integration_archive


def success_fixture(request: Mapping[str, Any]) -> dict[str, Any]:
    manifest = [
        {"path": OUTPUT, "nar_sha256": "sha256:" + "6" * 64, "nar_size": 4096},
        {"path": f"/nix/store/{'7' * 32}-dependency", "nar_sha256": "sha256:" + "8" * 64, "nar_size": 2048},
    ]
    manifest.sort(key=lambda item: item["path"])
    rendered = {
        name: {
            "path": OUTPUT + "/" + request["expected_rendered"][name]["relative_path"],
            "sha256": request["expected_rendered"][name]["sha256"],
            "size": request["expected_rendered"][name]["size"],
            "file_identity": {
                "resolved_path": "/nix/store/" + "9" * 32 + "-rendered/" + name,
                "dev": 1,
                "ino": index,
                "uid": 0,
                "gid": 0,
                "mode": 0o444,
                "nlink": 1,
            },
        }
        for index, name in enumerate(RENDERED_ARTIFACTS, 1)
    }
    sshd_command = [request["tools"]["sshd"]["path"], "-T", "-C", "user=codex,host=tgw-prod,addr=127.0.0.1", "-f", rendered["sshd-config"]["path"]]
    systemd_command = [request["tools"]["systemd_analyze"]["path"], "verify", "--man=no", "sshd.service"]

    def verifier(name: str, command: list[str], actual: list[str], version_flag: str) -> dict[str, Any]:
        return {
            "command": command,
            "actual_command": actual,
            "version_command": [request["tools"][name]["path"], version_flag],
            "actual_version_command": ["/proc/123/fd/10", version_flag],
            "executable": request["tools"][name],
            "returncode": 0,
            "stdout_sha256": digest(b""),
            "stderr_sha256": digest(b""),
            "version_stdout_sha256": request["expected_tool_versions"][name]["stdout_sha256"],
            "version_stderr_sha256": digest(b""),
        }

    issued = datetime.now(UTC)
    launch_nonce = secrets.token_hex(32)
    signed_attestation = {
        "schema": "tgw-nixos-a3-local-netns-attestation/v1",
        "packet_sha256": "sha256:" + "1" * 64,
        "composition_sha256": "sha256:" + "a" * 64,
        "request_sha256": request["request_sha256"],
        "launch_nonce": launch_nonce,
        "attempt_id": "attempt:" + secrets.token_hex(32),
        "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "started_at": (issued + timedelta(milliseconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "ended_at": (issued + timedelta(milliseconds=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "expires_at": (issued + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "netns": {
            "start_inode": 10,
            "end_inode": 10,
            "lo_only": True,
            "routes_empty": True,
            "link_sha256": "sha256:" + "4" * 64,
            "route_sha256": "sha256:" + "5" * 64,
        },
        "child": {"pid": 123, "starttime": 1, "exe": "/proc/123/fd/9", "uid": 1000, "gid": 1000, "capabilities": [], "no_new_privs": True},
        "probes": {
            phase: {
                name: {"attempted": True, "connected": False, "evidence_sha256": "sha256:" + str(index) * 64}
                for index, name in enumerate(("direct", "dns", "private", "metadata"), 6)
            }
            for phase in ("pre", "post")
        },
    }
    signed_attestation["signature"] = "ed25519:" + _FIXTURE_SIGNING_KEYS[request["request_sha256"]].sign(canonical(signed_attestation)).hex()
    challenge = {
        "packet_sha256": signed_attestation["packet_sha256"],
        "composition_sha256": signed_attestation["composition_sha256"],
        "request_sha256": signed_attestation["request_sha256"],
        "launch_nonce": signed_attestation["launch_nonce"],
        "attempt_id": signed_attestation["attempt_id"],
        "issued_at": signed_attestation["issued_at"],
        "expires_at": signed_attestation["expires_at"],
    }
    replay_store = DurableNonceReplayStore(request["validation_authority"]["replay_root"], _test_uid=os.getuid())
    durable = replay_store.claim(
        launch_nonce=signed_attestation["launch_nonce"],
        attempt_id=signed_attestation["attempt_id"],
        request_sha256=request["request_sha256"],
        composition_sha256=signed_attestation["composition_sha256"],
        attestation_sha256=digest(signed_attestation),
    )
    replay_store.close()
    launcher_evidence = [
        {
            "schema": "tgw-nixos-a3-launch-evidence/v1",
            "challenge": challenge,
            "signed_attestation": signed_attestation,
            "replay_claim": durable["claim"],
            "replay_claim_ref": durable["ref"],
        }
    ]
    result: dict[str, Any] = {
        "schema": SUCCESS_SCHEMA,
        "outcome": "SUCCEEDED",
        "request_sha256": request["request_sha256"],
        "operation_id": request["operation_id"],
        "source": request["source"],
        "integration": request["integration"],
        "target": request["target"],
        "derivation": DRV,
        "output_path": OUTPUT,
        "store_manifest": manifest,
        "store_manifest_sha256": digest(manifest),
        "rendered_artifacts": rendered,
        "tool_versions": {
            name: {
                "command": [request["tools"][name]["path"], "-V" if name == "sshd" else "--version"],
                "actual_command": ["/proc/123/fd/10", "-V" if name == "sshd" else "--version"],
                "executable": request["tools"][name],
                "returncode": 0,
                "stdout_sha256": request["expected_tool_versions"][name]["stdout_sha256"],
                "stderr_sha256": request["expected_tool_versions"][name]["stderr_sha256"],
            }
            for name in request["tools"]
        },
        "verifiers": {
            "sshd": verifier("sshd", sshd_command, ["/proc/123/fd/10", *sshd_command[1:-1], "/proc/123/fd/12"], "-V"),
            "systemd_analyze": verifier(
                "systemd_analyze",
                systemd_command,
                ["/proc/123/fd/11", "verify", "--man=no", "/tmp/a3-successor-fixture/sshd.service"],
                "--version",
            ),
        },
        "isolation": {
            "schema": "tgw-nixos-a3-local-isolation-summary/v1",
            "kind": "root-launcher-fresh-netns-per-command",
            "composition_sha256": "sha256:" + "a" * 64,
            "command_count": len(launcher_evidence),
            "launch_evidence_sha256": digest(launcher_evidence),
            "launcher_attested": True,
            "network_observed": False,
        },
        "launcher_evidence": launcher_evidence,
        "effects": {
            "build": True,
            "activate": False,
            "profile_write": False,
            "home_db_write": False,
            "live_flake_write": False,
            "gc_root_write": False,
            "deploy": False,
            "network": False,
            "lock_write": False,
            "substitute": False,
        },
        "cleanup": "REMOVED",
        "deployable": False,
    }
    result["receipt_sha256"] = self_hash(result)
    result["evidence"] = ["nixos-a3-successor-evaluation:" + result["receipt_sha256"]]
    return result


def _mutate(value: Mapping[str, Any], path: tuple[str, ...], replacement: Any) -> dict[str, Any]:
    changed = copy.deepcopy(value)
    cursor = changed
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = replacement
    return changed


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("schema",), "tgw-nixos-reviewed-evaluation-request/v1"),
        (("plan", "commit"), "f" * 40),
        (("source", "candidate_identity"), "candidate:sha256:" + "f" * 64),
        (("integration", "target_host"), "other"),
        (("target", "attribute"), "nixosConfigurations.other.config.system.build.toplevel"),
        (("policy", "offline"), False),
        (("policy", "builders"), ["ssh://host"]),
        (("policy", "allow_ifd"), True),
        (("policy", "max_archive_bytes"), 9_600_000),
        (("credentials", "authorized_public_key_ref"), "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ],
)
def test_request_mutation_matrix(tmp_path: Path, path: tuple[str, ...], replacement: Any) -> None:
    request, _ = request_fixture(tmp_path)
    validate_request(request, allow_fixture=True)
    with pytest.raises(A3EvaluationError):
        validate_request(_mutate(request, path, replacement), allow_fixture=True)


@pytest.mark.parametrize(
    "path",
    [
        ("source", "archive_size"),
        ("integration", "archive_size"),
        ("integration", "public_files", "attestation-public-key", "size"),
        ("input_closure", "paths", 0, "nar_size"),
        ("tools", "nix", "size"),
        ("tools", "nix", "uid"),
        ("tools", "nix", "gid"),
        ("tools", "nix", "mode"),
        ("expected_rendered", "sshd-service", "size"),
        ("policy", "max_seconds"),
        ("policy", "max_output_bytes"),
        ("policy", "max_archive_bytes"),
        ("policy", "max_unpacked_bytes"),
        ("policy", "max_files"),
        ("validation_authority", "attestation_public_key", "size"),
        ("validation_authority", "replay_root", "ino"),
        ("validation_authority", "replay_root", "mode"),
    ],
)
def test_request_numeric_fields_reject_bool_after_all_hashes_recomputed(tmp_path: Path, path: tuple[Any, ...]) -> None:
    request, _ = request_fixture(tmp_path)
    cursor: Any = request
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = True
    if path[0] == "integration":
        request["integration"]["manifest_sha256"] = digest(
            {key: item for key, item in request["integration"].items() if key not in {"manifest_ref", "manifest_sha256"}}
        )
        request["integration"]["manifest_ref"] = "manifest:" + request["integration"]["manifest_sha256"]
    if path[0] == "input_closure":
        request["input_closure"]["manifest_sha256"] = digest(request["input_closure"]["paths"])
        request["input_closure"]["manifest_ref"] = "manifest:" + request["input_closure"]["manifest_sha256"]
    request["request_sha256"] = self_hash(request, "request_sha256")
    with pytest.raises(A3EvaluationError, match="(integer|size|range|identity|authority)"):
        validate_request(request, allow_fixture=True)


def test_fake_production_marker_and_direct_constructor_are_rejected_preconsume(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)

    class FakeProduction:
        production_transport = True

        def __call__(self, _: Mapping[str, Any]) -> Mapping[str, Any]:
            return success_fixture(request)

    provider = A3TestSuccessorEvaluationProvider(A3EvaluationComposition(request["integration"], MemoryStore(), FakeProduction(), allow_fixture=True))
    with pytest.raises(Exception, match="distinct test transport"):
        provider.ready(request)
    with pytest.raises(TypeError, match="load_local"):
        A3LocalProductionTransport(None, _token=object())  # type: ignore[arg-type]


def test_local_composition_self_hash_changes_for_every_bound_identity() -> None:
    base = {
        "schema": "tgw-nixos-a3-local-production-composition/v1",
        "request_sha256": "sha256:" + "0" * 64,
        "runner_source": {"path": "/runner"},
        "helper": {"path": "/x"},
        "product_archive": {"path": "/a"},
        "integration_archive": {"path": "/b"},
        "target": {"host": "tgw-prod"},
        "current_cas_observer": {"path": "/observer"},
        "current_cas_observation": {"observed": "/nix/store/x"},
        "scratch_root": {"path": "/scratch"},
        "receipt_roots": {"terminal": "/receipts"},
        "tools": {"nix": "/nix"},
        "tool_versions": {"nix": "2.30"},
        "root_launcher": {"path": "/launcher"},
        "launcher_source": {"path": "/launcher.c"},
        "launcher_config": {"path": "/config"},
        "netns_prerequisite": {"path": "/prerequisite"},
        "prerequisite_status": "EXTERNAL_PREREQUISITE",
        "attestation_public_key": {"path": "/public"},
        "signing_key_ref": "external-root-0400:sha256:" + "f" * 64,
        "codex_identity": {"uid": 1000, "gid": 1000},
        "bounds": {"timeout_seconds": 60},
    }
    first = A3LocalProductionComposition(**base, composition_sha256=digest(base))
    changed = copy.deepcopy(base)
    changed["product_archive"] = {"path": "/different"}
    second = A3LocalProductionComposition(**changed, composition_sha256=digest(changed))
    assert first.composition_sha256 != second.composition_sha256


def test_production_provider_direct_construction_is_impossible(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    with pytest.raises(TypeError, match="build_local"):
        A3SuccessorEvaluationProvider(
            A3EvaluationComposition(request["integration"], MemoryStore(), A3TestTransport(lambda _: success_fixture(request)), allow_fixture=True)
        )


def test_different_immutable_store_identity_changes_composition_digest(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    roots = [tmp_path / "receipts-a", tmp_path / "receipts-b"]
    for root in roots:
        root.mkdir(mode=0o700)
    stores = [ImmutableEvaluationStore(root) for root in roots]
    compositions = [
        A3EvaluationComposition(request["integration"], store, A3TestTransport(lambda _: success_fixture(request)), allow_fixture=True)
        for store in stores
    ]
    assert compositions[0].receipt_sha256 != compositions[1].receipt_sha256


def test_composition_manifest_xy_replacement_is_rejected(tmp_path: Path) -> None:
    base = {
        "schema": "tgw-nixos-a3-local-production-composition/v1",
        "request_sha256": "sha256:" + "0" * 64,
        "runner_source": {"path": "/runner"},
        "helper": {"path": "/helper"},
        "product_archive": {"path": "/product"},
        "integration_archive": {"path": "/integration"},
        "target": {"host": "tgw-prod"},
        "current_cas_observer": {"path": "/observer"},
        "current_cas_observation": {"observed": "current"},
        "scratch_root": {"path": "/scratch"},
        "receipt_roots": {"terminal": "/terminal", "readiness": "/ready", "replay": "/replay"},
        "tools": {"nix": "/nix"},
        "tool_versions": {"nix": "version"},
        "root_launcher": {"path": "/launcher"},
        "launcher_source": {"path": "/launcher-source"},
        "launcher_config": {"path": "/launcher-config"},
        "netns_prerequisite": {"path": "/prerequisite"},
        "prerequisite_status": "EXTERNAL_PREREQUISITE",
        "attestation_public_key": {"path": "/public"},
        "signing_key_ref": "external-root-0400:sha256:" + "f" * 64,
        "codex_identity": {"uid": 1000, "gid": 1000},
        "bounds": {"timeout_seconds": 60},
    }
    value = {**base, "composition_sha256": digest(base)}
    path = tmp_path / "composition.json"
    raw = canonical(value)
    path.write_bytes(raw)
    path.chmod(0o400)

    def replace_after_open(target: Path) -> None:
        displaced = target.with_suffix(".held")
        target.rename(displaced)
        target.write_bytes(raw)
        target.chmod(0o400)

    with pytest.raises(A3EvaluationError, match="identity changed"):
        _load_local_production_transport(path, _test_uid=os.getuid(), _after_read=replace_after_open)


def test_forged_netns_attestation_is_rejected() -> None:
    attestation = {
        "schema": "tgw-nixos-a3-local-netns-attestation/v1",
        "packet_sha256": "sha256:" + "1" * 64,
        "composition_sha256": "sha256:" + "2" * 64,
        "request_sha256": "sha256:" + "3" * 64,
        "launch_nonce": "4" * 64,
        "attempt_id": "attempt:" + "5" * 64,
        "issued_at": "2026-08-12T00:00:00.000000Z",
        "started_at": "2026-08-12T00:00:00.100000Z",
        "ended_at": "2026-08-12T00:00:01.000000Z",
        "expires_at": "2026-08-12T00:01:00.000000Z",
        "netns": {
            "start_inode": 10,
            "end_inode": 10,
            "lo_only": True,
            "routes_empty": True,
            "link_sha256": "sha256:" + "4" * 64,
            "route_sha256": "sha256:" + "5" * 64,
        },
        "child": {"pid": 123, "starttime": 1, "exe": "/proc/123/fd/9", "uid": 1000, "gid": 1000, "capabilities": [], "no_new_privs": True},
        "probes": {
            phase: {
                name: {"attempted": True, "connected": False, "evidence_sha256": "sha256:" + str(index) * 64}
                for index, name in enumerate(("direct", "dns", "private", "metadata"), 6)
            }
            for phase in ("pre", "post")
        },
        "signature": "ed25519:" + "00" * 64,
    }
    with pytest.raises(A3EvaluationError, match="signature"):
        validate_launcher_attestation(
            attestation,
            packet_sha256=attestation["packet_sha256"],
            composition_sha256=attestation["composition_sha256"],
            request_sha256=attestation["request_sha256"],
            launch_nonce=attestation["launch_nonce"],
            attempt_id=attestation["attempt_id"],
            issued_at=attestation["issued_at"],
            expires_at=attestation["expires_at"],
            public_key_raw=b"\0" * 32,
            uid=1000,
            gid=1000,
            now=datetime(2026, 8, 12, 0, 0, 2, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("launch_nonce", "f" * 64, "binding"),
        ("issued_at", "2020-01-01T00:00:00.000000Z", "stale"),
        ("issued_at", "2030-01-01T00:00:00.000000Z", "future"),
    ],
)
def test_attestation_foreign_or_nonfresh_challenge_is_rejected(field: str, replacement: str, message: str) -> None:
    attestation = {
        "schema": "tgw-nixos-a3-local-netns-attestation/v1",
        "packet_sha256": "sha256:" + "1" * 64,
        "composition_sha256": "sha256:" + "2" * 64,
        "request_sha256": "sha256:" + "3" * 64,
        "launch_nonce": "4" * 64,
        "attempt_id": "attempt:" + "5" * 64,
        "issued_at": "2026-08-12T00:00:00.000000Z",
        "started_at": "2026-08-12T00:00:00.100000Z",
        "ended_at": "2026-08-12T00:00:01.000000Z",
        "expires_at": "2026-08-12T00:01:00.000000Z",
        "netns": {
            "start_inode": 10,
            "end_inode": 10,
            "lo_only": True,
            "routes_empty": True,
            "link_sha256": "sha256:" + "6" * 64,
            "route_sha256": "sha256:" + "7" * 64,
        },
        "child": {"pid": 123, "starttime": 1, "exe": "/proc/123/fd/9", "uid": 1000, "gid": 1000, "capabilities": [], "no_new_privs": True},
        "probes": {
            phase: {
                name: {"attempted": True, "connected": False, "evidence_sha256": "sha256:" + str(index) * 64}
                for index, name in enumerate(("direct", "dns", "private", "metadata"), 6)
            }
            for phase in ("pre", "post")
        },
        "signature": "ed25519:" + "00" * 64,
    }
    attestation[field] = replacement
    with pytest.raises(A3EvaluationError, match=message):
        validate_launcher_attestation(
            attestation,
            packet_sha256=attestation["packet_sha256"],
            composition_sha256=attestation["composition_sha256"],
            request_sha256=attestation["request_sha256"],
            launch_nonce="4" * 64,
            attempt_id=attestation["attempt_id"],
            issued_at=attestation["issued_at"],
            expires_at=attestation["expires_at"],
            public_key_raw=b"\0" * 32,
            uid=1000,
            gid=1000,
            now=datetime(2026, 8, 12, 0, 0, 2, tzinfo=UTC),
        )


def test_durable_launch_challenge_replay_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "replay"
    root.mkdir(mode=0o700)
    metadata = root.stat()
    identity = {
        "path": str(root),
        "dev": metadata.st_dev,
        "ino": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": 0o700,
    }
    store = DurableNonceReplayStore(identity, _test_uid=os.getuid())
    values = {
        "launch_nonce": "a" * 64,
        "attempt_id": "attempt:" + "b" * 64,
        "request_sha256": "sha256:" + "c" * 64,
        "composition_sha256": "sha256:" + "f" * 64,
        "attestation_sha256": "sha256:" + "d" * 64,
    }
    assert store.claim(**values)["ref"]["claim_sha256"].startswith("sha256:")
    with pytest.raises(A3EvaluationError, match="already consumed"):
        store.claim(**values)
    with pytest.raises(A3EvaluationError, match="already consumed"):
        store.claim(**{**values, "attempt_id": "attempt:" + "e" * 64})


def test_public_file_manifest_request_and_rendered_chain_is_exact(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    request["integration"]["public_files"]["authorized-key-codex"]["sha256"] = "sha256:" + "f" * 64
    request["integration"]["manifest_sha256"] = digest(
        {key: item for key, item in request["integration"].items() if key not in {"manifest_ref", "manifest_sha256"}}
    )
    request["integration"]["manifest_ref"] = "manifest:" + request["integration"]["manifest_sha256"]
    request["request_sha256"] = self_hash(request, "request_sha256")
    with pytest.raises(A3EvaluationError, match="credential identities"):
        validate_request(request, allow_fixture=True)


def test_process_group_timeout_is_typed_and_reaped(tmp_path: Path) -> None:
    process = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 60 & wait"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    with pytest.raises(StepFailure) as raised:
        _stream_bounded(process, input_bytes=b"", timeout=0, max_output=1024)
    assert raised.value.process_state == "REAPED_GROUP_EMPTY"
    assert process.poll() is not None


def test_process_group_kills_term_ignoring_grandchild(tmp_path: Path) -> None:
    process = subprocess.Popen(
        ["/bin/sh", "-c", "trap 'exit 0' TERM; (trap '' TERM; exec sleep 60) & wait"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _, state, cleanup = _terminate_group(process, grace_seconds=0.5)
    assert (state, cleanup) == ("REAPED_GROUP_EMPTY", "REMOVED")
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


def test_current_cas_observer_is_exact_no_argv_held_execution(tmp_path: Path) -> None:
    expected = f"/nix/store/{'3' * 32}-nixos-system-tgw-prod-26.07"
    observer = tmp_path / "observe-current"
    observer.write_text(f"#!/bin/sh\nprintf '%s\\n' '{expected}'\n")
    observer.chmod(0o555)
    metadata = observer.stat()
    identity = {
        "path": str(observer),
        "sha256": digest(observer.read_bytes()),
        "size": metadata.st_size,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": 0o555,
    }
    result = _observe_current_cas(identity, expected)
    assert result == {"returncode": 0, "stdout_sha256": digest((expected + "\n").encode()), "stderr_sha256": digest(b"")}


@pytest.mark.parametrize("target", ["cas", "launcher"])
def test_held_executable_named_replacement_is_rejected(tmp_path: Path, target: str) -> None:
    expected = f"/nix/store/{'3' * 32}-nixos-system-tgw-prod-26.07"
    executable = tmp_path / target
    executable.write_text(f"#!/bin/sh\nprintf '%s\\n' '{expected}'\n")
    executable.chmod(0o555)
    metadata = executable.stat()
    identity = {
        "path": str(executable),
        "sha256": digest(executable.read_bytes()),
        "size": metadata.st_size,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": 0o555,
    }

    def replace(path: Path) -> None:
        path.rename(path.with_suffix(".held"))
        path.write_text("#!/bin/sh\nexit 99\n")
        path.chmod(0o555)

    with pytest.raises(A3EvaluationError, match="(identity|proven|exact admitted state)"):
        if target == "cas":
            _observe_current_cas(identity, expected, _after_open=replace)
        else:
            _open_held_executable(identity, "root launcher", _after_open=replace)


@pytest.mark.parametrize("target", ["cas", "launcher"])
def test_held_executable_symlink_ancestor_is_rejected(tmp_path: Path, target: str) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    executable = real / target
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o555)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    metadata = executable.stat()
    identity = {
        "path": str(linked / target),
        "sha256": digest(executable.read_bytes()),
        "size": metadata.st_size,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": 0o555,
    }
    with pytest.raises((A3EvaluationError, OSError)):
        if target == "cas":
            _observe_current_cas(identity, "/nix/store/invalid")
        else:
            _open_held_executable(identity, "root launcher")


def test_terminal_state_table_rejects_impossible_tuples() -> None:
    effects = {"build": False, **{name: False for name in ("activate", "profile_write", "home_db_write", "live_flake_write", "gc_root_write", "deploy", "network", "lock_write", "substitute")}}
    with pytest.raises(A3EvaluationError, match="state table"):
        terminal_receipt(
            request_sha256="sha256:" + "1" * 64,
            provider_sha256="sha256:" + "2" * 64,
            outcome="AMBIGUOUS",
            stage="composition-readiness",
            step="provider",
            code="Missing",
            returncode=None,
            stdout=b"",
            stderr=b"",
            cleanup="NOT_CREATED",
            effects=effects,
            observation={},
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("step", "build"),
        ("code", "ArbitraryError"),
        ("returncode", 1),
        ("cleanup", "UNKNOWN"),
        ("outcome", "AMBIGUOUS"),
    ],
)
def test_terminal_exact_tuple_mutation_matrix(field: str, replacement: Any) -> None:
    request_sha256 = "sha256:" + "1" * 64
    provider_sha256 = "sha256:" + "2" * 64
    terminal = terminal_receipt(
        request_sha256=request_sha256,
        provider_sha256=provider_sha256,
        outcome="FAILED",
        stage="prebuild-validation",
        step="contract-validation",
        code="A3KnownFailure",
        returncode=None,
        stdout=b"",
        stderr=b"",
        cleanup="REMOVED",
        effects={"build": False, **{name: False for name in ("activate", "profile_write", "home_db_write", "live_flake_write", "gc_root_write", "deploy", "network", "lock_write", "substitute")}},
        observation={},
    )
    terminal[field] = replacement
    terminal["receipt_sha256"] = self_hash({key: item for key, item in terminal.items() if key != "evidence"})
    terminal["evidence"] = ["nixos-a3-successor-evaluation-terminal:" + terminal["receipt_sha256"]]
    with pytest.raises(A3EvaluationError, match="(state table|classification)"):
        validate_terminal(terminal, request_sha256=request_sha256, provider_sha256=provider_sha256)


@pytest.mark.parametrize("returncode", [True, False, -256, 256])
def test_terminal_rejects_bool_and_out_of_domain_returncodes(returncode: Any) -> None:
    request_sha256 = "sha256:" + "1" * 64
    provider_sha256 = "sha256:" + "2" * 64
    terminal = terminal_receipt(
        request_sha256=request_sha256,
        provider_sha256=provider_sha256,
        outcome="FAILED",
        stage="nix-build",
        step="nix-build",
        code="A3KnownFailure",
        returncode=17,
        stdout=b"",
        stderr=b"",
        cleanup="REMOVED",
        effects={"build": True, **{name: False for name in ("activate", "profile_write", "home_db_write", "live_flake_write", "gc_root_write", "deploy", "network", "lock_write", "substitute")}},
        observation={},
    )
    terminal["returncode"] = returncode
    terminal["receipt_sha256"] = self_hash({key: item for key, item in terminal.items() if key != "evidence"})
    terminal["evidence"] = ["nixos-a3-successor-evaluation-terminal:" + terminal["receipt_sha256"]]
    with pytest.raises(A3EvaluationError, match="classification"):
        validate_terminal(terminal, request_sha256=request_sha256, provider_sha256=provider_sha256)


@pytest.mark.parametrize("returncode", [0, None])
def test_nonzero_subprocess_terminal_rejects_zero_or_null(returncode: int | None) -> None:
    request_sha256 = "sha256:" + "1" * 64
    provider_sha256 = "sha256:" + "2" * 64
    terminal = terminal_receipt(
        request_sha256=request_sha256,
        provider_sha256=provider_sha256,
        outcome="FAILED",
        stage="evaluation",
        step="nix-eval",
        code="A3KnownFailure",
        returncode=17,
        stdout=b"",
        stderr=b"",
        cleanup="REMOVED",
        effects={"build": False, **{name: False for name in ("activate", "profile_write", "home_db_write", "live_flake_write", "gc_root_write", "deploy", "network", "lock_write", "substitute")}},
        observation={},
    )
    terminal["returncode"] = returncode
    terminal["receipt_sha256"] = self_hash({key: item for key, item in terminal.items() if key != "evidence"})
    terminal["evidence"] = ["nixos-a3-successor-evaluation-terminal:" + terminal["receipt_sha256"]]
    with pytest.raises(A3EvaluationError, match="returncode relation"):
        validate_terminal(terminal, request_sha256=request_sha256, provider_sha256=provider_sha256)


@pytest.mark.parametrize(
    ("stage", "step"),
    [
        ("evaluation", "nix-version"),
        ("evaluation", "nix-store-version"),
        ("evaluation", "sshd-version"),
        ("evaluation", "systemd-version"),
        ("evaluation", "path-info"),
        ("evaluation", "nix-hash"),
        ("evaluation", "nix-eval"),
        ("nix-build", "nix-build"),
        ("post-build", "nix-store"),
        ("post-build", "path-info"),
        ("post-build", "nix-hash"),
        ("static-verification", "sshd-verify"),
        ("static-verification", "systemd-verify"),
    ],
)
def test_run_exact_nonzero_paths_persist_valid_terminal(tmp_path: Path, stage: str, step: str) -> None:
    request, _ = request_fixture(tmp_path)
    store = MemoryStore()

    def invoke(_: Mapping[str, Any]) -> Mapping[str, Any]:
        _run_exact(
            lambda *_args, **_kwargs: Completed(23, b"bounded stdout", b"bounded stderr"),
            ["/proc/1/fd/9", step],
            failure_stage=stage,
            failure_step=step,
            cwd=tmp_path,
            env={},
            timeout=1,
            max_output=1024,
            pass_fds=(),
            attestations=[],
            allow_fixture=True,
        )
        raise AssertionError("nonzero result was accepted")

    provider = A3TestSuccessorEvaluationProvider(A3EvaluationComposition(request["integration"], store, A3TestTransport(invoke), allow_fixture=True))
    with pytest.raises(A3EvaluationFailure) as raised:
        provider({"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request})
    terminal = raised.value.terminal
    assert terminal["stage"] == stage and terminal["step"] == step and terminal["returncode"] == 23
    assert store.values == [terminal]
    validate_terminal(terminal, request_sha256=request["request_sha256"], provider_sha256=provider.composition.receipt_sha256)


@pytest.mark.parametrize(
    ("step", "returncode", "cleanup", "outcome"),
    [
        ("launcher", 7, "REMOVED", "FAILED"),
        ("launcher-identity", 0, "REMOVED", "FAILED"),
        ("timeout", None, "REMOVED", "FAILED"),
        ("output-bound", -9, "REMOVED", "FAILED"),
        ("process-group", 0, "UNKNOWN", "AMBIGUOUS"),
        ("response", 0, "REMOVED", "AMBIGUOUS"),
        ("response-contract", 0, "REMOVED", "AMBIGUOUS"),
    ],
)
def test_run_exact_launcher_failures_always_persist_terminal(
    tmp_path: Path,
    step: str,
    returncode: int | None,
    cleanup: str,
    outcome: str,
) -> None:
    request, _ = request_fixture(tmp_path)
    store = MemoryStore()

    def invoke(_: Mapping[str, Any]) -> Mapping[str, Any]:
        def runner(*_args: Any, **_kwargs: Any) -> Completed:
            raise StepFailure(
                "launcher failure",
                step=step,
                returncode=returncode,
                stdout=b"out",
                stderr=b"err",
                process_state="UNKNOWN" if cleanup == "UNKNOWN" else "REAPED_GROUP_EMPTY",
                cleanup=cleanup,
            )

        _run_exact(
            runner,
            ["/proc/1/fd/9", "eval"],
            failure_stage="evaluation",
            failure_step="nix-eval",
            cwd=tmp_path,
            env={},
            timeout=1,
            max_output=1024,
            pass_fds=(),
            attestations=[],
            allow_fixture=True,
        )
        raise AssertionError("launcher failure was accepted")

    provider = A3TestSuccessorEvaluationProvider(A3EvaluationComposition(request["integration"], store, A3TestTransport(invoke), allow_fixture=True))
    error = A3EvaluationAmbiguous if outcome == "AMBIGUOUS" else A3EvaluationFailure
    with pytest.raises(error):
        provider({"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request})
    terminal = store.values[-1]
    assert terminal["outcome"] == outcome and terminal["step"] == step
    validate_terminal(terminal, request_sha256=request["request_sha256"], provider_sha256=provider.composition.receipt_sha256)


@pytest.mark.parametrize(
    ("mode", "outcome", "step"),
    [
        ("output-contract", "FAILED", "output-contract"),
        ("process-state", "AMBIGUOUS", "process-state"),
        ("attestation", "FAILED", "attestation"),
    ],
)
def test_run_exact_contract_failures_always_persist_terminal(tmp_path: Path, mode: str, outcome: str, step: str) -> None:
    request, _ = request_fixture(tmp_path)
    store = MemoryStore()

    def invoke(_: Mapping[str, Any]) -> Mapping[str, Any]:
        def runner(*_args: Any, **_kwargs: Any) -> Any:
            if mode == "output-contract":
                return object()
            if mode == "process-state":
                return Completed(0, b"", b"", process_state="UNKNOWN")
            return Completed(0, b"", b"")

        _run_exact(
            runner,
            ["/proc/1/fd/9", "eval"],
            failure_stage="evaluation",
            failure_step="nix-eval",
            cwd=tmp_path,
            env={},
            timeout=1,
            max_output=1024,
            pass_fds=(),
            attestations=[],
            allow_fixture=mode != "attestation",
        )
        raise AssertionError("invalid runner result was accepted")

    provider = A3TestSuccessorEvaluationProvider(A3EvaluationComposition(request["integration"], store, A3TestTransport(invoke), allow_fixture=True))
    error = A3EvaluationAmbiguous if outcome == "AMBIGUOUS" else A3EvaluationFailure
    with pytest.raises(error):
        provider({"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request})
    terminal = store.values[-1]
    assert terminal["outcome"] == outcome and terminal["step"] == step
    validate_terminal(terminal, request_sha256=request["request_sha256"], provider_sha256=provider.composition.receipt_sha256)


@pytest.mark.parametrize("mutation", ["signed-extra", "claim-extra", "foreign-nonce", "foreign-composition", "wrong-ref", "fixture-only"])
def test_success_rejects_unbound_or_broadened_launcher_evidence(tmp_path: Path, mutation: str) -> None:
    request, _ = request_fixture(tmp_path)
    result = success_fixture(request)
    envelope = result["launcher_evidence"][0]
    if mutation in {"signed-extra", "fixture-only"}:
        envelope["signed_attestation"]["fixture_only" if mutation == "fixture-only" else "unsigned_extra"] = True
    elif mutation == "claim-extra":
        envelope["replay_claim"]["extra"] = True
    elif mutation == "foreign-nonce":
        envelope["replay_claim"]["launch_nonce"] = "f" * 64
    elif mutation == "foreign-composition":
        envelope["replay_claim"]["composition_sha256"] = "sha256:" + "f" * 64
    else:
        envelope["replay_claim_ref"]["file_sha256"] = "sha256:" + "f" * 64
    result["isolation"]["launch_evidence_sha256"] = digest(result["launcher_evidence"])
    result["receipt_sha256"] = self_hash({key: item for key, item in result.items() if key != "evidence"})
    result["evidence"] = ["nixos-a3-successor-evaluation:" + result["receipt_sha256"]]
    with pytest.raises(A3EvaluationError, match="(launcher|replay|attestation)"):
        validate_success(result, request)


@pytest.mark.parametrize("mutation", ["signed-child", "zero-signature"])
def test_success_cryptographically_rejects_rehashed_signed_mutation(tmp_path: Path, mutation: str) -> None:
    request, _ = request_fixture(tmp_path)
    result = success_fixture(request)
    envelope = result["launcher_evidence"][0]
    signed = envelope["signed_attestation"]
    if mutation == "signed-child":
        signed["child"]["pid"] += 1
    else:
        signed["signature"] = "ed25519:" + "0" * 128
    claim = envelope["replay_claim"]
    claim["attestation_sha256"] = digest(signed)
    claim["claim_sha256"] = self_hash({key: item for key, item in claim.items() if key != "claim_sha256"})
    raw = canonical(claim)
    reference = envelope["replay_claim_ref"]
    reference["claim_sha256"] = claim["claim_sha256"]
    reference["file_sha256"] = digest(raw)
    reference["size"] = len(raw)
    claim_path = Path(request["validation_authority"]["replay_root"]["path"]) / reference["name"]
    claim_path.chmod(0o600)
    claim_path.write_bytes(raw)
    claim_path.chmod(0o400)
    result["isolation"]["launch_evidence_sha256"] = digest(result["launcher_evidence"])
    result["receipt_sha256"] = self_hash({key: item for key, item in result.items() if key != "evidence"})
    result["evidence"] = ["nixos-a3-successor-evaluation:" + result["receipt_sha256"]]
    with pytest.raises(A3EvaluationError, match="signature"):
        validate_success(result, request)


@pytest.mark.parametrize("authority_attack", ["claim-unlink", "public-key-replace"])
def test_success_requires_live_exact_durable_crypto_authority(tmp_path: Path, authority_attack: str) -> None:
    request, _ = request_fixture(tmp_path)
    result = success_fixture(request)
    if authority_attack == "claim-unlink":
        reference = result["launcher_evidence"][0]["replay_claim_ref"]
        (Path(request["validation_authority"]["replay_root"]["path"]) / reference["name"]).unlink()
    else:
        public_path = Path(request["validation_authority"]["attestation_public_key"]["path"])
        public_path.chmod(0o600)
        public_path.write_bytes(b"x" * 32)
        public_path.chmod(0o400)
    with pytest.raises((A3EvaluationError, OSError)):
        validate_success(result, request)


def test_integration_contract_not_executable_fixture_is_source_truth() -> None:
    contract = json.loads(Path("agent-services/providers/nixos-a3-successor-integration-NOT-EXECUTABLE.json").read_text())
    assert validate_integration_contract(contract)["status"] == "NOT_EXECUTABLE"
    assert contract["commit"] is None and contract["archive_ref"] is None


def test_contract_matches_actual_admitted_module_and_outputs() -> None:
    module = Path("nix/a3-platform-bootstrap.nix").read_text()
    fixture = Path("nix/review-fixtures/tgw-prod-a3-successor-integration.nix").read_text()
    assert "options.services.tgw-a3-platform-bootstrap" in module
    assert "services.tgw-a3-platform-bootstrap" in fixture
    for option in ("package", "wrapperConfig", "composition", "prerequisiteReceipt", "attestationPublicKey", "sshAuthorizedPublicKey"):
        assert f"{option} =" in fixture
    assert "tgw.a3PlatformBootstrap" not in fixture
    assert "tgw-a3-platform-bootstrap.service" not in module
    assert set(RENDERED_PATHS.values()) == {
        "sw/bin/tgw-nix-observer-render-wrapper",
        "etc/tgw/nix-observer-render-wrapper.conf",
        "etc/tgw/nix-observer-render-composition.json",
        "etc/tgw/nix-observer-render-prerequisite.json",
        "etc/tgw/nix-observer-render-attestation.pub",
        "etc/sudoers",
        "etc/ssh/authorized_keys.d/codex",
        "etc/ssh/sshd_config",
        "etc/systemd/system/sshd.service",
    }


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("output_path",), f"/nix/store/{STORE_HASH}-nixos-system-other-26.08"),
        (("store_manifest",), []),
        (("effects", "activate"), True),
        (("cleanup",), "UNKNOWN"),
        (("rendered_artifacts",), {}),
        (("verifiers", "sshd", "returncode"), 1),
        (("deployable",), True),
    ],
)
def test_success_mutation_matrix(tmp_path: Path, path: tuple[str, ...], replacement: Any) -> None:
    request, _ = request_fixture(tmp_path)
    result = success_fixture(request)
    validate_success(result, request)
    with pytest.raises(A3EvaluationError):
        validate_success(_mutate(result, path, replacement), request)


@pytest.mark.parametrize(
    "path",
    [
        ("store_manifest", 0, "nar_size"),
        ("rendered_artifacts", "sshd-service", "size"),
        ("rendered_artifacts", "sshd-service", "file_identity", "dev"),
        ("rendered_artifacts", "sshd-service", "file_identity", "uid"),
        ("rendered_artifacts", "sshd-service", "file_identity", "mode"),
        ("rendered_artifacts", "sshd-service", "file_identity", "nlink"),
        ("isolation", "command_count"),
    ],
)
def test_success_numeric_fields_reject_bool_after_outer_hashes_recomputed(tmp_path: Path, path: tuple[Any, ...]) -> None:
    request, _ = request_fixture(tmp_path)
    result = success_fixture(request)
    cursor: Any = result
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = True
    if path[0] == "store_manifest":
        result["store_manifest_sha256"] = digest(result["store_manifest"])
    result["receipt_sha256"] = self_hash({key: item for key, item in result.items() if key != "evidence"})
    result["evidence"] = ["nixos-a3-successor-evaluation:" + result["receipt_sha256"]]
    with pytest.raises(A3EvaluationError):
        validate_success(result, request)


def test_actual_sshd_unit_is_required_and_synthetic_unit_is_rejected(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    assert request["expected_rendered"]["sshd-service"]["relative_path"] == "etc/systemd/system/sshd.service"
    missing = success_fixture(request)
    del missing["rendered_artifacts"]["sshd-service"]
    missing["receipt_sha256"] = self_hash({key: item for key, item in missing.items() if key != "evidence"})
    missing["evidence"] = ["nixos-a3-successor-evaluation:" + missing["receipt_sha256"]]
    with pytest.raises(A3EvaluationError, match="incomplete or broadened"):
        validate_success(missing, request)
    synthetic = success_fixture(request)
    synthetic["rendered_artifacts"]["a3-successor-rendered.service"] = synthetic["rendered_artifacts"]["sshd-service"]
    synthetic["receipt_sha256"] = self_hash({key: item for key, item in synthetic.items() if key != "evidence"})
    synthetic["evidence"] = ["nixos-a3-successor-evaluation:" + synthetic["receipt_sha256"]]
    with pytest.raises(A3EvaluationError, match="incomplete or broadened"):
        validate_success(synthetic, request)


def test_provider_persists_exact_success_and_refuses_bad_store_ref(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    good = MemoryStore()
    provider = A3TestSuccessorEvaluationProvider(A3EvaluationComposition(request["integration"], good, A3TestTransport(lambda _: success_fixture(request)), allow_fixture=True))
    result = provider({"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request})
    assert result["store_ref"]["sha256"] == result["terminal"]["receipt_sha256"]
    bad = A3TestSuccessorEvaluationProvider(
        A3EvaluationComposition(request["integration"], MemoryStore(wrong=True), A3TestTransport(lambda _: success_fixture(request)), allow_fixture=True)
    )
    with pytest.raises(A3EvaluationAmbiguous) as raised:
        bad({"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request})
    assert raised.value.evidence[0].startswith("nixos-a3-successor-evaluation-memory:sha256:")


def test_immutable_store_accepts_success_and_ambiguity_receipts(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    store = ImmutableEvaluationStore(root)
    success = success_fixture(request)
    reference = store.persist(success)
    assert reference["sha256"] == success["receipt_sha256"]
    provider = A3TestSuccessorEvaluationProvider(
        A3EvaluationComposition(request["integration"], store, A3TestTransport(lambda _: (_ for _ in ()).throw(OSError("lost"))), allow_fixture=True)
    )
    with pytest.raises(A3EvaluationAmbiguous) as raised:
        provider({"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request})
    assert raised.value.evidence[0].startswith("nixos-a3-successor-evaluation-terminal:sha256:")


def test_immutable_store_rejects_short_write_and_root_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request, _ = request_fixture(tmp_path)
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    store = ImmutableEvaluationStore(root)
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, raw: 0 if raw else real_write(fd, raw))
    with pytest.raises(OSError, match="short"):
        store.persist(success_fixture(request))
    monkeypatch.setattr(os, "write", real_write)
    root.rename(tmp_path / "held-receipts")
    root.mkdir(mode=0o777)
    root.chmod(0o777)
    with pytest.raises(A3EvaluationError, match="root identity changed"):
        store.persist(success_fixture(request))


def test_immutable_store_cleanup_loss_is_explicit_persistence_ambiguity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request, _ = request_fixture(tmp_path)
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    store = ImmutableEvaluationStore(root)
    success = success_fixture(request)
    monkeypatch.setattr(os, "write", lambda _fd, _raw: 0)
    monkeypatch.setattr(os, "unlink", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unlink refused")))
    with pytest.raises(A3EvaluationError, match="persistence ambiguity"):
        store.persist(success)


def test_immutable_store_rejects_named_replacement_during_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request, _ = request_fixture(tmp_path)
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    store = ImmutableEvaluationStore(root)
    success = success_fixture(request)
    real_fsync = os.fsync
    replaced = False

    def attacking_fsync(fd: int) -> None:
        nonlocal replaced
        metadata = os.fstat(fd)
        if stat.S_ISREG(metadata.st_mode) and not replaced:
            replaced = True
            names = list(root.iterdir())
            assert len(names) == 1
            names[0].rename(root / "displaced")
            (root / names[0].name).write_bytes(b"replacement")
            (root / names[0].name).chmod(0o400)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", attacking_fsync)
    with pytest.raises(A3EvaluationError, match="named readback mismatch"):
        store.persist(success)


def test_default_registry_has_distinct_unavailable_handler(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    request["integration"] = json.loads(Path("agent-services/providers/nixos-a3-successor-integration-NOT-EXECUTABLE.json").read_text())
    request["request_sha256"] = self_hash(request, "request_sha256")
    registry = TypedEffectHandlerRegistry(release_install=lambda _: {}, release_rollback=lambda _: {}, flake_push=lambda _: {}, flake_switch_record=lambda _: {}, dependency_resubmit=lambda _: {})
    with pytest.raises(Exception, match="not mounted"):
        registry.prepare(TypedEffect(EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION, "g1", request))


def test_unready_successor_never_consumes_authority(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    request["integration"] = json.loads(Path("agent-services/providers/nixos-a3-successor-integration-NOT-EXECUTABLE.json").read_text())
    request["request_sha256"] = self_hash(request, "request_sha256")
    consume_calls: list[str] = []
    registry = TypedEffectHandlerRegistry(
        release_install=lambda _: {},
        release_rollback=lambda _: {},
        flake_push=lambda _: {},
        flake_switch_record=lambda _: {},
        dependency_resubmit=lambda _: {},
    )
    controller = AuthorityEffectController(registry, lambda request_id, **_: consume_calls.append(request_id))
    with pytest.raises(Exception, match="not mounted"):
        controller.execute(
            request_id="request-1",
            effect=TypedEffect(EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION, "g1", request),
        )
    assert consume_calls == []


def test_mounted_not_executable_successor_never_consumes_authority(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    contract = json.loads(Path("agent-services/providers/nixos-a3-successor-integration-NOT-EXECUTABLE.json").read_text())
    request["integration"] = contract
    request["request_sha256"] = self_hash(request, "request_sha256")
    with pytest.raises(TypeError, match="allow_fixture"):
        A3TestSuccessorEvaluationProvider(A3EvaluationComposition(contract, MemoryStore(), A3TestTransport(lambda _: success_fixture(request))))
    provider = None
    registry = TypedEffectHandlerRegistry(
        release_install=lambda _: {},
        release_rollback=lambda _: {},
        flake_push=lambda _: {},
        flake_switch_record=lambda _: {},
        dependency_resubmit=lambda _: {},
        nixos_a3_successor_evaluation=provider,
    )
    consumed: list[str] = []
    controller = AuthorityEffectController(registry, lambda request_id, **_: consumed.append(request_id))
    with pytest.raises(Exception, match="not mounted"):
        controller.execute(
            request_id="request-1",
            effect=TypedEffect(EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION, "g1", request),
        )
    assert consumed == []


def test_known_failure_is_persisted_failed_and_store_loss_is_ambiguous(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    store = MemoryStore()

    def known(_: Mapping[str, Any]) -> Mapping[str, Any]:
        raise A3KnownFailure(
            "nix refused",
            stage="nix-build",
            step="nix-build",
            returncode=17,
            stdout=b"known stdout",
            stderr=b"known stderr",
        )

    provider = A3TestSuccessorEvaluationProvider(A3EvaluationComposition(request["integration"], store, A3TestTransport(known), allow_fixture=True))
    with pytest.raises(A3EvaluationFailure) as raised:
        provider({"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request})
    terminal = raised.value.terminal
    assert terminal["outcome"] == "FAILED" and terminal["returncode"] == 17
    assert terminal["stdout_sha256"] == digest(b"known stdout")
    assert terminal["effects"]["build"] is True

    registry = TypedEffectHandlerRegistry(
        release_install=lambda _: {},
        release_rollback=lambda _: {},
        flake_push=lambda _: {},
        flake_switch_record=lambda _: {},
        dependency_resubmit=lambda _: {},
        nixos_a3_successor_evaluation=provider,
    )
    controller = AuthorityEffectController(
        registry,
        lambda _request_id, **_: {"receipt_id": "authority-receipt-1"},
    )
    execution = controller.execute(
        request_id="request-1",
        effect=TypedEffect(EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION, "g1", request),
    )
    assert execution.outcome is EffectOutcome.FAILED
    assert execution.evidence[0].startswith("nixos-a3-successor-evaluation-terminal:sha256:")

    class RefusingStore:
        def persist(self, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
            raise OSError("store unavailable")

    lost = A3TestSuccessorEvaluationProvider(
        A3EvaluationComposition(request["integration"], RefusingStore(), A3TestTransport(lambda _: success_fixture(request)), allow_fixture=True)
    )
    with pytest.raises(A3EvaluationAmbiguous) as ambiguous:
        lost({"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request})
    assert ambiguous.value.evidence[0].startswith("nixos-a3-successor-evaluation-memory:sha256:")


def test_sql_migration_admits_only_exact_successor_kind() -> None:
    sql = Path("src/tgw/plan_authority.sql").read_text()
    assert sql.count("'nixos-a3-successor-evaluation'") == 1
    assert "nixos-a3-successor-evaluation@1" not in sql
    assert "generic" not in sql


def test_cli_reads_stdin_only_and_emits_handler_evidence(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    provider = A3TestSuccessorEvaluationProvider(
        A3EvaluationComposition(request["integration"], MemoryStore(), A3TestTransport(lambda _: success_fixture(request)), allow_fixture=True)
    )
    envelope = {"kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value, "generation": "g1", "parameters": request}
    output = io.StringIO()
    assert main([], input_stream=io.StringIO(json.dumps(envelope)), output_stream=output, provider=provider) == 0
    assert json.loads(output.getvalue())["terminal"]["schema"] == SUCCESS_SCHEMA
    with pytest.raises(SystemExit):
        main(["--command", "switch"], input_stream=io.StringIO("{}"), output_stream=io.StringIO(), provider=provider)


def test_cli_missing_provider_persists_exact_request_failed_receipt(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    envelope = {
        "kind": EffectKind.NIXOS_A3_SUCCESSOR_EVALUATION.value,
        "generation": "g1",
        "parameters": request,
    }
    store = MemoryStore()
    output = io.StringIO()
    assert main([], input_stream=io.StringIO(json.dumps(envelope)), output_stream=output, receipt_store=store) == 1
    terminal = json.loads(output.getvalue())
    assert terminal["outcome"] == "FAILED"
    assert terminal["request_sha256"] == request["request_sha256"]
    assert terminal["stage"] == "stdin-or-provider"
    assert store.values == [terminal]


def test_archive_rejects_links_and_wrong_commit(tmp_path: Path) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", pax_headers={"comment": "a" * 40}) as archive:
        item = tarfile.TarInfo("root/link")
        item.type = tarfile.SYMTYPE
        item.linkname = "/etc/passwd"
        archive.addfile(item)
    with pytest.raises(A3EvaluationError, match="non-regular"):
        verify_git_archive(raw.getvalue(), tmp_path / "out", expected_root="root", expected_commit="a" * 40, expected_tree="b" * 40, max_files=10, max_bytes=1_000_000)


@pytest.mark.parametrize("unit_attack", [None, "replace", "missing"])
def test_remote_fake_nix_systemd_sshd_e2e(tmp_path: Path, unit_attack: str | None) -> None:
    request, integration_archive = request_fixture(tmp_path)
    product_archive = subprocess.run(
        ["git", "-c", "safe.directory=/opt/TGW/tgw-lib/src/trader-grims-warehouse", "archive", "--format=tar", "--prefix=trader-grims-warehouse/", SOURCE_COMMIT],
        cwd=Path.cwd(),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert digest(product_archive) == SOURCE_ARCHIVE_SHA256
    output_root = tmp_path / "fake-store-output"
    for name, path in RENDERED_PATHS.items():
        target = output_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        public_name = Path(INTEGRATION_PUBLIC_FILES[name]).name if name in INTEGRATION_PUBLIC_FILES else None
        target.write_bytes(
            Path(request["validation_authority"]["attestation_public_key"]["path"]).read_bytes()
            if name == "attestation-public-key"
            else PUBLIC_FIXTURE_VALUES[public_name].encode()
            if public_name is not None
            else SSHD_SERVICE_FIXTURE
            if name == "sshd-service"
            else (name + "\n").encode()
        )

    seen: list[tuple[list[str], Mapping[str, str]]] = []

    def runner(argv: list[str], **kwargs: Any) -> Completed:
        env = kwargs["env"]
        seen.append((argv, env))
        assert argv[0].startswith("/proc/")
        assert env["NIX_REMOTE"] == "local" and env["PATH"] == ""
        assert "substituters =\n" in env["NIX_CONFIG"] and "builders =\n" in env["NIX_CONFIG"]
        joined = " ".join(argv)
        assert not any(word in joined for word in ("switch", "profile", "/home/db", "--impure"))
        if " eval " in f" {joined} ":
            return Completed(0, (DRV + "\n").encode(), b"")
        if " build " in f" {joined} ":
            assert "--no-link" in argv and "--offline" in argv and "--no-write-lock-file" in argv
            return Completed(0, (OUTPUT + "\n").encode(), b"")
        if " derivation show " in f" {joined} ":
            return Completed(0, json.dumps({DRV: {"outputs": {"out": {"path": OUTPUT}}}}).encode(), b"")
        if " --query --requisites " in f" {joined} ":
            dependency = f"/nix/store/{'7' * 32}-dependency"
            return Completed(0, ("\n".join(sorted((OUTPUT, dependency))) + "\n").encode(), b"")
        if " path-info " in f" {joined} ":
            path = next(item for item in reversed(argv) if item.startswith("/nix/store/"))
            digest_hex = "6" * 64 if path == OUTPUT else "8" * 64 if path.endswith("-dependency") else "2" * 64
            size = 4096 if path == OUTPUT else 2048 if path.endswith("-dependency") else 1024
            return Completed(0, json.dumps({path: {"narHash": "sha256:" + digest_hex, "narSize": size}}).encode(), b"")
        if " hash path " in f" {joined} ":
            path = argv[-1]
            digest_hex = "6" * 64 if path == OUTPUT else "8" * 64 if path.endswith("-dependency") else "2" * 64
            return Completed(0, (digest_hex + "\n").encode(), b"")
        if " verify " in f" {joined} ":
            unit = Path(argv[-1])
            assert unit.is_absolute() and unit.name == "sshd.service" and not str(unit).startswith("/proc/")
            completed = subprocess.run(["/usr/bin/systemd-analyze", *argv[1:]], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if unit_attack == "replace":
                displaced = unit.with_name("sshd.service.displaced")
                unit.rename(displaced)
                unit.write_bytes(SSHD_SERVICE_FIXTURE)
                unit.unlink()
                displaced.rename(unit)
            elif unit_attack == "missing":
                unit.unlink()
            return Completed(completed.returncode, completed.stdout, completed.stderr)
        return Completed(0, b"", b"")

    if unit_attack is not None:
        with pytest.raises(A3KnownFailure, match="verifier input (changed|named path disappeared)"):
            execute(
                request,
                tgw_archive=product_archive,
                integration_archive=integration_archive,
                runner=runner,
                scratch_parent=tmp_path,
                allow_fixture=True,
                output_resolver=lambda _: output_root,
            )
        return
    result = execute(
        request,
        tgw_archive=product_archive,
        integration_archive=integration_archive,
        runner=runner,
        scratch_parent=tmp_path,
        allow_fixture=True,
        output_resolver=lambda _: output_root,
    )
    # The fixture uses a mutable tmp output resolver, so production validation
    # correctly refuses its non-store inode identity.  The executor still proves
    # the fake Nix command, isolation, archive, closure, and cleanup flow.
    with pytest.raises(A3EvaluationError, match="held identity"):
        validate_success(result, request)
    assert result["deployable"] is False and result["effects"]["build"] is True
    assert all(result["effects"][name] is False for name in result["effects"] if name != "build")
    assert len(seen) == 16


def test_remote_cleanup_failure_is_never_success(tmp_path: Path) -> None:
    request, integration_archive = request_fixture(tmp_path)
    with pytest.raises(A3EvaluationError, match="cleanup failed"):
        execute(
            request,
            tgw_archive=b"wrong",
            integration_archive=integration_archive,
            runner=lambda *_args, **_kwargs: Completed(0, b"", b""),
            scratch_parent=tmp_path,
            allow_fixture=True,
            cleanup=lambda _: (_ for _ in ()).throw(OSError("refuse")),
        )


def test_remote_refuses_input_nar_size_before_build(tmp_path: Path) -> None:
    request, integration_archive = request_fixture(tmp_path)
    request["input_closure"]["paths"][0]["nar_size"] = 2048
    request["input_closure"]["manifest_sha256"] = digest(request["input_closure"]["paths"])
    request["input_closure"]["manifest_ref"] = "manifest:" + request["input_closure"]["manifest_sha256"]
    request["request_sha256"] = self_hash(request, "request_sha256")
    product_archive = subprocess.run(
        [
            "git",
            "-c",
            "safe.directory=/opt/TGW/tgw-lib/src/trader-grims-warehouse",
            "archive",
            "--format=tar",
            "--prefix=trader-grims-warehouse/",
            SOURCE_COMMIT,
        ],
        cwd=Path.cwd(),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: Any) -> Completed:
        calls.append(argv)
        path = request["input_closure"]["paths"][0]["path"]
        return Completed(0, json.dumps({path: {"narSize": 1024}}).encode(), b"")

    with pytest.raises(A3EvaluationError, match="NAR size mismatch"):
        execute(
            request,
            tgw_archive=product_archive,
            integration_archive=integration_archive,
            runner=runner,
            scratch_parent=tmp_path,
            allow_fixture=True,
        )
    assert len(calls) == 5 and all("build" not in call for call in calls)


def test_live_nix_gate_is_explicit_and_not_claimed() -> None:
    contract = json.loads(Path("agent-services/providers/nixos-a3-successor-integration-NOT-EXECUTABLE.json").read_text())
    assert contract["live_gate"] == "external:tgw-prod-flake-import-build-and-sshd-T"
    assert contract["status"] == "NOT_EXECUTABLE"

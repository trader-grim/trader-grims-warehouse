"""Closed, non-activating evaluation of the reviewed tgw-prod A3 successor.

This capability is deliberately separate from review-egress and observer-render.
It can build and statically inspect one exact NixOS successor, but has no
activation, profile, deployment, GC-root, or live-flake write operation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

EFFECT_KIND = "nixos-a3-successor-evaluation"
HANDLER_ID = "nixos-a3-successor-evaluation@1"
REQUEST_SCHEMA = "tgw-nixos-a3-successor-evaluation-request/v1"
COMPOSITION_SCHEMA = "tgw-nixos-a3-successor-evaluation-composition/v1"
INTEGRATION_SCHEMA = "tgw-nixos-a3-successor-integration/v1"
SUCCESS_SCHEMA = "tgw-nixos-a3-successor-evaluation-success/v1"
TERMINAL_SCHEMA = "tgw-nixos-a3-successor-evaluation-terminal/v1"
STORE_REF_SCHEMA = "tgw-nixos-a3-successor-evaluation-store-ref/v1"

PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
PLAN_SOLUTION = "sha256:d28650c26c6a3d26d6c943597ccb7abd7c6670b1703d9ce941ac5ed7a2d73a4d"
PLAN_CLOSURE = "sha256:bc0c53b2574fc359c629bd213e078fdd2824e5e1c4a98c0c7a347de869d9e6f8"
SOURCE_COMMIT = "f3cefe544a9f81422b57707c4289f2974c6dca51"
SOURCE_TREE = "2c6cc6199827aa8ce87686c02cdccb1c0373cca3"
SOURCE_ARCHIVE_SHA256 = "sha256:72f3ed988e1fdc132d6da19d6332321389d41e22c114a7b4fa14e95755c5889f"
SOURCE_CANDIDATE = "candidate:sha256:8ff4d73162a3458dce5e048df3a3586c4917f58b2fc905b008ee9359d667c761"
SOURCE_CATALOG = "sha256:24313e9eafaadf1180bf45b27369e6162b26109eecdf8bab36498441575a21f2"
TARGET_ATTR = "nixosConfigurations.tgw-prod.config.system.build.toplevel"

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,255}$")
_STORE = re.compile(r"^/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+$")
_OUTPUT = re.compile(r"^/nix/store/[0-9a-df-np-sv-z]{32}-nixos-system-tgw-prod-[A-Za-z0-9+._?=-]+$")

A3_SOURCE_IDENTITIES: Mapping[str, Mapping[str, Any]] = {
    "authority": {"path": "src/tgw/bootstrap_authority.py", "sha256": "sha256:0827d8edb8885667ce5584ff6b873236b7068c3eab1eed463104d489d6f51a33", "size": 21879},
    "controller": {"path": "src/tgw/nixos_observer_render_evaluation.py", "sha256": "sha256:ff9401ab9273203bb9a3b30c5328fe1b340b8246fbfa17845dcb3f576941adb3", "size": 86260},
    "deployment_runtime": {"path": "src/tgw/deployment_runtime.py", "sha256": "sha256:b232829594d4fe5fd148e9b7cf7c825407bdd394bb2b8659defc8525e241e788", "size": 3443},
    "effect_handlers": {"path": "src/tgw/effect_handlers.py", "sha256": "sha256:58571a1fc82ab61de918460a32f9610a78dee0cd9a3c2cd2c7936ebdebd43e56", "size": 34282},
    "flake": {"path": "flake.nix", "sha256": "sha256:a214e0ad62014556603f8cf484c45fbd9205e25bfd9d1667b620cb037309c259", "size": 7650},
    "helper": {"path": "src/tgw/nix_observer_render_helper.py", "sha256": "sha256:bfbd824429a1449f50166b71417c010c48b60f3d579e6050fb082d8d41724eb9", "size": 112714},
    "module": {"path": "nix/a3-platform-bootstrap.nix", "sha256": "sha256:5fc75b67452f1e43bf8b7c14e31e96672875762e700bd21bc65649e8cba447f6", "size": 4742},
    "native_transport_c": {"path": "src/native/tgw_nix_observer_render_transport.c", "sha256": "sha256:a730315f7f5963473e66492d80d6b3d8baaa1eac6a6be497d508cb16b2902dd8", "size": 31341},
    "package": {"path": "nix/a3-platform-bootstrap-package.nix", "sha256": "sha256:81da0876fa9e98255e8e75a8a794cf410a58291e1d304ef2772e3751584a20e5", "size": 880},
    "platform_bootstrap": {"path": "src/tgw/platform_bootstrap.py", "sha256": "sha256:f49f0fecec1d09023427ad5c0a15d335ff6f894f82f79c0fa1aceedde89bc142", "size": 38052},
    "remote_bootstrap": {"path": "src/tgw/nix_observer_render_remote.py", "sha256": "sha256:36214b1ab1fd617c41bf5b45acab353f25c68b2a5417721f385ed98bf2c36980", "size": 2201},
}

TOOL_NAMES = ("git", "tar", "nix", "nix_store", "systemd_analyze", "sshd")
RENDERED_ARTIFACTS = (
    "a3-package",
    "a3-module",
    "native-wrapper",
    "remote-helper",
    "wrapper-config",
    "render-composition",
    "sudoers",
    "authorized-key",
    "systemd-unit",
    "sshd-effective-config",
)
RENDERED_RELATIVE_PATHS = {
    "a3-package": "share/tgw/a3/a3-platform-bootstrap-package.nix",
    "a3-module": "share/tgw/a3/a3-platform-bootstrap.nix",
    "native-wrapper": "libexec/tgw-nix-observer-render-transport",
    "remote-helper": "libexec/tgw-nix-observer-render-remote",
    "wrapper-config": "etc/tgw/a3/wrapper.conf",
    "render-composition": "etc/tgw/a3/render-composition.json",
    "sudoers": "etc/sudoers.d/tgw-a3-platform-bootstrap",
    "authorized-key": "etc/ssh/authorized_keys.d/tgw-a3-bootstrap",
    "systemd-unit": "etc/systemd/system/tgw-a3-platform-bootstrap.service",
    "sshd-effective-config": "etc/ssh/sshd_config",
}
FORBIDDEN_EFFECTS = (
    "activate",
    "profile_write",
    "home_db_write",
    "live_flake_write",
    "gc_root_write",
    "deploy",
    "network",
    "lock_write",
    "substitute",
)


class A3EvaluationError(ValueError):
    """A request/result is outside the closed successor-evaluation contract."""


class A3EvaluationHold(A3EvaluationError):
    """The reviewed production integration is deliberately not executable."""


class A3EvaluationAmbiguous(A3EvaluationError):
    def __init__(self, message: str, observation: Mapping[str, Any], *, persisted_evidence: Sequence[str] = ()):
        super().__init__(message)
        self.observation = dict(observation)
        self.persisted_evidence = tuple(persisted_evidence)

    @property
    def evidence(self) -> tuple[str, ...]:
        return self.persisted_evidence or ("nixos-a3-successor-evaluation-memory:" + digest(self.observation),)


class ReceiptStore(Protocol):
    def persist(self, receipt: Mapping[str, Any]) -> Mapping[str, Any]: ...


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else canonical(value)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def self_hash(value: Mapping[str, Any], field: str = "receipt_sha256") -> str:
    return digest({key: item for key, item in value.items() if key != field})


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise A3EvaluationError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise A3EvaluationError(f"{label} is not an exact SHA-256 identity")
    return value


def _no_secret_fields(value: Any, trail: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("private", "secret", "password", "seed")):
                raise A3EvaluationError(f"secret-bearing field is forbidden: {'.'.join((*trail, str(key)))}")
            _no_secret_fields(item, (*trail, str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _no_secret_fields(item, (*trail, str(index)))
    elif isinstance(value, str) and ("OPENSSH PRIVATE KEY" in value or "PRIVATE KEY-----" in value):
        raise A3EvaluationError("secret key bytes are forbidden")


def validate_file_identity(value: Any, *, label: str) -> dict[str, Any]:
    item = dict(_exact(value, {"path", "sha256", "size", "uid", "gid", "mode"}, label))
    path = item["path"]
    if not isinstance(path, str) or not path.startswith("/") or ".." in Path(path).parts:
        raise A3EvaluationError(f"{label} path is not absolute and normalized")
    _sha(item["sha256"], f"{label}.sha256")
    if not isinstance(item["size"], int) or item["size"] <= 0:
        raise A3EvaluationError(f"{label} size is invalid")
    if not isinstance(item["uid"], int) or not isinstance(item["gid"], int):
        raise A3EvaluationError(f"{label} ownership is invalid")
    if not isinstance(item["mode"], int) or item["mode"] & 0o022 or not item["mode"] & 0o111:
        raise A3EvaluationError(f"{label} must be a non-writable executable")
    return item


def validate_integration_contract(value: Any, *, allow_fixture: bool = False) -> dict[str, Any]:
    fields = {
        "schema",
        "status",
        "repository_id",
        "target_host",
        "system",
        "commit",
        "tree",
        "archive_ref",
        "archive_sha256",
        "archive_size",
        "flake_lock_sha256",
        "module_import",
        "exact_options",
        "changed_paths",
        "unrelated_diff",
        "public_credentials_final",
        "closure_final",
        "live_gate",
        "manifest_ref",
        "manifest_sha256",
    }
    contract = dict(_exact(value, fields, "integration contract"))
    fixed = {
        "schema": INTEGRATION_SCHEMA,
        "repository_id": "tgw-flake",
        "target_host": "tgw-prod",
        "system": "x86_64-linux",
        "module_import": "inputs.tgw-lib.nixosModules.a3-platform-bootstrap",
        "unrelated_diff": False,
        "live_gate": "external:tgw-prod-flake-import-build-and-sshd-T",
    }
    if any(contract.get(key) != expected for key, expected in fixed.items()):
        raise A3EvaluationError("integration contract broadens the exact tgw-prod A3 import")
    expected_options = {
        "tgw.a3PlatformBootstrap.enable": True,
        "tgw.a3PlatformBootstrap.authorizedPublicKeyRef": "external:root-owned-a3-authorized-ed25519-public-key",
        "tgw.a3PlatformBootstrap.attestationPublicKeyRef": "external:a3-attestation-ed25519-public-verifier",
    }
    if contract["exact_options"] != expected_options:
        raise A3EvaluationError("integration options are not the exact reviewed A3 option set")
    if contract["changed_paths"] != ["flake.lock", "flake.nix", "hosts/tgw-prod/a3-platform-bootstrap.nix"]:
        raise A3EvaluationError("integration changed-path set is not exact")
    manifest_payload = {key: item for key, item in contract.items() if key not in {"manifest_ref", "manifest_sha256"}}
    if contract["manifest_sha256"] != digest(manifest_payload) or contract["manifest_ref"] != "manifest:" + contract["manifest_sha256"]:
        raise A3EvaluationError("integration manifest identity is invalid")
    status = contract["status"]
    if status == "REVIEWED_EXECUTABLE":
        if not (_SHA1.fullmatch(str(contract["commit"])) and _SHA1.fullmatch(str(contract["tree"]))):
            raise A3EvaluationError("reviewed integration Git identities are absent")
        _sha(contract["archive_sha256"], "integration archive")
        _sha(contract["flake_lock_sha256"], "integration flake.lock")
        if not isinstance(contract["archive_ref"], str) or contract["archive_ref"] != "artifact:" + contract["archive_sha256"]:
            raise A3EvaluationError("integration archive reference is not content-addressed")
        if not isinstance(contract["archive_size"], int) or contract["archive_size"] <= 0:
            raise A3EvaluationError("integration archive size is invalid")
        if contract["closure_final"] is not True or contract["public_credentials_final"] is not True:
            raise A3EvaluationError("executable integration is not deployable-final")
    elif status == "TEST_FIXTURE_NON_DEPLOYABLE" and allow_fixture:
        if contract["closure_final"] is not False or contract["public_credentials_final"] is not False:
            raise A3EvaluationError("test integration must remain non-deployable")
        if not (_SHA1.fullmatch(str(contract["commit"])) and _SHA1.fullmatch(str(contract["tree"]))):
            raise A3EvaluationError("test integration identities are invalid")
        _sha(contract["archive_sha256"], "test integration archive")
        _sha(contract["flake_lock_sha256"], "test integration lock")
        if contract["archive_ref"] != "artifact:" + contract["archive_sha256"] or not isinstance(contract["archive_size"], int) or contract["archive_size"] <= 0:
            raise A3EvaluationError("test integration archive binding is invalid")
    elif status != "NOT_EXECUTABLE":
        raise A3EvaluationError("integration status is not closed")
    return contract


def validate_request(value: Any, *, allow_fixture: bool = False) -> dict[str, Any]:
    fields = {
        "schema",
        "operation_id",
        "plan",
        "source",
        "integration",
        "target",
        "input_closure",
        "tools",
        "credentials",
        "expected_rendered",
        "expected_verifiers",
        "policy",
        "request_sha256",
    }
    request = dict(_exact(value, fields, "A3 successor request"))
    _no_secret_fields(request)
    if request["schema"] != REQUEST_SCHEMA or not isinstance(request["operation_id"], str) or not _IDENTITY.fullmatch(request["operation_id"]):
        raise A3EvaluationError("request schema or operation identity is invalid")
    plan = _exact(request["plan"], {"commit", "solution_sha256", "closure_sha256"}, "plan binding")
    if plan != {"commit": PLAN_COMMIT, "solution_sha256": PLAN_SOLUTION, "closure_sha256": PLAN_CLOSURE}:
        raise A3EvaluationError("request is not bound to the approved Plan solution")
    source = _exact(request["source"], {"commit", "tree", "archive_ref", "archive_sha256", "archive_size", "candidate_identity", "catalog_sha256", "a3_identities"}, "source binding")
    source_fixed = {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "archive_sha256": SOURCE_ARCHIVE_SHA256, "candidate_identity": SOURCE_CANDIDATE, "catalog_sha256": SOURCE_CATALOG}
    if any(source.get(key) != expected for key, expected in source_fixed.items()):
        raise A3EvaluationError("request does not name the admitted immutable product source")
    if source["archive_ref"] != "artifact:" + SOURCE_ARCHIVE_SHA256 or not isinstance(source["archive_size"], int) or source["archive_size"] <= 0:
        raise A3EvaluationError("product archive reference or size is invalid")
    if source["a3_identities"] != A3_SOURCE_IDENTITIES:
        raise A3EvaluationError("the exact eleven A3 source identities are incomplete")
    integration = validate_integration_contract(request["integration"], allow_fixture=allow_fixture)
    target = _exact(request["target"], {"host", "system", "attribute", "expected_current"}, "target")
    if target["host"] != "tgw-prod" or target["system"] != "x86_64-linux" or target["attribute"] != TARGET_ATTR or not _STORE.fullmatch(str(target["expected_current"])):
        raise A3EvaluationError("target host/system/attribute/CAS is outside the fixed bound")
    closure = _exact(request["input_closure"], {"manifest_ref", "manifest_sha256", "paths"}, "input closure")
    paths = closure["paths"]
    if not isinstance(paths, list) or not paths or len(paths) > 10_000:
        raise A3EvaluationError("input closure is empty or oversized")
    normalized: list[dict[str, Any]] = []
    for entry in paths:
        item = dict(_exact(entry, {"path", "nar_sha256", "nar_size"}, "input closure entry"))
        if not _STORE.fullmatch(str(item["path"])):
            raise A3EvaluationError("input closure contains a non-store path")
        _sha(item["nar_sha256"], "input NAR")
        if not isinstance(item["nar_size"], int) or item["nar_size"] <= 0:
            raise A3EvaluationError("input NAR size is invalid")
        normalized.append(item)
    if normalized != sorted(normalized, key=lambda item: item["path"]) or len({item["path"] for item in normalized}) != len(normalized):
        raise A3EvaluationError("input closure paths are not strictly sorted and unique")
    _sha(closure["manifest_sha256"], "input closure manifest")
    if closure["manifest_sha256"] != digest(paths) or closure["manifest_ref"] != "manifest:" + closure["manifest_sha256"]:
        raise A3EvaluationError("input closure manifest binding is invalid")
    tools = _exact(request["tools"], set(TOOL_NAMES), "tool identities")
    for name in TOOL_NAMES:
        validate_file_identity(tools[name], label=name)
    credentials = _exact(request["credentials"], {"authorized_public_key_ref", "authorized_public_key_sha256", "attestation_public_key_ref", "attestation_public_key_sha256", "final"}, "credentials")
    for name in ("authorized_public_key", "attestation_public_key"):
        if not isinstance(credentials[name + "_ref"], str) or not credentials[name + "_ref"].startswith("external:"):
            raise A3EvaluationError("credential material must remain an external public reference")
        _sha(credentials[name + "_sha256"], name)
    if credentials["final"] is not bool(credentials["final"]):
        raise A3EvaluationError("credential finality must be boolean")
    expected_rendered = _exact(request["expected_rendered"], set(RENDERED_ARTIFACTS), "expected rendered identities")
    for name in RENDERED_ARTIFACTS:
        item = _exact(expected_rendered[name], {"relative_path", "sha256", "size"}, f"expected rendered {name}")
        relative = item["relative_path"]
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts or not relative:
            raise A3EvaluationError("expected rendered path is not normalized and relative")
        if relative != RENDERED_RELATIVE_PATHS[name]:
            raise A3EvaluationError("expected rendered path is not the exact A3 package layout")
        _sha(item["sha256"], f"expected rendered {name}")
        if not isinstance(item["size"], int) or item["size"] <= 0:
            raise A3EvaluationError("expected rendered size is invalid")
    expected_verifiers = _exact(request["expected_verifiers"], {"systemd_analyze", "sshd"}, "expected verifier outputs")
    for name in ("systemd_analyze", "sshd"):
        item = _exact(expected_verifiers[name], {"stdout_sha256", "stderr_sha256"}, f"expected {name} output")
        _sha(item["stdout_sha256"], f"expected {name} stdout")
        _sha(item["stderr_sha256"], f"expected {name} stderr")
    policy = _exact(
        request["policy"],
        {
            "offline",
            "nix_remote",
            "substituters",
            "builders",
            "use_substitutes",
            "allow_ifd",
            "write_lock_file",
            "no_link",
            "max_seconds",
            "max_output_bytes",
            "max_archive_bytes",
            "max_unpacked_bytes",
            "max_files",
        },
        "evaluation policy",
    )
    fixed_policy = {"offline": True, "nix_remote": "local", "substituters": [], "builders": [], "use_substitutes": False, "allow_ifd": False, "write_lock_file": False, "no_link": True}
    if any(policy.get(key) != expected for key, expected in fixed_policy.items()):
        raise A3EvaluationError("evaluation policy enables an impure or activating operation")
    bounds = (policy["max_seconds"], policy["max_output_bytes"], policy["max_archive_bytes"], policy["max_unpacked_bytes"], policy["max_files"])
    if any(not isinstance(item, int) for item in bounds) or not (
        1 <= bounds[0] <= 1800 and 1024 <= bounds[1] <= 64 * 1024 * 1024 and 1024 <= bounds[2] <= 256 * 1024 * 1024 and bounds[2] <= bounds[3] <= 1024 * 1024 * 1024 and 1 <= bounds[4] <= 200_000
    ):
        raise A3EvaluationError("evaluation resource bounds are outside the closed range")
    if integration["status"] == "REVIEWED_EXECUTABLE" and credentials["final"] is not True:
        raise A3EvaluationError("reviewed executable request lacks final public credential identities")
    if request["request_sha256"] != self_hash(request, "request_sha256"):
        raise A3EvaluationError("request self-hash mismatch")
    return request


def validate_success(value: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema",
        "outcome",
        "request_sha256",
        "operation_id",
        "source",
        "integration",
        "target",
        "derivation",
        "output_path",
        "store_manifest",
        "store_manifest_sha256",
        "rendered_artifacts",
        "verifiers",
        "effects",
        "cleanup",
        "deployable",
        "receipt_sha256",
        "evidence",
    }
    result = dict(_exact(value, fields, "A3 successor success receipt"))
    exact = {
        "schema": SUCCESS_SCHEMA,
        "outcome": "SUCCEEDED",
        "request_sha256": request["request_sha256"],
        "operation_id": request["operation_id"],
        "source": request["source"],
        "integration": request["integration"],
        "target": request["target"],
    }
    if any(result.get(key) != expected for key, expected in exact.items()):
        raise A3EvaluationError("success receipt is not bound to the exact request")
    if not isinstance(result["derivation"], str) or not _STORE.fullmatch(result["derivation"]) or not result["derivation"].endswith(".drv"):
        raise A3EvaluationError("success derivation is invalid")
    if not isinstance(result["output_path"], str) or not _OUTPUT.fullmatch(result["output_path"]):
        raise A3EvaluationError("success output is not the exact tgw-prod NixOS successor")
    manifest = result["store_manifest"]
    if not isinstance(manifest, list) or not manifest or len(manifest) > 100_000:
        raise A3EvaluationError("recursive store manifest is absent")
    for entry in manifest:
        item = _exact(entry, {"path", "nar_sha256", "nar_size"}, "store manifest entry")
        if not _STORE.fullmatch(str(item["path"])) or not _SHA256.fullmatch(str(item["nar_sha256"])) or not isinstance(item["nar_size"], int) or item["nar_size"] <= 0:
            raise A3EvaluationError("store manifest entry is invalid")
    manifest_paths = [item["path"] for item in manifest]
    if manifest_paths != sorted(set(manifest_paths)) or result["output_path"] not in manifest_paths or result["store_manifest_sha256"] != digest(manifest):
        raise A3EvaluationError("recursive store manifest is incomplete or tampered")
    rendered = result["rendered_artifacts"]
    if not isinstance(rendered, Mapping) or tuple(sorted(rendered)) != tuple(sorted(RENDERED_ARTIFACTS)):
        raise A3EvaluationError("rendered A3 artifact set is incomplete or broadened")
    for name in RENDERED_ARTIFACTS:
        item = _exact(rendered[name], {"path", "sha256", "size"}, f"rendered {name}")
        if (
            not isinstance(item["path"], str)
            or not item["path"].startswith(result["output_path"] + "/")
            or not _SHA256.fullmatch(str(item["sha256"]))
            or not isinstance(item["size"], int)
            or item["size"] <= 0
        ):
            raise A3EvaluationError("rendered A3 artifact identity is invalid")
        expected = request["expected_rendered"][name]
        if item != {
            "path": result["output_path"] + "/" + expected["relative_path"],
            "sha256": expected["sha256"],
            "size": expected["size"],
        }:
            raise A3EvaluationError("rendered A3 artifact differs from its admitted identity")
    verifiers = _exact(result["verifiers"], {"systemd_analyze", "sshd"}, "static verifiers")
    expected_commands = {
        "systemd_analyze": [request["tools"]["systemd_analyze"]["path"], "verify", "--root", result["output_path"], "tgw-a3-platform-bootstrap.service"],
        "sshd": [request["tools"]["sshd"]["path"], "-T", "-C", "user=tgw-a3-bootstrap,host=tgw-prod,addr=127.0.0.1", "-f", rendered["sshd-effective-config"]["path"]],
    }
    for name, command in expected_commands.items():
        item = _exact(verifiers[name], {"command", "executable", "returncode", "stdout_sha256", "stderr_sha256"}, f"{name} verifier")
        if item["command"] != command or item["executable"] != request["tools"][name] or item["returncode"] != 0:
            raise A3EvaluationError("static verifier provenance or result is invalid")
        _sha(item["stdout_sha256"], f"{name} stdout")
        _sha(item["stderr_sha256"], f"{name} stderr")
        if {
            "stdout_sha256": item["stdout_sha256"],
            "stderr_sha256": item["stderr_sha256"],
        } != request["expected_verifiers"][name]:
            raise A3EvaluationError("static verifier output differs from the admitted result")
    effects = _exact(result["effects"], {"build", *FORBIDDEN_EFFECTS}, "effect observation")
    if effects["build"] is not True or any(effects[name] is not False for name in FORBIDDEN_EFFECTS):
        raise A3EvaluationError("receipt reports a forbidden operational effect")
    if result["cleanup"] != "REMOVED":
        raise A3EvaluationError("isolated evaluation scratch cleanup was not observed")
    expected_deployable = request["integration"]["status"] == "REVIEWED_EXECUTABLE" and request["credentials"]["final"] is True
    if result["deployable"] is not expected_deployable:
        raise A3EvaluationError("deployability classification is untruthful")
    if result["receipt_sha256"] != self_hash({key: item for key, item in result.items() if key != "evidence"}):
        raise A3EvaluationError("success receipt self-hash mismatch")
    if result["evidence"] != ["nixos-a3-successor-evaluation:" + result["receipt_sha256"]]:
        raise A3EvaluationError("success evidence is not exact")
    return result


def terminal_receipt(*, request_sha256: str, outcome: str, stage: str, code: str, cleanup: str, observation: Mapping[str, Any]) -> dict[str, Any]:
    if outcome not in {"FAILED", "AMBIGUOUS"} or cleanup not in {"NOT_CREATED", "REMOVED", "UNKNOWN"}:
        raise A3EvaluationError("terminal classification is invalid")
    value: dict[str, Any] = {
        "schema": TERMINAL_SCHEMA,
        "outcome": outcome,
        "request_sha256": request_sha256,
        "stage": stage,
        "code": code,
        "cleanup": cleanup,
        "observation_sha256": digest(observation),
    }
    value["receipt_sha256"] = self_hash(value)
    value["evidence"] = ["nixos-a3-successor-evaluation-terminal:" + value["receipt_sha256"]]
    return value


@dataclass(frozen=True)
class A3EvaluationComposition:
    integration: Mapping[str, Any]
    receipt_store: ReceiptStore
    runner: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    allow_fixture: bool = False

    @property
    def status(self) -> str:
        return str(self.integration["status"])

    @property
    def receipt_sha256(self) -> str:
        return digest({"schema": COMPOSITION_SCHEMA, "integration": self.integration, "allow_fixture": self.allow_fixture})


class A3SuccessorEvaluationProvider:
    def __init__(self, composition: A3EvaluationComposition):
        self.composition = composition
        validate_integration_contract(composition.integration, allow_fixture=composition.allow_fixture)

    def __call__(self, effect: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(effect, Mapping) or set(effect) != {"kind", "generation", "parameters"} or effect["kind"] != EFFECT_KIND:
            raise A3EvaluationError("provider accepts only the distinct A3 successor effect envelope")
        request = validate_request(effect["parameters"], allow_fixture=self.composition.allow_fixture)
        if request["integration"] != self.composition.integration:
            raise A3EvaluationError("request integration differs from the mounted composition")
        if self.composition.status == "NOT_EXECUTABLE":
            raise A3EvaluationHold("reviewed tgw-flake integration archive/closure/public identities are not final")
        try:
            untrusted = self.composition.runner(request)
            result = validate_success(untrusted, request)
            reference = self.composition.receipt_store.persist(result)
        except A3EvaluationHold:
            raise
        except Exception as exc:
            observation = {
                "schema": "tgw-nixos-a3-successor-evaluation-observation/v1",
                "request_sha256": request["request_sha256"],
                "generation": effect["generation"],
                "type": type(exc).__name__,
                "detail": str(exc),
                "composition_sha256": self.composition.receipt_sha256,
            }
            terminal = terminal_receipt(
                request_sha256=request["request_sha256"],
                outcome="AMBIGUOUS",
                stage="evaluation-or-success-persistence",
                code=type(exc).__name__,
                cleanup="UNKNOWN",
                observation=observation,
            )
            try:
                terminal_ref = self.composition.receipt_store.persist(terminal)
                if terminal_ref != {
                    "schema": STORE_REF_SCHEMA,
                    "sha256": terminal["receipt_sha256"],
                    "size": len(canonical(terminal)),
                }:
                    raise A3EvaluationError("terminal store reference mismatch")
                evidence = ("nixos-a3-successor-evaluation-terminal:" + terminal["receipt_sha256"],)
            except Exception:
                evidence = ()
            raise A3EvaluationAmbiguous(
                "success or persistence evidence could not be validated",
                observation,
                persisted_evidence=evidence,
            ) from exc
        expected = {"schema": STORE_REF_SCHEMA, "sha256": result["receipt_sha256"], "size": len(canonical(result))}
        if reference != expected:
            observation = {
                "schema": "tgw-nixos-a3-successor-evaluation-observation/v1",
                "request_sha256": request["request_sha256"],
                "generation": effect["generation"],
                "type": "StoreReferenceMismatch",
                "detail": digest(reference),
                "composition_sha256": self.composition.receipt_sha256,
            }
            raise A3EvaluationAmbiguous("immutable store returned an invalid reference", observation)
        return {"evidence": ["nixos-a3-successor-evaluation:" + result["receipt_sha256"]], "terminal": result, "store_ref": reference}


class ImmutableEvaluationStore:
    """Minimal content-addressed receipt store; existing names never overwrite."""

    def __init__(self, root: Path, *, trusted_uid: int | None = None):
        self.root = Path(root)
        metadata = self.root.lstat()
        uid = os.getuid() if trusted_uid is None else trusted_uid
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise A3EvaluationError("receipt root must be a trusted mode-0700 directory")

    def persist(self, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = canonical(receipt)
        sha = receipt.get("receipt_sha256")
        if sha != self_hash({key: item for key, item in receipt.items() if key != "evidence"}):
            raise A3EvaluationError("refusing to persist a non-self-hashed receipt")
        name = str(sha).removeprefix("sha256:") + ".json"
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=root_fd)
            try:
                offset = 0
                while offset < len(raw):
                    count = os.write(fd, raw[offset:])
                    if count <= 0:
                        raise OSError("short immutable receipt write")
                    offset += count
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        return {"schema": STORE_REF_SCHEMA, "sha256": sha, "size": len(raw)}


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: io.TextIOBase | None = None,
    output_stream: io.TextIOBase | None = None,
    provider: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> int:
    """Read exactly one effect envelope from stdin; no generic CLI arguments."""
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    source = sys.stdin if input_stream is None else input_stream
    sink = sys.stdout if output_stream is None else output_stream
    if arguments:
        raise SystemExit("nixos-a3-successor-evaluation accepts no arguments")
    if provider is None:
        raise A3EvaluationHold("production A3 successor evaluation provider is not mounted")
    try:
        value = json.load(source)
        result = provider(value)
    except Exception as exc:
        fallback = terminal_receipt(request_sha256="sha256:" + "0" * 64, outcome="FAILED", stage="stdin-or-provider", code=type(exc).__name__, cleanup="NOT_CREATED", observation={"detail": str(exc)})
        sink.write(canonical(fallback).decode() + "\n")
        return 1
    sink.write(canonical(result).decode() + "\n")
    return 0

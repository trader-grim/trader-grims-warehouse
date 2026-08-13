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
LAUNCH_EVIDENCE_SCHEMA = "tgw-nixos-a3-launch-evidence/v1"
ATTESTATION_SCHEMA = "tgw-nixos-a3-local-netns-attestation/v1"
REPLAY_CLAIM_SCHEMA = "tgw-nixos-a3-launch-replay-claim/v1"
REPLAY_CLAIM_REF_SCHEMA = "tgw-nixos-a3-launch-replay-claim-ref/v1"
_PROVIDER_SEAL = object()

# Exact terminal tuples.  The key names the complete classification and the
# row fixes cleanup, build observation and return-code semantics.  Keeping the
# command step in the key prevents a new subprocess path from silently gaining
# authority merely because it happens to share a broad stage name.
TERMINAL_STATE_TABLE: Mapping[tuple[str, str, str, str], Mapping[str, Any]] = {
    ("FAILED", "composition-readiness", step, "A3KnownFailure"): {"cleanup": "NOT_CREATED", "build": False, "rc": "none"}
    for step in ("provider-mount", "request")
} | {
    ("FAILED", "stdin-or-provider", "stdin-parse-or-dispatch", "InputOrProviderFailure"): {
        "cleanup": "NOT_CREATED",
        "build": False,
        "rc": "none",
    },
} | {
    ("AMBIGUOUS", "stdin-or-provider", "stdin-parse-or-dispatch", code): {
        "cleanup": "UNKNOWN",
        "build": False,
        "rc": "none",
    }
    for code in ("ReceiptStoreUnavailable", "ReceiptStorePersistenceFailure")
} | {
    ("FAILED", "prebuild-validation", "contract-validation", "A3KnownFailure"): {"cleanup": "REMOVED", "build": False, "rc": "none"},
} | {
    ("FAILED", "post-build", step, "A3KnownFailure"): {"cleanup": "REMOVED", "build": True, "rc": "none"}
    for step in ("contract-validation", "success-validation", "tool-identity")
} | {
    ("FAILED", stage, step, "A3KnownFailure"): {"cleanup": "REMOVED", "build": built, "rc": "nonzero"}
    for stage, built, steps in (
        ("evaluation", False, ("nix-version", "nix-store-version", "sshd-version", "systemd-version", "path-info", "nix-hash", "nix-eval")),
        ("nix-build", True, ("nix-build",)),
        ("post-build", True, ("nix-store", "path-info", "nix-hash")),
        ("static-verification", True, ("sshd-verify", "systemd-verify")),
    )
    for step in steps
} | {
    ("FAILED", stage, step, "StepFailure"): {"cleanup": "REMOVED", "build": built, "rc": rc_rule}
    for stage, built in (("evaluation", False), ("nix-build", True), ("post-build", True), ("static-verification", True))
    for step, rc_rule in (
        ("launcher", "nonzero"),
        ("launcher-identity", "none"),
        ("timeout", "bounded-optional"),
        ("output-bound", "bounded-optional"),
    )
} | {
    ("AMBIGUOUS", stage, step, "StepFailure"): {"cleanup": "UNKNOWN", "build": built, "rc": "bounded-optional"}
    for stage, built in (("evaluation", False), ("nix-build", True), ("post-build", True), ("static-verification", True))
    for step in ("response", "response-contract", "timeout", "output-bound", "process-group", "process-state")
} | {
    ("FAILED", stage, "output-contract", "A3KnownFailure"): {"cleanup": "REMOVED", "build": built, "rc": "none"}
    for stage, built in (("evaluation", False), ("nix-build", True), ("post-build", True), ("static-verification", True))
} | {
    ("FAILED", stage, "attestation", "A3KnownFailure"): {"cleanup": "REMOVED", "build": built, "rc": "none"}
    for stage, built in (("evaluation", False), ("nix-build", True), ("post-build", True), ("static-verification", True))
} | {
    ("AMBIGUOUS", "evaluation-or-success-persistence", "unknown", "UnknownExternalState"): {
        "cleanup": "UNKNOWN",
        "build": True,
        "rc": "none",
    },
    ("AMBIGUOUS", "prebuild-terminal-classification", "unknown", "UnknownFailureTuple"): {
        "cleanup": "UNKNOWN",
        "build": False,
        "rc": "none",
    },
    ("AMBIGUOUS", "postbuild-terminal-classification", "unknown", "UnknownFailureTuple"): {
        "cleanup": "UNKNOWN",
        "build": True,
        "rc": "none",
    },
} | {
    ("AMBIGUOUS", stage, "process-state", "A3KnownFailure"): {"cleanup": "UNKNOWN", "build": built, "rc": "bounded-optional"}
    for stage, built in (("evaluation", False), ("nix-build", True), ("post-build", True), ("static-verification", True))
}

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

TOOL_NAMES = ("nix", "nix_store", "sshd", "systemd_analyze")
INTEGRATION_PUBLIC_FILES = {
    "authorized-key-codex": "a3-public/codex-authorized-key.txt",
    "attestation-public-key": "a3-public/nix-observer-render-attestation.pub",
    "render-composition": "a3-public/nix-observer-render-composition.json",
    "prerequisite-receipt": "a3-public/nix-observer-render-prerequisite.json",
    "wrapper-config": "a3-public/nix-observer-render-wrapper.conf",
}
RENDERED_ARTIFACTS = (
    "system-wrapper",
    "wrapper-config",
    "render-composition",
    "prerequisite-receipt",
    "attestation-public-key",
    "sudoers",
    "authorized-key-codex",
    "sshd-config",
    "sshd-service",
)
RENDERED_RELATIVE_PATHS = {
    "system-wrapper": "sw/bin/tgw-nix-observer-render-wrapper",
    "wrapper-config": "etc/tgw/nix-observer-render-wrapper.conf",
    "render-composition": "etc/tgw/nix-observer-render-composition.json",
    "prerequisite-receipt": "etc/tgw/nix-observer-render-prerequisite.json",
    "attestation-public-key": "etc/tgw/nix-observer-render-attestation.pub",
    "sudoers": "etc/sudoers",
    "authorized-key-codex": "etc/ssh/authorized_keys.d/codex",
    "sshd-config": "etc/ssh/sshd_config",
    "sshd-service": "etc/systemd/system/sshd.service",
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


class A3KnownFailure(A3EvaluationError):
    def __init__(self, message: str, *, stage: str, step: str, returncode: int | None = None, stdout: bytes = b"", stderr: bytes = b"", cleanup: str = "REMOVED"):
        super().__init__(message)
        self.stage = stage
        self.step = step
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cleanup = cleanup


class A3EvaluationFailure(A3EvaluationError):
    def __init__(self, message: str, terminal: Mapping[str, Any]):
        super().__init__(message)
        self.terminal = dict(terminal)

    @property
    def evidence(self) -> tuple[str, ...]:
        return ("nixos-a3-successor-evaluation-terminal:" + self.terminal["receipt_sha256"],)


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
        "public_files",
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
        "services.tgw-a3-platform-bootstrap.enable": True,
        "services.tgw-a3-platform-bootstrap.package": "inputs.tgw-lib.packages.x86_64-linux.a3-platform-bootstrap",
        "services.tgw-a3-platform-bootstrap.wrapperConfig": "../../a3-public/nix-observer-render-wrapper.conf",
        "services.tgw-a3-platform-bootstrap.composition": "../../a3-public/nix-observer-render-composition.json",
        "services.tgw-a3-platform-bootstrap.prerequisiteReceipt": "../../a3-public/nix-observer-render-prerequisite.json",
        "services.tgw-a3-platform-bootstrap.attestationPublicKey": "../../a3-public/nix-observer-render-attestation.pub",
        "services.tgw-a3-platform-bootstrap.sshAuthorizedPublicKey": "../../a3-public/codex-authorized-key.txt",
    }
    if contract["exact_options"] != expected_options:
        raise A3EvaluationError("integration options are not the exact reviewed A3 option set")
    public_files = _exact(contract["public_files"], set(INTEGRATION_PUBLIC_FILES), "integration public files")
    for name, relative_path in INTEGRATION_PUBLIC_FILES.items():
        identity = _exact(public_files[name], {"path", "sha256", "size"}, f"integration public file {name}")
        if identity["path"] != relative_path:
            raise A3EvaluationError("integration public file path is not exact")
    if contract["changed_paths"] != [
        "a3-public/codex-authorized-key.txt",
        "a3-public/nix-observer-render-attestation.pub",
        "a3-public/nix-observer-render-composition.json",
        "a3-public/nix-observer-render-prerequisite.json",
        "a3-public/nix-observer-render-wrapper.conf",
        "flake.lock",
        "flake.nix",
        "hosts/tgw-prod/a3-platform-bootstrap.nix",
    ]:
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
        for name, identity in public_files.items():
            _sha(identity["sha256"], f"integration public file {name}")
            if not isinstance(identity["size"], int) or identity["size"] <= 0:
                raise A3EvaluationError("executable integration public file identity is incomplete")
    elif status == "TEST_FIXTURE_NON_DEPLOYABLE" and allow_fixture:
        if contract["closure_final"] is not False or contract["public_credentials_final"] is not False:
            raise A3EvaluationError("test integration must remain non-deployable")
        if not (_SHA1.fullmatch(str(contract["commit"])) and _SHA1.fullmatch(str(contract["tree"]))):
            raise A3EvaluationError("test integration identities are invalid")
        _sha(contract["archive_sha256"], "test integration archive")
        _sha(contract["flake_lock_sha256"], "test integration lock")
        if contract["archive_ref"] != "artifact:" + contract["archive_sha256"] or not isinstance(contract["archive_size"], int) or contract["archive_size"] <= 0:
            raise A3EvaluationError("test integration archive binding is invalid")
        for name, identity in public_files.items():
            _sha(identity["sha256"], f"test integration public file {name}")
            if not isinstance(identity["size"], int) or identity["size"] <= 0:
                raise A3EvaluationError("test integration public file identity is incomplete")
    elif status != "NOT_EXECUTABLE":
        raise A3EvaluationError("integration status is not closed")
    elif any(identity["sha256"] is not None or identity["size"] is not None for identity in public_files.values()):
        raise A3EvaluationError("unreviewed integration must not claim public file identities")
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
        "expected_tool_versions",
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
    target = _exact(request["target"], {"host", "system", "attribute", "expected_current", "expected_successor"}, "target")
    if (
        target["host"] != "tgw-prod"
        or target["system"] != "x86_64-linux"
        or target["attribute"] != TARGET_ATTR
        or not _STORE.fullmatch(str(target["expected_current"]))
        or not _OUTPUT.fullmatch(str(target["expected_successor"]))
        or target["expected_current"] == target["expected_successor"]
    ):
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
    expected_tool_versions = _exact(request["expected_tool_versions"], set(TOOL_NAMES), "expected tool versions")
    for name in TOOL_NAMES:
        item = _exact(expected_tool_versions[name], {"stdout_sha256", "stderr_sha256"}, f"expected {name} version")
        _sha(item["stdout_sha256"], f"expected {name} version stdout")
        _sha(item["stderr_sha256"], f"expected {name} version stderr")
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
    if integration["status"] != "NOT_EXECUTABLE":
        if (
            integration["public_files"]["authorized-key-codex"]["sha256"] != credentials["authorized_public_key_sha256"]
            or integration["public_files"]["attestation-public-key"]["sha256"] != credentials["attestation_public_key_sha256"]
        ):
            raise A3EvaluationError("integration public credential identities differ from request credentials")
        for name in INTEGRATION_PUBLIC_FILES:
            public_identity = integration["public_files"][name]
            if {key: expected_rendered[name][key] for key in ("sha256", "size")} != {
                "sha256": public_identity["sha256"],
                "size": public_identity["size"],
            }:
                raise A3EvaluationError("integration public identity differs from expected rendered identity")
    expected_verifiers = _exact(request["expected_verifiers"], {"sshd", "systemd_analyze"}, "expected verifier outputs")
    for name in ("sshd", "systemd_analyze"):
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
    archive_sizes = (source["archive_size"], integration["archive_size"])
    if any(not isinstance(size, int) or size <= 0 or size > policy["max_archive_bytes"] for size in archive_sizes) or sum(archive_sizes) > policy["max_archive_bytes"]:
        raise A3EvaluationError("individual or total archive size exceeds the closed bound")
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
        "tool_versions",
        "verifiers",
        "isolation",
        "launcher_evidence",
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
    if result["output_path"] != request["target"]["expected_successor"] or not _OUTPUT.fullmatch(result["output_path"]):
        raise A3EvaluationError("success output is not the exact tgw-prod NixOS successor")
    manifest = result["store_manifest"]
    if not isinstance(manifest, list) or len(manifest) < 2 or len(manifest) > 100_000:
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
        item = _exact(rendered[name], {"path", "sha256", "size", "file_identity"}, f"rendered {name}")
        if (
            not isinstance(item["path"], str)
            or not item["path"].startswith(result["output_path"] + "/")
            or not _SHA256.fullmatch(str(item["sha256"]))
            or not isinstance(item["size"], int)
            or item["size"] <= 0
        ):
            raise A3EvaluationError("rendered A3 artifact identity is invalid")
        expected = request["expected_rendered"][name]
        if {key: item[key] for key in ("path", "sha256", "size")} != {
            "path": result["output_path"] + "/" + expected["relative_path"],
            "sha256": expected["sha256"],
            "size": expected["size"],
        }:
            raise A3EvaluationError("rendered A3 artifact differs from its admitted identity")
        identity = _exact(item["file_identity"], {"resolved_path", "dev", "ino", "uid", "gid", "mode", "nlink"}, f"rendered {name} held identity")
        if (
            not isinstance(identity["resolved_path"], str)
            or not identity["resolved_path"].startswith("/nix/store/")
            or any(not isinstance(identity[key], int) for key in ("dev", "ino", "uid", "gid", "mode", "nlink"))
            or identity["nlink"] < 1
        ):
            raise A3EvaluationError("rendered A3 held identity is invalid")
    tool_versions = _exact(result["tool_versions"], set(TOOL_NAMES), "tool version evidence")
    version_flags = {"nix": "--version", "nix_store": "--version", "sshd": "-V", "systemd_analyze": "--version"}
    for name in TOOL_NAMES:
        version = _exact(tool_versions[name], {"command", "actual_command", "executable", "returncode", "stdout_sha256", "stderr_sha256"}, f"{name} version")
        expected_command = [request["tools"][name]["path"], version_flags[name]]
        if (
            version["command"] != expected_command
            or version["executable"] != request["tools"][name]
            or version["returncode"] != 0
            or not isinstance(version["actual_command"], list)
            or len(version["actual_command"]) != 2
            or version["actual_command"][1:] != expected_command[1:]
            or not re.fullmatch(r"/proc/[1-9][0-9]*/fd/[1-9][0-9]*", str(version["actual_command"][0]))
        ):
            raise A3EvaluationError("tool version provenance is invalid")
        if {key: version[key] for key in ("stdout_sha256", "stderr_sha256")} != request["expected_tool_versions"][name]:
            raise A3EvaluationError("tool version output differs from its admitted identity")
    verifiers = _exact(result["verifiers"], {"sshd", "systemd_analyze"}, "static verifiers")
    expected_commands = {
        "sshd": [request["tools"]["sshd"]["path"], "-T", "-C", "user=codex,host=tgw-prod,addr=127.0.0.1", "-f", rendered["sshd-config"]["path"]],
        "systemd_analyze": [request["tools"]["systemd_analyze"]["path"], "verify", "--man=no", "sshd.service"],
    }
    for name, command in expected_commands.items():
        item = _exact(
            verifiers[name],
            {
                "command",
                "actual_command",
                "version_command",
                "actual_version_command",
                "executable",
                "returncode",
                "stdout_sha256",
                "stderr_sha256",
                "version_stdout_sha256",
                "version_stderr_sha256",
            },
            f"{name} verifier",
        )
        if item["command"] != command or item["executable"] != request["tools"][name] or item["returncode"] != 0:
            raise A3EvaluationError("static verifier provenance or result is invalid")
        expected_version = [request["tools"][name]["path"], "-V" if name == "sshd" else "--version"]
        actual_version = item["actual_version_command"]
        if (
            item["version_command"] != expected_version
            or not isinstance(actual_version, list)
            or len(actual_version) != 2
            or actual_version[1:] != expected_version[1:]
            or not re.fullmatch(r"/proc/[1-9][0-9]*/fd/[1-9][0-9]*", str(actual_version[0]))
        ):
            raise A3EvaluationError("static verifier version provenance is invalid")
        actual = item["actual_command"]
        if not isinstance(actual, list) or len(actual) != len(command) or not re.fullmatch(r"/proc/[1-9][0-9]*/fd/[1-9][0-9]*", str(actual[0])):
            raise A3EvaluationError("static verifier actual held-fd command is invalid")
        if name == "sshd" and (actual[1:-1] != command[1:-1] or not re.fullmatch(r"/proc/[1-9][0-9]*/fd/[1-9][0-9]*", str(actual[-1]))):
            raise A3EvaluationError("sshd verifier did not consume its held materialization")
        if name == "systemd_analyze":
            unit_path = Path(str(actual[-1]))
            if actual[1:3] != ["verify", "--man=no"] or not unit_path.is_absolute() or ".." in unit_path.parts or unit_path.name != "sshd.service":
                raise A3EvaluationError("systemd verifier did not consume its canonical held materialization")
        for hash_field in ("stdout_sha256", "stderr_sha256", "version_stdout_sha256", "version_stderr_sha256"):
            _sha(item[hash_field], f"{name} {hash_field}")
        if {
            "stdout_sha256": item["stdout_sha256"],
            "stderr_sha256": item["stderr_sha256"],
        } != request["expected_verifiers"][name]:
            raise A3EvaluationError("static verifier output differs from the admitted result")
    isolation = _exact(
        result["isolation"],
        {"schema", "kind", "composition_sha256", "command_count", "launch_evidence_sha256", "launcher_attested", "network_observed"},
        "isolation evidence",
    )
    if (
        isolation["schema"] != "tgw-nixos-a3-local-isolation-summary/v1"
        or isolation["kind"] != "root-launcher-fresh-netns-per-command"
        or not _SHA256.fullmatch(str(isolation["composition_sha256"]))
        or isinstance(isolation["command_count"], bool)
        or not isinstance(isolation["command_count"], int)
        or isolation["command_count"] < 1
        or not _SHA256.fullmatch(str(isolation["launch_evidence_sha256"]))
        or isolation["launcher_attested"] is not True
        or isolation["network_observed"] is not False
    ):
        raise A3EvaluationError("network isolation evidence is invalid")
    attestations = result["launcher_evidence"]
    if (
        not isinstance(attestations, list)
        or len(attestations) != isolation["command_count"]
        or digest(attestations) != isolation["launch_evidence_sha256"]
    ):
        raise A3EvaluationError("launcher evidence set is incomplete or tampered")
    observed_challenges: set[tuple[str, str]] = set()
    for envelope_value in attestations:
        envelope = _exact(
            envelope_value,
            {"schema", "signed_attestation", "replay_claim", "replay_claim_ref"},
            "launcher evidence envelope",
        )
        attestation = _exact(
            envelope["signed_attestation"],
            {
                "schema",
                "packet_sha256",
                "composition_sha256",
                "request_sha256",
                "launch_nonce",
                "attempt_id",
                "issued_at",
                "started_at",
                "ended_at",
                "expires_at",
                "netns",
                "child",
                "probes",
                "signature",
            },
            "signed launcher attestation",
        )
        claim = _exact(
            envelope["replay_claim"],
            {
                "schema",
                "launch_nonce",
                "attempt_id",
                "request_sha256",
                "composition_sha256",
                "attestation_sha256",
                "claim_sha256",
            },
            "launcher replay claim",
        )
        claim_ref = _exact(envelope["replay_claim_ref"], {"schema", "name", "sha256", "size"}, "launcher replay claim ref")
        netns = _exact(attestation["netns"], {"start_inode", "end_inode", "lo_only", "routes_empty", "link_sha256", "route_sha256"}, "signed netns")
        child = _exact(attestation["child"], {"pid", "starttime", "exe", "uid", "gid", "capabilities", "no_new_privs"}, "signed child")
        probes = _exact(attestation["probes"], {"pre", "post"}, "signed probes")
        for phase in ("pre", "post"):
            phase_value = _exact(probes[phase], {"direct", "dns", "private", "metadata"}, f"signed probes {phase}")
            for probe_name, probe_value in phase_value.items():
                probe = _exact(probe_value, {"attempted", "connected", "evidence_sha256"}, f"signed probe {phase}.{probe_name}")
                if probe["attempted"] is not True or probe["connected"] is not False or not _SHA256.fullmatch(str(probe["evidence_sha256"])):
                    raise A3EvaluationError("signed launcher probe is not exact negative evidence")
        challenge = (str(attestation["launch_nonce"]), str(attestation["attempt_id"]))
        timestamp_values = [attestation[name] for name in ("issued_at", "started_at", "ended_at", "expires_at")]
        if (
            envelope["schema"] != LAUNCH_EVIDENCE_SCHEMA
            or attestation["schema"] != ATTESTATION_SCHEMA
            or attestation["request_sha256"] != request["request_sha256"]
            or attestation["composition_sha256"] != isolation["composition_sha256"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(attestation["launch_nonce"]))
            or not re.fullmatch(r"attempt:[0-9a-f]{64}", str(attestation["attempt_id"]))
            or not _SHA256.fullmatch(str(attestation["packet_sha256"]))
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value) for value in timestamp_values)
            or timestamp_values != sorted(timestamp_values)
            or not isinstance(attestation["signature"], str)
            or not re.fullmatch(r"ed25519:[0-9a-f]{128}", attestation["signature"])
            or isinstance(netns["start_inode"], bool)
            or not isinstance(netns["start_inode"], int)
            or netns["start_inode"] <= 0
            or netns["end_inode"] != netns["start_inode"]
            or netns["lo_only"] is not True
            or netns["routes_empty"] is not True
            or not _SHA256.fullmatch(str(netns["link_sha256"]))
            or not _SHA256.fullmatch(str(netns["route_sha256"]))
            or any(isinstance(child[name], bool) or not isinstance(child[name], int) or child[name] <= 0 for name in ("pid", "starttime"))
            or not isinstance(child["uid"], int)
            or isinstance(child["uid"], bool)
            or not isinstance(child["gid"], int)
            or isinstance(child["gid"], bool)
            or not isinstance(child["exe"], str)
            or not child["exe"].startswith("/proc/")
            or child["capabilities"] != []
            or child["no_new_privs"] is not True
            or claim["schema"] != REPLAY_CLAIM_SCHEMA
            or claim["launch_nonce"] != attestation["launch_nonce"]
            or claim["attempt_id"] != attestation["attempt_id"]
            or claim["request_sha256"] != request["request_sha256"]
            or claim["composition_sha256"] != isolation["composition_sha256"]
            or claim["attestation_sha256"] != digest(attestation)
            or claim["claim_sha256"] != self_hash({key: item for key, item in claim.items() if key != "claim_sha256"})
            or claim_ref["schema"] != REPLAY_CLAIM_REF_SCHEMA
            or claim_ref["name"] != digest({"launch_nonce": attestation["launch_nonce"]}).removeprefix("sha256:") + ".json"
            or claim_ref["sha256"] != claim["claim_sha256"]
            or isinstance(claim_ref["size"], bool)
            or claim_ref["size"] != len(canonical(claim))
            or challenge in observed_challenges
        ):
            raise A3EvaluationError("signed launcher/replay evidence is structurally invalid or unbound")
        observed_challenges.add(challenge)
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


def terminal_receipt(
    *,
    request_sha256: str,
    provider_sha256: str,
    outcome: str,
    stage: str,
    step: str,
    code: str,
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
    cleanup: str,
    effects: Mapping[str, bool],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    if outcome not in {"FAILED", "AMBIGUOUS"} or cleanup not in {"NOT_CREATED", "REMOVED", "UNKNOWN"}:
        raise A3EvaluationError("terminal classification is invalid")
    value: dict[str, Any] = {
        "schema": TERMINAL_SCHEMA,
        "outcome": outcome,
        "request_sha256": request_sha256,
        "provider_sha256": provider_sha256,
        "stage": stage,
        "step": step,
        "code": code,
        "returncode": returncode,
        "stdout_sha256": digest(stdout),
        "stderr_sha256": digest(stderr),
        "cleanup": cleanup,
        "effects": dict(effects),
        "observation_sha256": digest(observation),
    }
    value["receipt_sha256"] = self_hash(value)
    value["evidence"] = ["nixos-a3-successor-evaluation-terminal:" + value["receipt_sha256"]]
    return validate_terminal(value, request_sha256=request_sha256, provider_sha256=provider_sha256)


def validate_terminal(value: Any, *, request_sha256: str, provider_sha256: str) -> dict[str, Any]:
    fields = {
        "schema",
        "outcome",
        "request_sha256",
        "provider_sha256",
        "stage",
        "step",
        "code",
        "returncode",
        "stdout_sha256",
        "stderr_sha256",
        "cleanup",
        "effects",
        "observation_sha256",
        "receipt_sha256",
        "evidence",
    }
    terminal = dict(_exact(value, fields, "A3 successor terminal receipt"))
    if (
        terminal["schema"] != TERMINAL_SCHEMA
        or terminal["outcome"] not in {"FAILED", "AMBIGUOUS"}
        or terminal["request_sha256"] != request_sha256
        or terminal["provider_sha256"] != provider_sha256
        or terminal["cleanup"] not in {"NOT_CREATED", "REMOVED", "UNKNOWN"}
        or not all(isinstance(terminal[key], str) and terminal[key] for key in ("stage", "step", "code"))
        or (
            terminal["returncode"] is not None
            and (
                isinstance(terminal["returncode"], bool)
                or not isinstance(terminal["returncode"], int)
                or not -255 <= terminal["returncode"] <= 255
            )
        )
    ):
        raise A3EvaluationError("terminal receipt classification or binding is invalid")
    for key in ("stdout_sha256", "stderr_sha256", "observation_sha256"):
        _sha(terminal[key], key)
    effects = _exact(terminal["effects"], {"build", *FORBIDDEN_EFFECTS}, "terminal effects")
    if any(effects[name] is not False for name in FORBIDDEN_EFFECTS) or effects["build"] not in {True, False}:
        raise A3EvaluationError("terminal receipt reports a forbidden effect")
    if any(len(terminal[key]) > 128 for key in ("stage", "step", "code")):
        raise A3EvaluationError("terminal diagnostics are unbounded")
    row = TERMINAL_STATE_TABLE.get((terminal["outcome"], terminal["stage"], terminal["step"], terminal["code"]))
    if (
        row is None
        or terminal["cleanup"] != row["cleanup"]
        or effects["build"] is not row["build"]
    ):
        raise A3EvaluationError("terminal tuple is outside the exact state table")
    rc_rule = row["rc"]
    if (
        (rc_rule == "none" and terminal["returncode"] is not None)
        or (rc_rule == "nonzero" and (not isinstance(terminal["returncode"], int) or isinstance(terminal["returncode"], bool) or terminal["returncode"] == 0))
    ):
        raise A3EvaluationError("terminal returncode relation is outside the exact state table")
    if terminal["receipt_sha256"] != self_hash({key: item for key, item in terminal.items() if key != "evidence"}):
        raise A3EvaluationError("terminal receipt self-hash mismatch")
    if terminal["evidence"] != ["nixos-a3-successor-evaluation-terminal:" + terminal["receipt_sha256"]]:
        raise A3EvaluationError("terminal receipt evidence is invalid")
    return terminal


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
        transport_sha256 = getattr(getattr(self.runner, "composition", None), "composition_sha256", None)
        return digest(
            {
                "schema": COMPOSITION_SCHEMA,
                "integration": self.integration,
                "allow_fixture": self.allow_fixture,
                "transport_kind": type(self.runner).__name__,
                "transport_sha256": transport_sha256,
                "receipt_store_identity": getattr(
                    self.receipt_store,
                    "identity",
                    {"schema": "tgw-nixos-a3-fixture-store/v1", "kind": type(self.receipt_store).__name__},
                ),
            }
        )


class _A3SuccessorEvaluationProviderCore:
    def __init__(self, composition: A3EvaluationComposition):
        self.composition = composition
        validate_integration_contract(composition.integration, allow_fixture=composition.allow_fixture)

    def ready(self, request_value: Mapping[str, Any]) -> None:
        """Fail before authority consumption unless the exact composition can run."""
        request = validate_request(request_value, allow_fixture=self.composition.allow_fixture)
        if request["integration"] != self.composition.integration:
            raise A3EvaluationHold("request integration differs from the mounted composition")
        if self.composition.status == "NOT_EXECUTABLE":
            raise A3EvaluationHold("reviewed tgw-flake integration archive/closure/public identities are not final")
        # Local imports avoid a module cycle while keeping the production type
        # check exact.  A truthy marker or look-alike callable is insufficient.
        from tgw.nixos_a3_successor_transport import A3LocalProductionTransport, A3TestTransport

        if self.composition.allow_fixture:
            if not isinstance(self.composition.runner, A3TestTransport):
                raise A3EvaluationHold("fixture composition does not mount the distinct test transport")
        else:
            if not isinstance(self.composition.runner, A3LocalProductionTransport):
                raise A3EvaluationHold("production composition does not mount the sealed local tgw-prod transport")
            self.composition.runner.validate_sealed(request)

    def __call__(self, effect: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(effect, Mapping) or set(effect) != {"kind", "generation", "parameters"} or effect["kind"] != EFFECT_KIND:
            raise A3EvaluationError("provider accepts only the distinct A3 successor effect envelope")
        self.ready(effect["parameters"])
        request = validate_request(effect["parameters"], allow_fixture=self.composition.allow_fixture)
        try:
            untrusted = self.composition.runner(request)
            try:
                result = validate_success(untrusted, request)
                if not self.composition.allow_fixture and result["isolation"]["composition_sha256"] != self.composition.runner.composition.composition_sha256:
                    raise A3EvaluationError("success isolation evidence differs from mounted local composition")
            except A3EvaluationError as exc:
                raise A3KnownFailure(
                    "success producer returned a deterministic invalid receipt",
                    stage="post-build",
                    step="success-validation",
                    cleanup="REMOVED",
                ) from exc
            reference = self.composition.receipt_store.persist(result)
        except A3EvaluationHold:
            raise
        except A3KnownFailure as exc:
            ambiguous = exc.cleanup == "UNKNOWN"
            built = exc.stage in {"nix-build", "post-build", "static-verification"}
            try:
                terminal = terminal_receipt(
                    request_sha256=request["request_sha256"],
                    provider_sha256=self.composition.receipt_sha256,
                    outcome="AMBIGUOUS" if ambiguous else "FAILED",
                    stage=exc.stage,
                    step=exc.step,
                    code="StepFailure" if type(exc).__name__ == "StepFailure" else "A3KnownFailure",
                    returncode=exc.returncode,
                    stdout=exc.stdout,
                    stderr=exc.stderr,
                    cleanup=exc.cleanup,
                    effects={"build": built, **{name: False for name in FORBIDDEN_EFFECTS}},
                    observation={"detail": str(exc)},
                )
            except A3EvaluationError as classification_exc:
                ambiguous = True
                terminal = terminal_receipt(
                    request_sha256=request["request_sha256"],
                    provider_sha256=self.composition.receipt_sha256,
                    outcome="AMBIGUOUS",
                    stage="postbuild-terminal-classification" if built else "prebuild-terminal-classification",
                    step="unknown",
                    code="UnknownFailureTuple",
                    returncode=None,
                    stdout=b"",
                    stderr=b"",
                    cleanup="UNKNOWN",
                    effects={"build": built, **{name: False for name in FORBIDDEN_EFFECTS}},
                    observation={"detail": str(exc), "classification_error": str(classification_exc)},
                )
            try:
                reference = self.composition.receipt_store.persist(terminal)
            except Exception as store_exc:
                observation = {
                    "schema": "tgw-nixos-a3-successor-evaluation-observation/v1",
                    "request_sha256": request["request_sha256"],
                    "generation": effect["generation"],
                    "type": type(store_exc).__name__,
                    "detail": str(store_exc),
                    "composition_sha256": self.composition.receipt_sha256,
                    "terminal_sha256": terminal["receipt_sha256"],
                }
                raise A3EvaluationAmbiguous("known failure receipt persistence is ambiguous", observation) from store_exc
            if reference != {"schema": STORE_REF_SCHEMA, "sha256": terminal["receipt_sha256"], "size": len(canonical(terminal))}:
                raise A3EvaluationAmbiguous("known failure store reference is invalid", {"terminal_sha256": terminal["receipt_sha256"], "reference_sha256": digest(reference)})
            if ambiguous:
                raise A3EvaluationAmbiguous(
                    str(exc),
                    {
                        "request_sha256": request["request_sha256"],
                        "terminal_sha256": terminal["receipt_sha256"],
                        "process_state": getattr(exc, "process_state", "UNKNOWN"),
                    },
                    persisted_evidence=("nixos-a3-successor-evaluation-terminal:" + terminal["receipt_sha256"],),
                ) from exc
            raise A3EvaluationFailure(str(exc), terminal) from exc
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
                provider_sha256=self.composition.receipt_sha256,
                stage="evaluation-or-success-persistence",
                step="unknown",
                code="UnknownExternalState",
                returncode=None,
                stdout=b"",
                stderr=b"",
                cleanup="UNKNOWN",
                effects={"build": True, **{name: False for name in FORBIDDEN_EFFECTS}},
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


class A3SuccessorEvaluationProvider(_A3SuccessorEvaluationProviderCore):
    """Production provider; only the sealed local factory may construct it."""

    def __init__(self, composition: A3EvaluationComposition, *, _token: object | None = None):
        if _token is not _PROVIDER_SEAL or composition.allow_fixture:
            raise TypeError("production A3 provider must be constructed by build_local_production_provider")
        super().__init__(composition)


class A3TestSuccessorEvaluationProvider(_A3SuccessorEvaluationProviderCore):
    """Distinct fixture provider that cannot satisfy production composition wiring."""

    def __init__(self, composition: A3EvaluationComposition):
        if not composition.allow_fixture:
            raise TypeError("test A3 provider requires allow_fixture=True")
        super().__init__(composition)


class ImmutableEvaluationStore:
    """Held-root, same-inode, read-after-fsync immutable receipt store."""

    def __init__(self, root: Path, *, trusted_uid: int | None = None):
        self.root = Path(root)
        uid = os.getuid() if trusted_uid is None else trusted_uid
        if not self.root.is_absolute() or self.root.parent == self.root or ".." in self.root.parts:
            raise A3EvaluationError("receipt root path is not absolute and normalized")
        # Walk from the filesystem anchor one named component at a time.  No
        # component is followed through a symlink.  Root-owned sticky ancestors
        # (for example /tmp in tests) are allowed; all other writable ancestors
        # are refused.
        parent_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in self.root.parent.parts[1:]:
                before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                held_component = os.fstat(next_fd)
                after = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                mode = stat.S_IMODE(held_component.st_mode)
                sticky_root = held_component.st_uid == 0 and bool(mode & stat.S_ISVTX)
                if (
                    not stat.S_ISDIR(held_component.st_mode)
                    or held_component.st_uid not in {0, uid}
                    or (mode & 0o022 and not sticky_root)
                    or (before.st_dev, before.st_ino) != (held_component.st_dev, held_component.st_ino)
                    or (after.st_dev, after.st_ino) != (held_component.st_dev, held_component.st_ino)
                ):
                    os.close(next_fd)
                    raise A3EvaluationError("receipt store component walk found an unsafe ancestor")
                os.close(parent_fd)
                parent_fd = next_fd
            self._parent_fd = parent_fd
            parent_fd = -1
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
        parent_held = os.fstat(self._parent_fd)
        self._parent_identity = (parent_held.st_dev, parent_held.st_ino, parent_held.st_uid, stat.S_IMODE(parent_held.st_mode))
        named_before = os.stat(self.root.name, dir_fd=self._parent_fd, follow_symlinks=False)
        self._root_fd = os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd)
        held = os.fstat(self._root_fd)
        named_after = os.stat(self.root.name, dir_fd=self._parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(held.st_mode)
            or held.st_uid != uid
            or stat.S_IMODE(held.st_mode) != 0o700
            or (named_before.st_dev, named_before.st_ino) != (held.st_dev, held.st_ino)
            or (named_after.st_dev, named_after.st_ino) != (held.st_dev, held.st_ino)
        ):
            self.close()
            raise A3EvaluationError("receipt root must be one held trusted mode-0700 directory")
        self._root_identity = (held.st_dev, held.st_ino, held.st_uid, stat.S_IMODE(held.st_mode))
        self.identity = {
            "path": str(self.root),
            "dev": held.st_dev,
            "ino": held.st_ino,
            "uid": held.st_uid,
            "gid": held.st_gid,
            "mode": stat.S_IMODE(held.st_mode),
        }

    def close(self) -> None:
        for name in ("_root_fd", "_parent_fd"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, name, -1)

    def __del__(self) -> None:
        self.close()

    def _verify_root(self) -> None:
        parent = os.fstat(self._parent_fd)
        held = os.fstat(self._root_fd)
        named = os.stat(self.root.name, dir_fd=self._parent_fd, follow_symlinks=False)
        observed = (held.st_dev, held.st_ino, held.st_uid, stat.S_IMODE(held.st_mode))
        if (
            (parent.st_dev, parent.st_ino, parent.st_uid, stat.S_IMODE(parent.st_mode)) != self._parent_identity
            or observed != self._root_identity
            or (named.st_dev, named.st_ino) != observed[:2]
        ):
            raise A3EvaluationError("immutable receipt root identity changed")

    @staticmethod
    def _read_held(fd: int, maximum: int) -> bytes:
        os.lseek(fd, 0, os.SEEK_SET)
        value = bytearray()
        while len(value) <= maximum:
            block = os.read(fd, min(1024 * 1024, maximum + 1 - len(value)))
            if not block:
                break
            value.extend(block)
        if len(value) > maximum:
            raise A3EvaluationError("immutable receipt exceeds its exact size")
        return bytes(value)

    def _verify_named(self, name: str, raw: bytes, expected_inode: tuple[int, int] | None = None) -> tuple[int, int]:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._root_fd)
        try:
            metadata = os.fstat(fd)
            identity = (metadata.st_dev, metadata.st_ino)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self._root_identity[2]
                or stat.S_IMODE(metadata.st_mode) != 0o400
                or metadata.st_size != len(raw)
                or metadata.st_nlink != 1
                or (expected_inode is not None and identity != expected_inode)
                or self._read_held(fd, len(raw)) != raw
            ):
                raise A3EvaluationError("immutable receipt named readback mismatch")
            return identity
        finally:
            os.close(fd)

    def persist(self, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = canonical(receipt)
        sha = receipt.get("receipt_sha256")
        if sha != self_hash({key: item for key, item in receipt.items() if key != "evidence"}):
            raise A3EvaluationError("refusing to persist a non-self-hashed receipt")
        name = str(sha).removeprefix("sha256:") + ".json"
        self._verify_root()
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self._root_fd)
        except FileExistsError:
            self._verify_named(name, raw)
            return {"schema": STORE_REF_SCHEMA, "sha256": sha, "size": len(raw)}
        created = True
        try:
            try:
                offset = 0
                while offset < len(raw):
                    count = os.write(fd, raw[offset:])
                    if count <= 0:
                        raise OSError("short immutable receipt write")
                    offset += count
                os.fsync(fd)
                held = os.fstat(fd)
                if self._read_held(fd, len(raw)) != raw:
                    raise A3EvaluationError("immutable receipt held readback mismatch")
            finally:
                os.close(fd)
            inode = self._verify_named(name, raw, (held.st_dev, held.st_ino))
            os.fsync(self._root_fd)
            self._verify_root()
            self._verify_named(name, raw, inode)
            created = False
        finally:
            if created:
                try:
                    os.unlink(name, dir_fd=self._root_fd)
                    os.fsync(self._root_fd)
                    try:
                        os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise A3EvaluationError("failed receipt cleanup left a poisoned path")
                except OSError as cleanup_exc:
                    raise A3EvaluationError("failed receipt cleanup is persistence ambiguity") from cleanup_exc
        return {"schema": STORE_REF_SCHEMA, "sha256": sha, "size": len(raw)}


def build_local_production_provider(
    request_value: Mapping[str, Any],
    *,
    composition_path: Path = Path("/etc/tgw/a3-successor-local-composition.json"),
) -> A3SuccessorEvaluationProvider:
    """Build the only production provider from the fixed local manifest."""
    from tgw.nixos_a3_successor_transport import load_local_production_transport

    request = validate_request(request_value)
    transport = load_local_production_transport(composition_path)
    transport.validate_sealed(request)
    receipt_root = Path(transport.composition.receipt_roots["terminal"]["path"])
    store = ImmutableEvaluationStore(receipt_root, trusted_uid=0)
    if store.identity != transport.composition.receipt_roots["terminal"]:
        raise A3EvaluationError("provider receipt store differs from sealed local composition")
    return A3SuccessorEvaluationProvider(A3EvaluationComposition(request["integration"], store, transport), _token=_PROVIDER_SEAL)


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: io.TextIOBase | None = None,
    output_stream: io.TextIOBase | None = None,
    provider: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    receipt_store: ReceiptStore | None = None,
) -> int:
    """Read exactly one effect envelope from stdin; no generic CLI arguments."""
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    source = sys.stdin if input_stream is None else input_stream
    sink = sys.stdout if output_stream is None else output_stream
    if arguments:
        raise SystemExit("nixos-a3-successor-evaluation accepts no arguments")
    request_sha256 = "sha256:" + "0" * 64
    try:
        value = json.load(source)
        if isinstance(value, Mapping) and isinstance(value.get("parameters"), Mapping):
            candidate = value["parameters"].get("request_sha256")
            if isinstance(candidate, str) and _SHA256.fullmatch(candidate):
                request_sha256 = candidate
        if provider is None:
            if not isinstance(value, Mapping) or not isinstance(value.get("parameters"), Mapping):
                raise A3KnownFailure(
                    "production A3 successor evaluation request is absent",
                    stage="composition-readiness",
                    step="request",
                    cleanup="NOT_CREATED",
                )
            provider = build_local_production_provider(value["parameters"])
        result = provider(value)
    except A3EvaluationFailure as exc:
        sink.write(canonical(exc.terminal).decode() + "\n")
        return 1
    except Exception as exc:
        fallback = terminal_receipt(
            request_sha256=request_sha256,
            provider_sha256=digest({"provider": "unmounted" if provider is None else type(provider).__name__}),
            outcome="FAILED",
            stage="stdin-or-provider",
            step="stdin-parse-or-dispatch",
            code="InputOrProviderFailure",
            returncode=None,
            stdout=b"",
            stderr=b"",
            cleanup="NOT_CREATED",
            effects={"build": False, **{name: False for name in FORBIDDEN_EFFECTS}},
            observation={"detail": str(exc)},
        )
        if receipt_store is None:
            fallback["outcome"] = "AMBIGUOUS"
            fallback["code"] = "ReceiptStoreUnavailable"
            fallback["cleanup"] = "UNKNOWN"
            fallback["receipt_sha256"] = self_hash({key: item for key, item in fallback.items() if key != "evidence"})
            fallback["evidence"] = ["nixos-a3-successor-evaluation-terminal:" + fallback["receipt_sha256"]]
            validate_terminal(fallback, request_sha256=request_sha256, provider_sha256=fallback["provider_sha256"])
        else:
            try:
                receipt_store.persist(fallback)
            except Exception:
                fallback["outcome"] = "AMBIGUOUS"
                fallback["code"] = "ReceiptStorePersistenceFailure"
                fallback["cleanup"] = "UNKNOWN"
                fallback["receipt_sha256"] = self_hash({key: item for key, item in fallback.items() if key != "evidence"})
                fallback["evidence"] = ["nixos-a3-successor-evaluation-terminal:" + fallback["receipt_sha256"]]
                validate_terminal(fallback, request_sha256=request_sha256, provider_sha256=fallback["provider_sha256"])
        sink.write(canonical(fallback).decode() + "\n")
        return 1
    sink.write(canonical(result).decode() + "\n")
    return 0

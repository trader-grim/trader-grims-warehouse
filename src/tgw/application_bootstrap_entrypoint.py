"""Fixed, no-argument W09 controller composition and execution entrypoint."""

from __future__ import annotations

import json
import os
import stat
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from tgw.application_deployment_contract import (
    PLAN_COMMIT,
    PinnedApplicationDeploymentContractResolver,
    ProductionApplicationBinding,
    ProtectedGitObjectReader,
)
from tgw.application_release_provider import build_production_application_release_provider
from tgw.bootstrap_authority import BootstrapSessionAuthority
from tgw.candidate_receipt_sink import (
    PinnedCandidateEvidenceDescriptor,
    PinnedGitReceiptSink,
    protected_git_object_reads,
)
from tgw.deployment_runtime import compose_application_bootstrap_controller
from tgw.effect_completion_store import ImmutableEffectCompletionStore

CONFIG_PATH = Path("/etc/tgw/w09/application-bootstrap-controller.json")
SCHEMA = "tgw-w09-application-bootstrap-controller/v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _held_config(path: Path) -> tuple[dict[str, Any], int, bytes, tuple[int, ...]]:
    for ancestor in (path.parent, *path.parents):
        metadata = ancestor.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ValueError("W09 controller config ancestor is not root-protected")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        raw = os.pread(fd, 4 * 1024 * 1024 + 1, 0)
        named = os.stat(path, follow_symlinks=False)
        if (
            len(raw) > 4 * 1024 * 1024 or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0 or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError("W09 controller config is not one protected artifact")
        value = json.loads(raw)
    except Exception:
        os.close(fd)
        raise
    if not isinstance(value, dict):
        os.close(fd)
        raise ValueError("W09 controller config is not an object")
    unsigned = dict(value); claimed = unsigned.pop("config_sha256", None)
    fields = {
        "schema", "candidate_repository", "plan_repository", "plan_approved_ref",
        "git_path", "git_sha256", "protected_repositories", "candidate_evidence_pin",
        "sinks", "production", "grant_path", "consumption_receipt_path",
        "terminal_store", "provider_descriptor_path", "trusted_uid", "config_sha256",
    }
    if (
        set(value) != fields or value.get("schema") != SCHEMA
        or claimed != "sha256:" + sha256(_canonical(unsigned)).hexdigest()
    ):
        os.close(fd)
        raise ValueError("W09 controller config schema/hash is invalid")
    identity = (
        metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_gid,
        metadata.st_mode, metadata.st_size,
    )
    return value, fd, raw, identity


def _revalidate_config(
    path: Path, fd: int, raw: bytes, identity: tuple[int, ...],
) -> None:
    held = os.fstat(fd)
    named = os.stat(path, follow_symlinks=False)
    held_identity = (
        held.st_dev, held.st_ino, held.st_uid, held.st_gid, held.st_mode, held.st_size,
    )
    named_identity = (
        named.st_dev, named.st_ino, named.st_uid, named.st_gid, named.st_mode, named.st_size,
    )
    if (
        held_identity != identity or named_identity != identity
        or os.pread(fd, len(raw) + 1, 0) != raw
    ):
        raise OSError("W09 controller config changed during execution")


def _forbidden(_parameters: Mapping[str, str]) -> Mapping[str, Any]:
    raise ValueError("unrelated steady-state effect is not mounted in W09")


def execute_from_fixed_config(path: Path = CONFIG_PATH) -> Mapping[str, Any]:
    """Compose exact mounted authorities, execute the grant, and persist terminal output."""

    config, config_fd, config_raw, config_identity = _held_config(path)
    readers: dict[Path, ProtectedGitObjectReader] = {}
    authority = provider = terminal = None
    try:
        repository_paths = config["protected_repositories"]
        if (
            not isinstance(repository_paths, list) or not repository_paths
            or repository_paths != sorted(set(repository_paths))
            or any(not isinstance(item, str) or not item.startswith("/") for item in repository_paths)
        ):
            raise ValueError("W09 protected repository set is invalid")
        for named in repository_paths:
            root = Path(named).resolve(strict=True)
            readers[root] = ProtectedGitObjectReader(
                root, git_path=Path(config["git_path"]), git_sha256=config["git_sha256"],
            )
        candidate_repository = Path(config["candidate_repository"]).resolve(strict=True)
        plan_repository = Path(config["plan_repository"]).resolve(strict=True)
        sink_fields = {
            "execution_evidence", "contract", "runtime_config", "archive",
            "instruction", "predecessor_observation",
        }
        if not isinstance(config["sinks"], Mapping) or set(config["sinks"]) != sink_fields:
            raise ValueError("W09 controller sink set is invalid")
        with protected_git_object_reads(readers):
            descriptor = PinnedCandidateEvidenceDescriptor(
                config["candidate_evidence_pin"], candidate_repository=candidate_repository,
            )
            sinks = {
                name: PinnedGitReceiptSink(binding, candidate_repository=candidate_repository)
                for name, binding in config["sinks"].items()
            }
        production_raw = config["production"]
        if not isinstance(production_raw, Mapping) or set(production_raw) != {
            "target_host", "root_id", "release_root", "services", "health_probes",
            "operation_sink_id", "operation_sink_descriptor_hash",
        }:
            raise ValueError("W09 production binding is invalid")
        production = ProductionApplicationBinding(
            target_host=production_raw["target_host"], root_id=production_raw["root_id"],
            release_root=Path(production_raw["release_root"]),
            services=tuple(production_raw["services"]),
            health_probes=tuple(production_raw["health_probes"]),
            operation_sink_id=production_raw["operation_sink_id"],
            operation_sink_descriptor_hash=production_raw["operation_sink_descriptor_hash"],
        )
        resolver = PinnedApplicationDeploymentContractResolver.production(
            repository=candidate_repository, plan_repository=plan_repository,
            plan_approved_ref=config["plan_approved_ref"],
            candidate_evidence_descriptor=descriptor,
            execution_evidence_sink=sinks["execution_evidence"],
            contract_sink=sinks["contract"], runtime_config_sink=sinks["runtime_config"],
            archive_sink=sinks["archive"], instruction_sink=sinks["instruction"],
            predecessor_observation_sink=sinks["predecessor_observation"],
            candidate_objects=readers[candidate_repository], plan_objects=readers[plan_repository],
            protected_readers=readers, production=production,
        )
        trusted_uid = config["trusted_uid"]
        if not isinstance(trusted_uid, int) or trusted_uid < 0:
            raise ValueError("W09 trusted uid is invalid")
        authority = BootstrapSessionAuthority.production_application(
            Path(config["grant_path"]), receipt_path=Path(config["consumption_receipt_path"]),
            current_plan_commit=PLAN_COMMIT, trusted_uid=trusted_uid,
        )
        terminal_raw = config["terminal_store"]
        if not isinstance(terminal_raw, Mapping) or set(terminal_raw) != {"root", "sink_id", "descriptor_hash"}:
            raise ValueError("W09 terminal store binding is invalid")
        terminal = ImmutableEffectCompletionStore(
            Path(terminal_raw["root"]), sink_id=terminal_raw["sink_id"],
            descriptor_hash=terminal_raw["descriptor_hash"], trusted_uid=trusted_uid,
        )
        provider = build_production_application_release_provider(Path(config["provider_descriptor_path"]))
        controller = compose_application_bootstrap_controller(
            expected_host="tgw-prod", authority=authority, application_resolver=resolver,
            terminal_store=terminal, provider=provider, flake_push=_forbidden,
            flake_switch_record=_forbidden, dependency_resubmit=_forbidden,
            controller_evidence="w09-controller-config:" + "sha256:" + sha256(
                _canonical({
                    "content_sha256": "sha256:" + sha256(config_raw).hexdigest(),
                    "identity": list(config_identity),
                })
            ).hexdigest(),
            terminal_precheck=lambda: _revalidate_config(
                path, config_fd, config_raw, config_identity,
            ),
        )
        result = controller.execute(request_id=authority.grant.grant_id, effect=authority.grant.effect)
        _revalidate_config(path, config_fd, config_raw, config_identity)
        return result.sealed_mapping()
    finally:
        cleanup_errors: list[Exception] = []
        for resource in (provider, terminal, authority):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:  # cleanup cannot replace a durable terminal outcome
                    cleanup_errors.append(exc)
        for reader in reversed(tuple(readers.values())):
            try:
                reader.close()
            except Exception as exc:  # cleanup cannot replace a durable terminal outcome
                cleanup_errors.append(exc)
        try:
            os.close(config_fd)
        except OSError as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            sys.stderr.write(
                "w09-controller-cleanup:" + sha256(
                    _canonical([type(item).__name__ for item in cleanup_errors])
                ).hexdigest() + "\n"
            )


def main() -> int:
    if len(sys.argv) != 1:
        raise SystemExit("tgw-w09-application-bootstrap accepts no arguments")
    result = execute_from_fixed_config()
    sys.stdout.buffer.write(_canonical(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

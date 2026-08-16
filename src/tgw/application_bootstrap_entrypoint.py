"""Fixed, no-argument W09 controller composition and execution entrypoint."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

CONFIG_PATH = Path("/etc/tgw/w09/application-bootstrap-controller.json")
SCHEMA = "tgw-w09-application-bootstrap-controller/v2"
RUNTIME_SCHEMA = "tgw-w09-controller-runtime-manifest/v1"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _held_config(path: Path) -> tuple[dict[str, Any], int, bytes, tuple[int, ...]]:
    for ancestor in (path.parent, *path.parents):
        metadata = ancestor.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise ValueError("W09 controller config ancestor is not root-protected")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        raw = os.pread(fd, 4 * 1024 * 1024 + 1, 0)
        named = os.stat(path, follow_symlinks=False)
        if (
            len(raw) > 4 * 1024 * 1024
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
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
    unsigned = dict(value)
    claimed = unsigned.pop("config_sha256", None)
    fields = {
        "schema",
        "candidate_repository",
        "plan_repository",
        "plan_approved_ref",
        "git_path",
        "git_sha256",
        "protected_repositories",
        "candidate_evidence_pin",
        "sinks",
        "production",
        "grant_path",
        "consumption_receipt_path",
        "terminal_store",
        "provider_descriptor_path",
        "trusted_uid",
        "controller_runtime",
        "config_sha256",
    }
    if set(value) != fields or value.get("schema") != SCHEMA or claimed != "sha256:" + sha256(_canonical(unsigned)).hexdigest():
        os.close(fd)
        raise ValueError("W09 controller config schema/hash is invalid")
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_mode,
        metadata.st_size,
    )
    return value, fd, raw, identity


def _protected_ancestors(path: Path) -> None:
    for ancestor in (path.parent, *path.parents):
        metadata = ancestor.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise ValueError(f"controller runtime ancestor is not root-protected: {ancestor}")


def _hold_runtime_artifact(
    binding: Mapping[str, Any],
    *,
    label: str,
    max_bytes: int = 64 * 1024 * 1024,
) -> tuple[Path, int, bytes, tuple[int, ...]]:
    if not isinstance(binding, Mapping) or set(binding) != {
        "path",
        "sha256",
        "uid",
        "gid",
        "mode",
        "size",
    }:
        raise ValueError(f"{label} binding is invalid")
    path = Path(str(binding["path"]))
    if not path.is_absolute() or _SHA256.fullmatch(str(binding["sha256"])) is None:
        raise ValueError(f"{label} path or digest is invalid")
    _protected_ancestors(path)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        held = os.fstat(fd)
        raw = os.pread(fd, max_bytes + 1, 0)
        named = os.stat(path, follow_symlinks=False)
        identity = (
            held.st_dev,
            held.st_ino,
            held.st_uid,
            held.st_gid,
            held.st_mode,
            held.st_size,
        )
        if (
            len(raw) > max_bytes
            or not stat.S_ISREG(held.st_mode)
            or held.st_uid != binding["uid"]
            or held.st_gid != binding["gid"]
            or stat.S_IMODE(held.st_mode) != binding["mode"]
            or held.st_size != binding["size"]
            or "sha256:" + sha256(raw).hexdigest() != binding["sha256"]
            or identity
            != (
                named.st_dev,
                named.st_ino,
                named.st_uid,
                named.st_gid,
                named.st_mode,
                named.st_size,
            )
        ):
            raise ValueError(f"{label} differs from its protected binding")
        return path, fd, raw, identity
    except Exception:
        os.close(fd)
        raise


def _revalidate_runtime_artifact(
    artifact: tuple[Path, int, bytes, tuple[int, ...]],
) -> None:
    path, fd, raw, identity = artifact
    held = os.fstat(fd)
    named = os.stat(path, follow_symlinks=False)
    held_identity = (
        held.st_dev,
        held.st_ino,
        held.st_uid,
        held.st_gid,
        held.st_mode,
        held.st_size,
    )
    named_identity = (
        named.st_dev,
        named.st_ino,
        named.st_uid,
        named.st_gid,
        named.st_mode,
        named.st_size,
    )
    if held_identity != identity or named_identity != identity or os.pread(fd, len(raw) + 1, 0) != raw:
        raise OSError(f"controller runtime artifact changed: {path}")


def _hold_controller_runtime(
    value: Any,
    *,
    require_launcher: bool,
) -> tuple[dict[str, Any], list[tuple[Path, int, bytes, tuple[int, ...]]], str]:
    if not isinstance(value, Mapping) or set(value) != {
        "launcher",
        "python",
        "entrypoint",
        "manifest",
    }:
        raise ValueError("controller runtime binding is invalid")
    artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]] = []
    try:
        named = {}
        for name in ("launcher", "python", "entrypoint", "manifest"):
            artifact = _hold_runtime_artifact(
                value[name],
                label=f"controller {name}",
            )
            artifacts.append(artifact)
            named[name] = artifact
        if require_launcher and Path(sys.argv[0]).resolve(strict=True) != named["launcher"][0].resolve(strict=True):
            raise ValueError("running controller launcher differs from its binding")
        if Path(__file__).resolve(strict=True) != named["entrypoint"][0].resolve(strict=True):
            raise ValueError("imported controller entrypoint differs from its binding")
        manifest = json.loads(named["manifest"][2])
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema",
            "files",
            "manifest_sha256",
        }:
            raise ValueError("controller runtime manifest is invalid")
        unsigned = dict(manifest)
        claimed = unsigned.pop("manifest_sha256")
        if manifest["schema"] != RUNTIME_SCHEMA or claimed != "sha256:" + sha256(_canonical(unsigned)).hexdigest() or not isinstance(manifest["files"], list) or not manifest["files"]:
            raise ValueError("controller runtime manifest hash/schema is invalid")
        paths = [item.get("path") if isinstance(item, Mapping) else None for item in manifest["files"]]
        if any(not isinstance(path, str) for path in paths):
            raise ValueError("controller runtime file set is invalid")
        if paths != sorted(set(paths)):
            raise ValueError("controller runtime file set is invalid")
        for index, binding in enumerate(manifest["files"]):
            artifact = _hold_runtime_artifact(
                binding,
                label=f"controller module {index}",
            )
            artifacts.append(artifact)
        manifest_paths = {str(artifact[0].resolve(strict=True)) for artifact in artifacts[4:]}
        if str(named["entrypoint"][0].resolve(strict=True)) not in manifest_paths:
            raise ValueError("controller entrypoint is absent from the runtime manifest")
        runtime_evidence = (
            "w09-controller-runtime:"
            + "sha256:"
            + sha256(
                _canonical(
                    {
                        "manifest_sha256": claimed,
                        "identities": [list(artifact[3]) for artifact in artifacts],
                    }
                )
            ).hexdigest()
        )
        return manifest, artifacts, runtime_evidence
    except Exception:
        for _path, fd, _raw, _identity in reversed(artifacts):
            os.close(fd)
        raise


def _revalidate_controller_runtime(
    manifest: Mapping[str, Any],
    artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]],
) -> None:
    for artifact in artifacts:
        _revalidate_runtime_artifact(artifact)
    allowed = {str(Path(item["path"]).resolve(strict=True)) for item in manifest["files"]}
    loaded = {str(Path(location).resolve(strict=True)) for module in tuple(sys.modules.values()) if isinstance((location := getattr(module, "__file__", None)), str) and not location.startswith("<")}
    unexpected = loaded - allowed
    if unexpected:
        raise OSError("loaded controller modules are absent from the protected runtime manifest: " + "sha256:" + sha256(_canonical(sorted(unexpected))).hexdigest())


def _revalidate_config(
    path: Path,
    fd: int,
    raw: bytes,
    identity: tuple[int, ...],
) -> None:
    held = os.fstat(fd)
    named = os.stat(path, follow_symlinks=False)
    held_identity = (
        held.st_dev,
        held.st_ino,
        held.st_uid,
        held.st_gid,
        held.st_mode,
        held.st_size,
    )
    named_identity = (
        named.st_dev,
        named.st_ino,
        named.st_uid,
        named.st_gid,
        named.st_mode,
        named.st_size,
    )
    if held_identity != identity or named_identity != identity or os.pread(fd, len(raw) + 1, 0) != raw:
        raise OSError("W09 controller config changed during execution")


def _forbidden(_parameters: Mapping[str, str]) -> Mapping[str, Any]:
    raise ValueError("unrelated steady-state effect is not mounted in W09")


def execute_from_fixed_config(path: Path = CONFIG_PATH) -> Mapping[str, Any]:
    """Compose exact mounted authorities, execute the grant, and persist terminal output."""

    config, config_fd, config_raw, config_identity = _held_config(path)
    runtime_manifest: dict[str, Any] | None = None
    runtime_artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]] = []
    runtime_evidence = ""
    readers: dict[Path, Any] = {}
    authority = provider = terminal = None
    try:
        runtime_manifest, runtime_artifacts, runtime_evidence = _hold_controller_runtime(
            config["controller_runtime"],
            require_launcher=False,
        )
        python_path = runtime_artifacts[1][0].resolve(strict=True)
        if not sys.flags.isolated or "PYTHONPATH" in os.environ or Path("/proc/self/exe").resolve(strict=True) != python_path:
            raise ValueError("W09 controller must run through its exact isolated interpreter")
        _revalidate_controller_runtime(runtime_manifest, runtime_artifacts)

        # No TGW module is imported until the exact protected controller runtime
        # has been held and independently verified above.
        from tgw.application_deployment_contract import (
            PLAN_COMMIT,
            PinnedApplicationDeploymentContractResolver,
            ProductionApplicationBinding,
            ProtectedGitObjectReader,
        )
        from tgw.application_release_provider import (
            build_production_application_release_provider,
        )
        from tgw.bootstrap_authority import BootstrapSessionAuthority
        from tgw.candidate_receipt_sink import (
            PinnedCandidateEvidenceDescriptor,
            PinnedGitReceiptSink,
            protected_git_object_reads,
        )
        from tgw.deployment_runtime import compose_application_bootstrap_controller
        from tgw.effect_completion_store import ImmutableEffectCompletionStore

        _revalidate_controller_runtime(runtime_manifest, runtime_artifacts)
        repository_paths = config["protected_repositories"]
        if (
            not isinstance(repository_paths, list)
            or not repository_paths
            or repository_paths != sorted(set(repository_paths))
            or any(not isinstance(item, str) or not item.startswith("/") for item in repository_paths)
        ):
            raise ValueError("W09 protected repository set is invalid")
        for named in repository_paths:
            root = Path(named).resolve(strict=True)
            readers[root] = ProtectedGitObjectReader(
                root,
                git_path=Path(config["git_path"]),
                git_sha256=config["git_sha256"],
            )
        candidate_repository = Path(config["candidate_repository"]).resolve(strict=True)
        plan_repository = Path(config["plan_repository"]).resolve(strict=True)
        sink_fields = {
            "execution_evidence",
            "contract",
            "runtime_config",
            "archive",
            "instruction",
            "predecessor_observation",
        }
        if not isinstance(config["sinks"], Mapping) or set(config["sinks"]) != sink_fields:
            raise ValueError("W09 controller sink set is invalid")
        with protected_git_object_reads(readers):
            descriptor = PinnedCandidateEvidenceDescriptor(
                config["candidate_evidence_pin"],
                candidate_repository=candidate_repository,
            )
            sinks = {name: PinnedGitReceiptSink(binding, candidate_repository=candidate_repository) for name, binding in config["sinks"].items()}
        production_raw = config["production"]
        if not isinstance(production_raw, Mapping) or set(production_raw) != {
            "target_host",
            "root_id",
            "release_root",
            "services",
            "health_probes",
            "operation_sink_id",
            "operation_sink_descriptor_hash",
        }:
            raise ValueError("W09 production binding is invalid")
        production = ProductionApplicationBinding(
            target_host=production_raw["target_host"],
            root_id=production_raw["root_id"],
            release_root=Path(production_raw["release_root"]),
            services=tuple(production_raw["services"]),
            health_probes=tuple(production_raw["health_probes"]),
            operation_sink_id=production_raw["operation_sink_id"],
            operation_sink_descriptor_hash=production_raw["operation_sink_descriptor_hash"],
        )
        resolver = PinnedApplicationDeploymentContractResolver.production(
            repository=candidate_repository,
            plan_repository=plan_repository,
            plan_approved_ref=config["plan_approved_ref"],
            candidate_evidence_descriptor=descriptor,
            execution_evidence_sink=sinks["execution_evidence"],
            contract_sink=sinks["contract"],
            runtime_config_sink=sinks["runtime_config"],
            archive_sink=sinks["archive"],
            instruction_sink=sinks["instruction"],
            predecessor_observation_sink=sinks["predecessor_observation"],
            candidate_objects=readers[candidate_repository],
            plan_objects=readers[plan_repository],
            protected_readers=readers,
            production=production,
        )
        trusted_uid = config["trusted_uid"]
        if not isinstance(trusted_uid, int) or trusted_uid < 0:
            raise ValueError("W09 trusted uid is invalid")
        authority = BootstrapSessionAuthority.production_application(
            Path(config["grant_path"]),
            receipt_path=Path(config["consumption_receipt_path"]),
            current_plan_commit=PLAN_COMMIT,
            trusted_uid=trusted_uid,
        )
        terminal_raw = config["terminal_store"]
        if not isinstance(terminal_raw, Mapping) or set(terminal_raw) != {"root", "sink_id", "descriptor_hash"}:
            raise ValueError("W09 terminal store binding is invalid")
        terminal = ImmutableEffectCompletionStore(
            Path(terminal_raw["root"]),
            sink_id=terminal_raw["sink_id"],
            descriptor_hash=terminal_raw["descriptor_hash"],
            trusted_uid=trusted_uid,
        )
        provider = build_production_application_release_provider(Path(config["provider_descriptor_path"]))
        controller_evidence = (
            "w09-controller-closure:"
            + "sha256:"
            + sha256(
                _canonical(
                    {
                        "config": {
                            "content_sha256": "sha256:" + sha256(config_raw).hexdigest(),
                            "identity": list(config_identity),
                        },
                        "runtime": runtime_evidence,
                    }
                )
            ).hexdigest()
        )

        def terminal_precheck() -> None:
            _revalidate_config(path, config_fd, config_raw, config_identity)
            _revalidate_controller_runtime(runtime_manifest, runtime_artifacts)

        controller = compose_application_bootstrap_controller(
            expected_host="tgw-prod",
            authority=authority,
            application_resolver=resolver,
            terminal_store=terminal,
            provider=provider,
            flake_push=_forbidden,
            flake_switch_record=_forbidden,
            dependency_resubmit=_forbidden,
            controller_evidence=controller_evidence,
            terminal_precheck=terminal_precheck,
        )
        result = controller.execute(request_id=authority.grant.grant_id, effect=authority.grant.effect)
        terminal_precheck()
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
        for artifact in reversed(runtime_artifacts):
            try:
                _revalidate_runtime_artifact(artifact)
            except Exception as exc:
                cleanup_errors.append(exc)
            try:
                os.close(artifact[1])
            except OSError as exc:
                cleanup_errors.append(exc)
        try:
            os.close(config_fd)
        except OSError as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            sys.stderr.write("w09-controller-cleanup:" + sha256(_canonical([type(item).__name__ for item in cleanup_errors])).hexdigest() + "\n")


def _isolated_runtime() -> bool:
    return bool(sys.flags.isolated) and "PYTHONPATH" not in os.environ


def main() -> int:
    if len(sys.argv) != 1:
        raise SystemExit("tgw-w09-application-bootstrap accepts no arguments")
    if not _isolated_runtime():
        raise SystemExit("tgw-w09-application-bootstrap requires its exact isolated launcher")
    result = execute_from_fixed_config()
    sys.stdout.buffer.write(_canonical(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

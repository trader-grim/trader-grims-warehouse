"""Closed remote provider for immutable, non-activating NixOS evaluation."""

from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

REMOTE_HELPER = "/run/current-system/sw/bin/tgw-nixos-reviewed-evaluation"
SSH_COMMAND = ("ssh", "-oBatchMode=yes", "-oClearAllForwardings=yes", "-oStrictHostKeyChecking=yes", "--", "tgw-prod", "sudo", "-n", "--", REMOTE_HELPER)
EXECUTABLES = {"git": "/run/current-system/sw/bin/git", "nix": "/run/current-system/sw/bin/nix", "systemd_analyze": "/run/current-system/sw/bin/systemd-analyze"}
UNITS = (
    "tgw-review-egress@.service",
    "tgw-review-egress-attest@.service",
    "tgw-review-egress-namespace@.service",
)


class EvaluationError(RuntimeError):
    pass


class ArtifactResolver(Protocol):
    def __call__(self, artifact_ref: str) -> Path: ...


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _packet(parameters: Mapping[str, str], archive: Path) -> bytes:
    request = _canonical(dict(parameters))
    if len(request) > 64 * 1024:
        raise EvaluationError("evaluation request is oversized")
    return struct.pack("!Q", len(request)) + request + archive.read_bytes()


class SshReviewedEvaluationProvider:
    """Resolve one content-addressed artifact and invoke one fixed remote helper."""

    def __init__(self, resolve_artifact: ArtifactResolver, *, invoke: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run):
        self.resolve_artifact = resolve_artifact
        self.invoke = invoke

    def __call__(self, parameters: Mapping[str, str]) -> Mapping[str, Any]:
        archive = self.resolve_artifact(parameters["artifact_ref"])
        if not archive.is_file() or _digest_file(archive) != "sha256:" + parameters["source_archive_sha256"].removeprefix("sha256:"):
            raise EvaluationError("resolved source artifact digest mismatch")
        completed = self.invoke(
            list(SSH_COMMAND), input=_packet(parameters, archive), capture_output=True,
            timeout=int(parameters["max_duration_seconds"]) + 30, check=False,
        )
        if completed.returncode:
            raise EvaluationError("remote reviewed evaluation failed")
        if len(completed.stdout) > int(parameters["max_output_bytes"]):
            raise EvaluationError("remote reviewed evaluation output exceeded its bound")
        try:
            result = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvaluationError("remote reviewed evaluation returned malformed JSON") from exc
        if not isinstance(result, dict):
            raise EvaluationError("remote reviewed evaluation receipt is not an object")
        return result


def _run(argv: list[str], *, cwd: Path, timeout: int) -> str:
    completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False, env={"PATH": "/run/current-system/sw/bin:/usr/bin:/bin", "HOME": str(cwd)})
    if completed.returncode:
        raise EvaluationError(f"fixed evaluation step failed: {Path(argv[0]).name}")
    return completed.stdout


def _safe_extract(archive: Path, target: Path) -> str:
    with tarfile.open(archive) as source:
        commit = source.pax_headers.get("comment", "")
        if not commit or len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise EvaluationError("source archive lacks an exact Git commit identity")
        for member in source.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or member.isdev():
                raise EvaluationError("source archive contains an unsafe member")
        source.extractall(target, filter="data")
        return commit


def execute_packet(stream: io.BufferedReader, *, run: Callable[..., str] = _run, scratch_root: Path = Path("/var/tmp/tgw-reviewed-evaluation")) -> dict[str, Any]:
    header = stream.read(8)
    if len(header) != 8:
        raise EvaluationError("evaluation packet header is truncated")
    request_size = struct.unpack("!Q", header)[0]
    if request_size > 64 * 1024:
        raise EvaluationError("evaluation request is oversized")
    request_raw = stream.read(request_size)
    if len(request_raw) != request_size:
        raise EvaluationError("evaluation request is truncated")
    parameters = json.loads(request_raw)
    # Reuse the authority registry's exact closed parser before any filesystem write.
    from tgw.effect_handlers import TypedEffectHandlerRegistry
    from tgw.plan_authority import TypedEffect

    def unavailable(_: Mapping[str, str]) -> Mapping[str, Any]:
        raise EvaluationError("unavailable")

    registry = TypedEffectHandlerRegistry(release_install=unavailable, release_rollback=unavailable, flake_push=unavailable, flake_switch_record=unavailable, dependency_resubmit=unavailable)
    effect = TypedEffect.parse({"kind": "nixos-reviewed-evaluation", "generation": parameters.pop("generation"), "parameters": parameters})
    _, bound, _, _ = registry.prepare(effect)
    if _digest_file(Path(__file__)) != "sha256:" + bound["provider_sha256"].removeprefix("sha256:"):
        raise EvaluationError("installed evaluation provider digest mismatch")
    timeout = int(bound["max_duration_seconds"])
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="run-", dir=scratch_root))
    archive = scratch / "source.tar"
    source = scratch / "source"
    try:
        with archive.open("xb") as sink:
            shutil.copyfileobj(stream, sink, length=1024 * 1024)
        if _digest_file(archive) != "sha256:" + bound["source_archive_sha256"].removeprefix("sha256:"):
            raise EvaluationError("received archive digest mismatch")
        source.mkdir()
        archive_commit = _safe_extract(archive, source)
        if archive_commit != bound["source_commit"]:
            raise EvaluationError("archive commit identity mismatch")
        run([EXECUTABLES["git"], "init", "-q"], cwd=source, timeout=timeout)
        run([EXECUTABLES["git"], "add", "-A"], cwd=source, timeout=timeout)
        if run([EXECUTABLES["git"], "write-tree"], cwd=source, timeout=timeout).strip() != bound["source_tree"]:
            raise EvaluationError("unpacked source tree mismatch")
        lock_matches = _digest_file(source / "flake.lock") == "sha256:" + bound["flake_lock_sha256"].removeprefix("sha256:")
        module_matches = _digest_file(source / bound["module_path"]) == "sha256:" + bound["module_sha256"].removeprefix("sha256:")
        if not lock_matches or not module_matches:
            raise EvaluationError("lock or module digest mismatch")
        base = [EXECUTABLES["nix"], "--offline", "--option", "substituters", "", "--no-write-lock-file"]
        drv = run(base + ["eval", "--raw", ".#nixosConfigurations.tgw-prod.config.system.build.toplevel.drvPath"], cwd=source, timeout=timeout).strip()
        build_log = run(base + ["build", "--no-link", "--print-out-paths", ".#nixosConfigurations.tgw-prod.config.system.build.toplevel"], cwd=source, timeout=timeout)
        closure = build_log.strip()
        if "\n" in closure or not closure.startswith("/nix/store/"):
            raise EvaluationError("Nix build returned an unexpected closure set")
        unit_paths = [Path(closure) / "etc/systemd/system" / unit for unit in UNITS]
        if any(not path.is_file() for path in unit_paths):
            raise EvaluationError("generated unit set is incomplete")
        verify_log = run([EXECUTABLES["systemd_analyze"], "verify", *map(str, unit_paths)], cwd=source, timeout=timeout)
        eval_log = drv + "\n"
        receipt: dict[str, Any] = {
            "schema": bound["output_schema"], "outcome": "verified", "source_commit": bound["source_commit"],
            "source_tree": bound["source_tree"], "source_archive_sha256": bound["source_archive_sha256"],
            "flake_lock_sha256": bound["flake_lock_sha256"], "module_sha256": bound["module_sha256"],
            "provider_sha256": bound["provider_sha256"], "executables": EXECUTABLES,
            "scratch_id": bound["scratch_id"], "cleanup": "removed", "activate": False,
            "profile_write": False, "home_db_write": False, "system": bound["system"],
            "evaluation_target": bound["evaluation_target"], "evaluated_config_drv": drv,
            "evaluated_closure_sha256": "sha256:" + sha256(closure.encode()).hexdigest(),
            "eval_log_sha256": "sha256:" + sha256(eval_log.encode()).hexdigest(),
            "build_log_sha256": "sha256:" + sha256(build_log.encode()).hexdigest(),
            "systemd_verify_output_sha256": "sha256:" + sha256(verify_log.encode()).hexdigest(),
            "systemd_verify_exit": 0, "systemd_version": int(run([EXECUTABLES["systemd_analyze"], "--version"], cwd=source, timeout=timeout).split()[1]),
            "nix_version": run([EXECUTABLES["nix"], "--version"], cwd=source, timeout=timeout).strip(),
            "unit_sha256": {unit: _digest_file(path) for unit, path in zip(UNITS, unit_paths, strict=True)},
            "evidence": [],
        }
        receipt["receipt_sha256"] = "sha256:" + sha256(_canonical({key: value for key, value in receipt.items() if key != "evidence"})).hexdigest()
        receipt["evidence"] = ["nixos-evaluation:" + receipt["receipt_sha256"]]
        return receipt
    finally:
        shutil.rmtree(scratch, ignore_errors=False)


def main() -> int:
    try:
        receipt = execute_packet(sys.stdin.buffer)
        sys.stdout.buffer.write(_canonical(receipt))
        return 0
    except Exception as exc:
        print(f"reviewed evaluation refused: {exc}", file=sys.stderr)
        return 1

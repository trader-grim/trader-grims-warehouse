"""Remote, non-activating executor for ``nixos-a3-successor-evaluation@1``.

The helper consumes two already authenticated archive byte strings.  It never
selects an attribute, repository, command, or path from ambient configuration.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from tgw.nixos_a3_successor_evaluation import (
    FORBIDDEN_EFFECTS,
    INTEGRATION_PUBLIC_FILES,
    RENDERED_ARTIFACTS,
    RENDERED_RELATIVE_PATHS,
    SUCCESS_SCHEMA,
    TARGET_ATTR,
    A3EvaluationError,
    A3KnownFailure,
    digest,
    self_hash,
    validate_request,
)


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes
    attestation: Mapping[str, Any] | None = None
    process_state: str = "REAPED"
    process_facts: Mapping[str, Any] | None = None


Runner = Callable[..., Completed]

_INTEGRATION_MODULE_LINES = (
    "{ inputs, ... }:",
    "{",
    "imports = [ inputs.tgw-lib.nixosModules.a3-platform-bootstrap ];",
    "services.tgw-a3-platform-bootstrap = {",
    "enable = true;",
    "package = inputs.tgw-lib.packages.x86_64-linux.a3-platform-bootstrap;",
    "wrapperConfig = ../../a3-public/nix-observer-render-wrapper.conf;",
    "composition = ../../a3-public/nix-observer-render-composition.json;",
    "prerequisiteReceipt = ../../a3-public/nix-observer-render-prerequisite.json;",
    "attestationPublicKey = ../../a3-public/nix-observer-render-attestation.pub;",
    "sshAuthorizedPublicKey = builtins.readFile ../../a3-public/codex-authorized-key.txt;",
    "};",
    "}",
)


def parse_integration_module(raw: bytes) -> dict[str, Any]:
    """Parse the complete closed fixture grammar; comments/extras are refused."""
    try:
        lines = tuple(line.strip() for line in raw.decode("utf-8").splitlines() if line.strip())
    except UnicodeDecodeError as exc:
        raise A3EvaluationError("reviewed integration module is not UTF-8") from exc
    if lines != _INTEGRATION_MODULE_LINES:
        raise A3EvaluationError("reviewed integration module is outside the exact structural grammar")
    return {
        "module_import": "inputs.tgw-lib.nixosModules.a3-platform-bootstrap",
        "options": {
            "services.tgw-a3-platform-bootstrap.enable": True,
            "services.tgw-a3-platform-bootstrap.package": "inputs.tgw-lib.packages.x86_64-linux.a3-platform-bootstrap",
            "services.tgw-a3-platform-bootstrap.wrapperConfig": "../../a3-public/nix-observer-render-wrapper.conf",
            "services.tgw-a3-platform-bootstrap.composition": "../../a3-public/nix-observer-render-composition.json",
            "services.tgw-a3-platform-bootstrap.prerequisiteReceipt": "../../a3-public/nix-observer-render-prerequisite.json",
            "services.tgw-a3-platform-bootstrap.attestationPublicKey": "../../a3-public/nix-observer-render-attestation.pub",
            "services.tgw-a3-platform-bootstrap.sshAuthorizedPublicKey": "../../a3-public/codex-authorized-key.txt",
        },
    }


def _read_archive_regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise A3EvaluationError("integration public input is not regular")
        raw = bytearray()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        named = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns) or (
            named.st_dev,
            named.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise A3EvaluationError("integration public input changed while held")
        return bytes(raw)
    finally:
        os.close(fd)


def _git_object(kind: str, raw: bytes) -> bytes:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).digest()  # noqa: S324 - Git object identity is SHA-1 by definition


def _git_tree(entries: Mapping[str, tuple[int, bytes]]) -> str:
    tree: dict[str, Any] = {}
    for path, (mode, raw) in entries.items():
        cursor = tree
        parts = path.split("/")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):
                raise A3EvaluationError("archive file/directory collision")
        if parts[-1] in cursor:
            raise A3EvaluationError("archive contains a duplicate Git path")
        cursor[parts[-1]] = (mode, raw)

    def build(node: Mapping[str, Any]) -> bytes:
        value = bytearray()
        # Git compares a directory as though its name had a trailing slash.
        for name in sorted(node, key=lambda item: (item + "/" if isinstance(node[item], dict) else item).encode()):
            item = node[name]
            if isinstance(item, dict):
                mode = b"40000"
                object_id = _git_object("tree", build(item))
            else:
                file_mode, raw = item
                mode = b"100755" if file_mode & 0o111 else b"100644"
                object_id = _git_object("blob", raw)
            value.extend(mode + b" " + name.encode() + b"\0" + object_id)
        return bytes(value)

    return _git_object("tree", build(tree)).hex()


def verify_git_archive(
    raw: bytes,
    target: Path,
    *,
    expected_root: str,
    expected_commit: str,
    expected_tree: str,
    max_files: int,
    max_bytes: int,
) -> tuple[int, int]:
    """Verify Git/PAX identities and extract only regular files/directories."""
    if not raw or len(raw) > max_bytes:
        raise A3EvaluationError("archive byte bound exceeded")
    archive = target.parent / (target.name + ".tar")
    archive.write_bytes(raw)
    entries: dict[str, tuple[int, bytes]] = {}
    total = 0
    with tarfile.open(archive, mode="r:*") as source:
        if source.pax_headers.get("comment") != expected_commit:
            raise A3EvaluationError("archive lacks its exact Git commit PAX identity")
        members = source.getmembers()
        if len(members) > max_files:
            raise A3EvaluationError("archive file-count bound exceeded")
        for member in members:
            posix = PurePosixPath(member.name)
            if posix.is_absolute() or ".." in posix.parts or not posix.parts or posix.parts[0] != expected_root:
                raise A3EvaluationError("archive member escapes the single exact root")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo() or not (member.isdir() or member.isfile()):
                raise A3EvaluationError("archive contains a non-regular member")
            relative = "/".join(posix.parts[1:])
            if not relative:
                if not member.isdir():
                    raise A3EvaluationError("archive root is not a directory")
                continue
            if relative == ".git" or relative.startswith(".git/"):
                raise A3EvaluationError("archive contains forbidden Git administrative state")
            destination = target.joinpath(*posix.parts[1:])
            if member.isdir():
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            if relative in entries:
                raise A3EvaluationError("archive contains duplicate normalized paths")
            extracted = source.extractfile(member)
            if extracted is None:
                raise A3EvaluationError("archive regular file cannot be read")
            content = extracted.read(max_bytes + 1)
            total += len(content)
            if len(content) != member.size or total > max_bytes:
                raise A3EvaluationError("archive unpacked byte bound exceeded")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700 if member.mode & 0o111 else 0o600)
            try:
                offset = 0
                while offset < len(content):
                    count = os.write(fd, content[offset:])
                    if count <= 0:
                        raise OSError("short archive extraction write")
                    offset += count
            finally:
                os.close(fd)
            entries[relative] = (member.mode, content)
    if not entries or _git_tree(entries) != expected_tree:
        raise A3EvaluationError("archive content does not reproduce the exact Git tree")
    return len(entries), total


def _held_tool(value: Mapping[str, Any]) -> tuple[int, str]:
    fd = os.open(value["path"], os.O_RDONLY | os.O_NOFOLLOW)
    metadata = os.fstat(fd)
    raw = bytearray()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        raw.extend(block)
    os.lseek(fd, 0, os.SEEK_SET)
    observed = {
        "sha256": digest(bytes(raw)),
        "size": metadata.st_size,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }
    if not stat.S_ISREG(metadata.st_mode) or any(observed[key] != value[key] for key in observed):
        os.close(fd)
        raise A3EvaluationError("held executable identity differs from the request")
    return fd, f"/proc/{os.getpid()}/fd/{fd}"


def _verify_held_tool(fd: int, value: Mapping[str, Any]) -> None:
    metadata = os.fstat(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    raw = bytearray()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        raw.extend(block)
    named = os.stat(value["path"], follow_symlinks=False)
    if not stat.S_ISREG(named.st_mode) or (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino) or digest(bytes(raw)) != value["sha256"] or len(raw) != value["size"]:
        raise A3EvaluationError("held executable changed during evaluation")


@dataclass
class _HeldRendered:
    fd: int
    raw: bytes
    before: os.stat_result
    resolved: Path
    components: list[tuple[Path, os.stat_result, str | None]]


def _verify_held_rendered(held: _HeldRendered) -> None:
    after = os.fstat(held.fd)
    os.lseek(held.fd, 0, os.SEEK_SET)
    raw = bytearray()
    while len(raw) <= len(held.raw):
        block = os.read(held.fd, min(1024 * 1024, len(held.raw) + 1 - len(raw)))
        if not block:
            break
        raw.extend(block)
    if bytes(raw) != held.raw or (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns) != (
        held.before.st_dev,
        held.before.st_ino,
        held.before.st_size,
        held.before.st_ctime_ns,
    ):
        raise A3EvaluationError("rendered artifact held inode changed after verifier use")
    for named, metadata, link in held.components:
        observed = named.lstat()
        if (observed.st_dev, observed.st_ino, observed.st_mode, observed.st_size, observed.st_ctime_ns) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_ctime_ns,
        ) or (link is not None and os.readlink(named) != link):
            raise A3EvaluationError("rendered artifact named component changed after verifier use")


def _read_rendered(
    path: Path,
    *,
    logical_path: str,
    expected: Mapping[str, Any],
    allow_fixture: bool,
) -> tuple[dict[str, Any], bytes, _HeldRendered]:
    """Hold the resolved store file and prove every named component is unchanged."""
    components: list[tuple[Path, os.stat_result, str | None]] = []
    current = path
    while True:
        metadata = current.lstat()
        link = os.readlink(current) if stat.S_ISLNK(metadata.st_mode) else None
        components.append((current, metadata, link))
        if not stat.S_ISLNK(metadata.st_mode):
            break
        current = (current.parent / link).resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not allow_fixture and not str(resolved).startswith("/nix/store/"):
        raise A3EvaluationError("rendered artifact does not resolve to a bounded store fixture")
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    before = os.fstat(fd)
    raw = bytearray()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        raw.extend(block)
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns):
        os.close(fd)
        raise A3EvaluationError("rendered artifact changed while held")
    for named, metadata, link in components:
        observed = named.lstat()
        if (observed.st_dev, observed.st_ino, observed.st_mode, observed.st_size) != (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size) or (
            link is not None and os.readlink(named) != link
        ):
            os.close(fd)
            raise A3EvaluationError("rendered artifact named identity changed")
    content = bytes(raw)
    if digest(content) != expected["sha256"] or len(content) != expected["size"]:
        os.close(fd)
        raise A3EvaluationError("rendered artifact content differs from the admitted identity")
    identity = {
        "path": logical_path,
        "sha256": digest(content),
        "size": len(content),
        "file_identity": {
            "resolved_path": str(resolved),
            "dev": before.st_dev,
            "ino": before.st_ino,
            "uid": before.st_uid,
            "gid": before.st_gid,
            "mode": stat.S_IMODE(before.st_mode),
            "nlink": before.st_nlink,
        },
    }
    return identity, content, _HeldRendered(fd, content, before, resolved, components)


def _run_exact(
    runner: Runner,
    argv: Sequence[str],
    *,
    failure_stage: str,
    failure_step: str,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    max_output: int,
    pass_fds: Sequence[int],
    attestations: list[Mapping[str, Any]],
    allow_fixture: bool,
) -> Completed:
    try:
        result = runner(list(argv), cwd=cwd, env=dict(env), timeout=timeout, max_output=max_output, pass_fds=tuple(pass_fds))
    except A3KnownFailure as exc:
        if exc.stage == "local-launcher":
            exc.stage = failure_stage
            if type(exc).__name__ == "StepFailure":
                if exc.step == "launcher-identity":
                    exc.returncode = None
                    exc.cleanup = "REMOVED"
                elif exc.step in {"response", "response-contract"}:
                    exc.returncode = None
                    exc.cleanup = "UNKNOWN"
        raise
    if (
        not isinstance(result, Completed)
        or isinstance(result.returncode, bool)
        or not isinstance(result.returncode, int)
        or not -255 <= result.returncode <= 255
        or len(result.stdout) + len(result.stderr) > max_output
    ):
        raise A3KnownFailure("subprocess result violates its closed output contract", stage=failure_stage, step="output-contract")
    if result.process_state != "REAPED":
        raise A3KnownFailure(
            "subprocess process group is not proven reaped",
            stage=failure_stage,
            step="process-state",
            returncode=result.returncode,
            cleanup="UNKNOWN",
        )
    if result.attestation is None:
        if not allow_fixture:
            raise A3KnownFailure("local root launcher omitted signed isolation evidence", stage=failure_stage, step="attestation")
    else:
        attestations.append(result.attestation)
    if result.returncode != 0:
        raise A3KnownFailure(
            "fixed non-activating evaluation step failed",
            stage=failure_stage,
            step=failure_step,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    return result


def execute(
    request_value: Mapping[str, Any],
    *,
    tgw_archive: bytes,
    integration_archive: bytes,
    runner: Runner,
    scratch_parent: Path,
    allow_fixture: bool = False,
    cleanup: Callable[[Path], None] = shutil.rmtree,
    output_resolver: Callable[[str], Path] = Path,
) -> dict[str, Any]:
    if output_resolver is not Path and not allow_fixture:
        raise A3EvaluationError("output path substitution is available only to a non-deployable test fixture")
    request = validate_request(request_value, allow_fixture=allow_fixture)
    policy = request["policy"]
    scratch_parent = Path(scratch_parent)
    run_root = Path(tempfile.mkdtemp(prefix="a3-successor-", dir=scratch_parent)).resolve(strict=True)
    os.chmod(run_root, 0o700)
    source = run_root / "tgw-lib"
    integration = run_root / "tgw-flake"
    source.mkdir(mode=0o700)
    integration.mkdir(mode=0o700)
    tool_fds: list[int] = []
    tool_fd_by_name: dict[str, int] = {}
    rendered_holds: list[_HeldRendered] = []
    verifier_input_fds: list[int] = []
    attestations: list[Mapping[str, Any]] = []
    cleanup_state = "UNKNOWN"
    build_started = False
    try:
        if digest(tgw_archive) != request["source"]["archive_sha256"] or len(tgw_archive) != request["source"]["archive_size"]:
            raise A3EvaluationError("product archive bytes do not match the request")
        if digest(integration_archive) != request["integration"]["archive_sha256"] or len(integration_archive) != request["integration"]["archive_size"]:
            raise A3EvaluationError("integration archive bytes do not match the request")
        if (
            len(tgw_archive) > policy["max_archive_bytes"]
            or len(integration_archive) > policy["max_archive_bytes"]
            or len(tgw_archive) + len(integration_archive) > policy["max_archive_bytes"]
        ):
            raise A3EvaluationError("individual or total archive byte bound exceeded")
        source_counts = verify_git_archive(
            tgw_archive,
            source,
            expected_root="trader-grims-warehouse",
            expected_commit=request["source"]["commit"],
            expected_tree=request["source"]["tree"],
            max_files=policy["max_files"],
            max_bytes=policy["max_unpacked_bytes"],
        )
        integration_counts = verify_git_archive(
            integration_archive,
            integration,
            expected_root="tgw-flake",
            expected_commit=request["integration"]["commit"],
            expected_tree=request["integration"]["tree"],
            max_files=policy["max_files"],
            max_bytes=policy["max_unpacked_bytes"],
        )
        if source_counts[0] + integration_counts[0] > policy["max_files"] or source_counts[1] + integration_counts[1] > policy["max_unpacked_bytes"]:
            raise A3EvaluationError("combined archive unpack bounds exceeded")
        lock = integration / "flake.lock"
        if digest(_read_archive_regular(lock)) != request["integration"]["flake_lock_sha256"]:
            raise A3EvaluationError("reviewed integration flake.lock identity mismatch")
        module = integration / "hosts/tgw-prod/a3-platform-bootstrap.nix"
        if not module.is_file():
            raise A3EvaluationError("reviewed integration module is absent")
        structure = parse_integration_module(_read_archive_regular(module))
        if structure != {
            "module_import": request["integration"]["module_import"],
            "options": request["integration"]["exact_options"],
        }:
            raise A3EvaluationError("reviewed integration module does not implement the admitted structural contract")
        integration_public: dict[str, bytes] = {}
        for name, relative_path in INTEGRATION_PUBLIC_FILES.items():
            raw = _read_archive_regular(integration / relative_path)
            expected_public = request["integration"]["public_files"][name]
            if digest(raw) != expected_public["sha256"] or len(raw) != expected_public["size"]:
                raise A3EvaluationError("integration public file differs from its manifest identity")
            integration_public[name] = raw
        if digest(integration_public["authorized-key-codex"]) != request["credentials"]["authorized_public_key_sha256"] or digest(
            integration_public["attestation-public-key"]
        ) != request["credentials"]["attestation_public_key_sha256"]:
            raise A3EvaluationError("integration public credential bytes differ from request credentials")
        for name in INTEGRATION_PUBLIC_FILES:
            if digest(integration_public[name]) != request["expected_rendered"][name]["sha256"] or len(integration_public[name]) != request["expected_rendered"][name]["size"]:
                raise A3EvaluationError("integration public bytes differ from expected rendered identity")
        held: dict[str, str] = {}
        for name, value in request["tools"].items():
            fd, path = _held_tool(value)
            tool_fds.append(fd)
            tool_fd_by_name[name] = fd
            held[name] = path
        env = {
            "HOME": str(run_root / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NIX_CONFIG": "substituters =\nbuilders =\nuse-substitutes = false\nallow-import-from-derivation = false\nflake-registry =\n",
            "NIX_PATH": "",
            "NIX_REMOTE": "local",
            "PATH": "",
            "TMPDIR": str(run_root / "tmp"),
        }
        Path(env["HOME"]).mkdir(mode=0o700)
        Path(env["TMPDIR"]).mkdir(mode=0o700)
        base = [
            held["nix"],
            "--offline",
            "--option",
            "substituters",
            "",
            "--option",
            "builders",
            "",
            "--option",
            "use-substitutes",
            "false",
            "--option",
            "allow-import-from-derivation",
            "false",
            "--option",
            "pure-eval",
            "true",
            "--option",
            "sandbox",
            "true",
            "--option",
            "sandbox-fallback",
            "false",
            "--option",
            "allowed-uris",
            "",
        ]
        flake_flags = ["--no-write-lock-file", "--override-input", "tgw-lib", "path:" + str(source)]
        flake_target = f"{integration}#{TARGET_ATTR}"
        common = {
            "cwd": integration,
            "env": env,
            "timeout": policy["max_seconds"],
            "max_output": policy["max_output_bytes"],
            "pass_fds": list(tool_fds),
            "attestations": attestations,
            "allow_fixture": allow_fixture,
        }
        version_flags = {"nix": "--version", "nix_store": "--version", "sshd": "-V", "systemd_analyze": "--version"}
        version_steps = {"nix": "nix-version", "nix_store": "nix-store-version", "sshd": "sshd-version", "systemd_analyze": "systemd-version"}
        version_results = {
            name: _run_exact(runner, [held[name], flag], failure_stage="evaluation", failure_step=version_steps[name], **common)
            for name, flag in version_flags.items()
        }
        for expected in request["input_closure"]["paths"]:
            path_info = _run_exact(
                runner,
                [*base, "path-info", "--json", expected["path"]],
                failure_stage="evaluation",
                failure_step="path-info",
                **common,
            ).stdout
            try:
                input_info = json_loads(path_info)
            except Exception as exc:
                raise A3EvaluationError("offline input closure size evidence is invalid") from exc
            metadata = input_info.get(expected["path"]) if isinstance(input_info, Mapping) else None
            if (
                not isinstance(metadata, Mapping)
                or metadata.get("narSize") != expected["nar_size"]
                or _nar_hash_hex(metadata.get("narHash")) != expected["nar_sha256"]
            ):
                raise A3EvaluationError("offline input closure NAR size mismatch")
            observed = (
                _run_exact(
                    runner,
                    [*base, "hash", "path", "--type", "sha256", "--base16", expected["path"]],
                    failure_stage="evaluation",
                    failure_step="nix-hash",
                    **common,
                )
                .stdout.decode()
                .strip()
            )
            if observed != expected["nar_sha256"].removeprefix("sha256:"):
                raise A3EvaluationError("offline input closure NAR identity mismatch")
        drv = _run_exact(
            runner,
            [*base, "eval", *flake_flags, "--raw", flake_target + ".drvPath"],
            failure_stage="evaluation",
            failure_step="nix-eval",
            **common,
        ).stdout.decode().strip()
        build_started = True
        output = _run_exact(
            runner,
            [*base, "build", *flake_flags, "--no-link", "--print-out-paths", flake_target],
            failure_stage="nix-build",
            failure_step="nix-build",
            **common,
        ).stdout.decode().strip()
        if "\n" in drv or "\n" in output or output != request["target"]["expected_successor"]:
            raise A3EvaluationError("Nix returned multiple derivations or outputs")
        derivation_raw = _run_exact(
            runner,
            [*base, "derivation", "show", drv],
            failure_stage="post-build",
            failure_step="nix-store",
            **common,
        ).stdout
        try:
            derivation = json_loads(derivation_raw)
            outputs = derivation[drv]["outputs"]
        except Exception as exc:
            raise A3EvaluationError("derivation output relation is invalid") from exc
        if outputs != {"out": {"path": output}}:
            raise A3EvaluationError("derivation does not bind the exact singleton successor output")
        requisites_raw = _run_exact(
            runner,
            [held["nix_store"], "--query", "--requisites", output],
            failure_stage="post-build",
            failure_step="nix-store",
            **common,
        ).stdout
        try:
            requisites = requisites_raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise A3EvaluationError("Nix store requisites are not UTF-8") from exc
        if (
            len(requisites) < 2
            or len(requisites) > 100_000
            or requisites != sorted(requisites)
            or len(set(requisites)) != len(requisites)
            or output not in requisites
            or any(not _store_path(path) for path in requisites)
        ):
            raise A3EvaluationError("Nix store requisites are incomplete, duplicate, unsorted, or malformed")
        manifest = []
        for path in requisites:
            path_info = _run_exact(
                runner,
                [*base, "path-info", "--json", path],
                failure_stage="post-build",
                failure_step="path-info",
                **common,
            ).stdout
            try:
                info = json_loads(path_info)
                metadata = info[path]
            except Exception as exc:
                raise A3EvaluationError("recursive Nix store metadata entry is invalid") from exc
            if not isinstance(metadata, Mapping) or not isinstance(metadata.get("narSize"), int) or metadata["narSize"] <= 0:
                raise A3EvaluationError("recursive Nix store metadata entry is invalid")
            observed = (
                _run_exact(
                    runner,
                    [*base, "hash", "path", "--type", "sha256", "--base16", path],
                    failure_stage="post-build",
                    failure_step="nix-hash",
                    **common,
                )
                .stdout.decode()
                .strip()
            )
            if not re_fullmatch_sha256(observed) or _nar_hash_hex(metadata.get("narHash")) != "sha256:" + observed:
                raise A3EvaluationError("recursive Nix store NAR hash is invalid")
            manifest.append({"path": path, "nar_sha256": "sha256:" + observed, "nar_size": metadata["narSize"]})
        rendered: dict[str, dict[str, Any]] = {}
        rendered_bytes: dict[str, bytes] = {}
        output_root = output_resolver(output)
        for name in RENDERED_ARTIFACTS:
            path = output_root / RENDERED_RELATIVE_PATHS[name]
            identity, content, rendered_hold = _read_rendered(
                path,
                logical_path=str(Path(output) / RENDERED_RELATIVE_PATHS[name]),
                expected=request["expected_rendered"][name],
                allow_fixture=allow_fixture,
            )
            rendered[name], rendered_bytes[name] = identity, content
            rendered_holds.append(rendered_hold)
        for name in INTEGRATION_PUBLIC_FILES:
            if rendered_bytes[name] != integration_public[name]:
                raise A3EvaluationError("rendered public artifact bytes differ from reviewed integration input")

        verifier_inputs: list[tuple[Path, int, bytes, os.stat_result]] = []

        def materialize(name: str, raw: bytes, *, named_path: bool = False) -> tuple[int, str]:
            path = run_root / name
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            offset = 0
            while offset < len(raw):
                count = os.write(fd, raw[offset:])
                if count <= 0:
                    os.close(fd)
                    raise A3EvaluationError("short verifier-input write")
                offset += count
            os.fsync(fd)
            before = os.fstat(fd)
            verifier_inputs.append((path, fd, raw, before))
            verifier_input_fds.append(fd)
            common["pass_fds"].append(fd)
            return fd, str(path) if named_path else f"/proc/{os.getpid()}/fd/{fd}"

        _, sshd_proc = materialize("sshd-verifier.conf", rendered_bytes["sshd-config"])
        _, systemd_proc = materialize("sshd.service", rendered_bytes["sshd-service"], named_path=True)
        sshd_command = [held["sshd"], "-T", "-C", "user=codex,host=tgw-prod,addr=127.0.0.1", "-f", sshd_proc]
        systemd_command = [held["systemd_analyze"], "verify", "--man=no", systemd_proc]
        sshd_result = _run_exact(
            runner,
            sshd_command,
            failure_stage="static-verification",
            failure_step="sshd-verify",
            **common,
        )
        systemd_result = _run_exact(
            runner,
            systemd_command,
            failure_stage="static-verification",
            failure_step="systemd-verify",
            **common,
        )
        for path, fd, expected_raw, before in verifier_inputs:
            os.lseek(fd, 0, os.SEEK_SET)
            observed_raw = bytearray()
            while len(observed_raw) <= len(expected_raw):
                block = os.read(fd, min(1024 * 1024, len(expected_raw) + 1 - len(observed_raw)))
                if not block:
                    break
                observed_raw.extend(block)
            after = os.fstat(fd)
            try:
                named = path.lstat()
            except OSError as exc:
                raise A3EvaluationError("verifier input named path disappeared during use") from exc
            if bytes(observed_raw) != expected_raw or (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_ctime_ns,
            ) or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino):
                raise A3EvaluationError("verifier input changed while held or by named replacement")
        for rendered_hold in rendered_holds:
            _verify_held_rendered(rendered_hold)
        for path, fd, _, _ in verifier_inputs:
            common["pass_fds"].remove(fd)
            os.close(fd)
            verifier_input_fds.remove(fd)
        logical_sshd_command = [request["tools"]["sshd"]["path"], "-T", "-C", "user=codex,host=tgw-prod,addr=127.0.0.1", "-f", rendered["sshd-config"]["path"]]
        logical_systemd_command = [request["tools"]["systemd_analyze"]["path"], "verify", "--man=no", "sshd.service"]
        verifiers = {
            "sshd": {
                "command": logical_sshd_command,
                "actual_command": sshd_command,
                "version_command": [request["tools"]["sshd"]["path"], "-V"],
                "actual_version_command": [held["sshd"], "-V"],
                "executable": request["tools"]["sshd"],
                "returncode": 0,
                "stdout_sha256": digest(sshd_result.stdout),
                "stderr_sha256": digest(sshd_result.stderr),
                "version_stdout_sha256": digest(version_results["sshd"].stdout),
                "version_stderr_sha256": digest(version_results["sshd"].stderr),
            },
            "systemd_analyze": {
                "command": logical_systemd_command,
                "actual_command": systemd_command,
                "version_command": [request["tools"]["systemd_analyze"]["path"], "--version"],
                "actual_version_command": [held["systemd_analyze"], "--version"],
                "executable": request["tools"]["systemd_analyze"],
                "returncode": 0,
                "stdout_sha256": digest(systemd_result.stdout),
                "stderr_sha256": digest(systemd_result.stderr),
                "version_stdout_sha256": digest(version_results["systemd_analyze"].stdout),
                "version_stderr_sha256": digest(version_results["systemd_analyze"].stderr),
            },
        }
        tool_versions = {
            name: {
                "command": [request["tools"][name]["path"], version_flags[name]],
                "actual_command": [held[name], version_flags[name]],
                "executable": request["tools"][name],
                "returncode": 0,
                "stdout_sha256": digest(version_results[name].stdout),
                "stderr_sha256": digest(version_results[name].stderr),
            }
            for name in version_flags
        }
        for name, fd in tool_fd_by_name.items():
            try:
                _verify_held_tool(fd, request["tools"][name])
            except A3EvaluationError as exc:
                raise A3KnownFailure(str(exc), stage="post-build", step="tool-identity", cleanup="REMOVED") from exc
        receipt: dict[str, Any] = {
            "schema": SUCCESS_SCHEMA,
            "outcome": "SUCCEEDED",
            "request_sha256": request["request_sha256"],
            "operation_id": request["operation_id"],
            "source": request["source"],
            "integration": request["integration"],
            "target": request["target"],
            "derivation": drv,
            "output_path": output,
            "store_manifest": manifest,
            "store_manifest_sha256": digest(manifest),
            "rendered_artifacts": rendered,
            "tool_versions": tool_versions,
            "verifiers": verifiers,
            "isolation": {
                "schema": "tgw-nixos-a3-local-isolation-summary/v1",
                "kind": "root-launcher-fresh-netns-per-command",
                "composition_sha256": attestations[0]["signed_attestation"]["composition_sha256"] if attestations else digest({"test_transport_no_launcher_evidence": True}),
                "command_count": len(attestations),
                "launch_evidence_sha256": digest(attestations),
                "launcher_attested": bool(attestations),
                "network_observed": False,
            },
            "launcher_evidence": list(attestations),
            "effects": {"build": True, **{name: False for name in FORBIDDEN_EFFECTS}},
            "cleanup": "REMOVED",
            "deployable": request["integration"]["status"] == "REVIEWED_EXECUTABLE" and request["credentials"]["final"] is True,
        }
    except A3KnownFailure:
        raise
    except A3EvaluationError as exc:
        raise A3KnownFailure(
            str(exc),
            stage="post-build" if build_started else "prebuild-validation",
            step="contract-validation",
            cleanup="REMOVED",
        ) from exc
    finally:
        for fd in verifier_input_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        for rendered_hold in rendered_holds:
            try:
                os.close(rendered_hold.fd)
            except OSError:
                pass
        for fd in tool_fds:
            os.close(fd)
        try:
            cleanup(run_root)
            cleanup_state = "REMOVED" if not run_root.exists() else "UNKNOWN"
        except Exception as exc:
            raise A3EvaluationError("isolated scratch cleanup failed") from exc
    if cleanup_state != "REMOVED":
        raise A3EvaluationError("isolated scratch cleanup is ambiguous")
    receipt["receipt_sha256"] = self_hash(receipt)
    receipt["evidence"] = ["nixos-a3-successor-evaluation:" + receipt["receipt_sha256"]]
    return receipt


def json_loads(raw: bytes) -> Any:
    import json

    return json.loads(raw)


def re_fullmatch_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _nar_hash_hex(value: Any) -> str:
    if isinstance(value, str) and value.startswith("sha256:") and re_fullmatch_sha256(value[7:]):
        return value
    if isinstance(value, str) and value.startswith("sha256-"):
        try:
            raw = base64.b64decode(value[7:], validate=True)
        except ValueError as exc:
            raise A3EvaluationError("Nix narHash encoding is invalid") from exc
        if len(raw) == 32:
            return "sha256:" + raw.hex()
    raise A3EvaluationError("Nix narHash is not exact SHA-256")


def _store_path(value: str) -> bool:
    if not value.startswith("/nix/store/") or "\n" in value or "\r" in value:
        return False
    name = value.removeprefix("/nix/store/")
    return len(name) >= 34 and name[32] == "-" and all(char in "0123456789abcdfghijklmnpqrsvwxyz" for char in name[:32])

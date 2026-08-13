"""Remote, non-activating executor for ``nixos-a3-successor-evaluation@1``.

The helper consumes two already authenticated archive byte strings.  It never
selects an attribute, repository, command, or path from ambient configuration.
"""

from __future__ import annotations

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
    RENDERED_ARTIFACTS,
    RENDERED_RELATIVE_PATHS,
    SUCCESS_SCHEMA,
    TARGET_ATTR,
    A3EvaluationError,
    digest,
    self_hash,
    validate_request,
)


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes


Runner = Callable[..., Completed]


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
) -> None:
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


def _run_exact(
    runner: Runner,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    max_output: int,
    pass_fds: Sequence[int],
) -> Completed:
    result = runner(list(argv), cwd=cwd, env=dict(env), timeout=timeout, max_output=max_output, pass_fds=tuple(pass_fds))
    if not isinstance(result, Completed) or len(result.stdout) + len(result.stderr) > max_output:
        raise A3EvaluationError("subprocess result violates its closed output contract")
    if result.returncode != 0:
        raise A3EvaluationError("fixed non-activating evaluation step failed")
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
    run_root = Path(tempfile.mkdtemp(prefix="a3-successor-", dir=scratch_parent))
    os.chmod(run_root, 0o700)
    source = run_root / "tgw-lib"
    integration = run_root / "tgw-flake"
    source.mkdir(mode=0o700)
    integration.mkdir(mode=0o700)
    tool_fds: list[int] = []
    cleanup_state = "UNKNOWN"
    try:
        if digest(tgw_archive) != request["source"]["archive_sha256"] or len(tgw_archive) != request["source"]["archive_size"]:
            raise A3EvaluationError("product archive bytes do not match the request")
        if digest(integration_archive) != request["integration"]["archive_sha256"] or len(integration_archive) != request["integration"]["archive_size"]:
            raise A3EvaluationError("integration archive bytes do not match the request")
        verify_git_archive(
            tgw_archive,
            source,
            expected_root="trader-grims-warehouse",
            expected_commit=request["source"]["commit"],
            expected_tree=request["source"]["tree"],
            max_files=policy["max_files"],
            max_bytes=policy["max_unpacked_bytes"],
        )
        verify_git_archive(
            integration_archive,
            integration,
            expected_root="tgw-flake",
            expected_commit=request["integration"]["commit"],
            expected_tree=request["integration"]["tree"],
            max_files=policy["max_files"],
            max_bytes=policy["max_unpacked_bytes"],
        )
        lock = integration / "flake.lock"
        if not lock.is_file() or digest(lock.read_bytes()) != request["integration"]["flake_lock_sha256"]:
            raise A3EvaluationError("reviewed integration flake.lock identity mismatch")
        module = integration / "hosts/tgw-prod/a3-platform-bootstrap.nix"
        if not module.is_file():
            raise A3EvaluationError("reviewed integration module is absent")
        module_text = module.read_text()
        required_integration_lines = (
            "imports = [ inputs.tgw-lib.nixosModules.a3-platform-bootstrap ];",
            "tgw.a3PlatformBootstrap.enable = true;",
            'tgw.a3PlatformBootstrap.authorizedPublicKeyRef = "external:root-owned-a3-authorized-ed25519-public-key";',
            'tgw.a3PlatformBootstrap.attestationPublicKeyRef = "external:a3-attestation-ed25519-public-verifier";',
        )
        if any(module_text.count(line) != 1 for line in required_integration_lines):
            raise A3EvaluationError("reviewed integration module does not implement the exact A3 contract")
        held: dict[str, str] = {}
        for name, value in request["tools"].items():
            fd, path = _held_tool(value)
            tool_fds.append(fd)
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
        common = {"cwd": integration, "env": env, "timeout": policy["max_seconds"], "max_output": policy["max_output_bytes"], "pass_fds": tool_fds}
        for expected in request["input_closure"]["paths"]:
            observed = (
                _run_exact(
                    runner,
                    [*base, "hash", "path", "--type", "sha256", "--base16", expected["path"]],
                    **common,
                )
                .stdout.decode()
                .strip()
            )
            if observed != expected["nar_sha256"].removeprefix("sha256:"):
                raise A3EvaluationError("offline input closure NAR identity mismatch")
        drv = _run_exact(runner, [*base, "eval", *flake_flags, "--raw", flake_target + ".drvPath"], **common).stdout.decode().strip()
        output = _run_exact(runner, [*base, "build", *flake_flags, "--no-link", "--print-out-paths", flake_target], **common).stdout.decode().strip()
        if "\n" in drv or "\n" in output:
            raise A3EvaluationError("Nix returned multiple derivations or outputs")
        path_info = _run_exact(runner, [*base, "path-info", "--json", "--recursive", output], **common).stdout
        try:
            info = json_loads(path_info)
        except Exception as exc:
            raise A3EvaluationError("recursive Nix store path metadata is invalid") from exc
        if not isinstance(info, Mapping) or not info:
            raise A3EvaluationError("recursive Nix store path metadata is not an object")
        manifest = []
        for path, metadata in sorted(info.items()):
            if not isinstance(metadata, Mapping) or "narSize" not in metadata:
                raise A3EvaluationError("recursive Nix store metadata entry is invalid")
            observed = (
                _run_exact(
                    runner,
                    [*base, "hash", "path", "--type", "sha256", "--base16", path],
                    **common,
                )
                .stdout.decode()
                .strip()
            )
            if not re_fullmatch_sha256(observed):
                raise A3EvaluationError("recursive Nix store NAR hash is invalid")
            manifest.append({"path": path, "nar_sha256": "sha256:" + observed, "nar_size": metadata["narSize"]})
        rendered: dict[str, dict[str, Any]] = {}
        output_root = output_resolver(output)
        for name in RENDERED_ARTIFACTS:
            path = output_root / RENDERED_RELATIVE_PATHS[name]
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise A3EvaluationError("rendered A3 artifact is not regular")
            raw = path.read_bytes()
            rendered[name] = {
                "path": str(Path(output) / RENDERED_RELATIVE_PATHS[name]),
                "sha256": digest(raw),
                "size": len(raw),
            }
        systemd_command = [held["systemd_analyze"], "verify", "--root", output, "tgw-a3-platform-bootstrap.service"]
        sshd_command = [held["sshd"], "-T", "-C", "user=tgw-a3-bootstrap,host=tgw-prod,addr=127.0.0.1", "-f", rendered["sshd-effective-config"]["path"]]
        systemd_result = _run_exact(runner, systemd_command, **common)
        sshd_result = _run_exact(runner, sshd_command, **common)
        verifiers = {
            "systemd_analyze": {
                "command": [request["tools"]["systemd_analyze"]["path"], *systemd_command[1:]],
                "executable": request["tools"]["systemd_analyze"],
                "returncode": 0,
                "stdout_sha256": digest(systemd_result.stdout),
                "stderr_sha256": digest(systemd_result.stderr),
            },
            "sshd": {
                "command": [request["tools"]["sshd"]["path"], *sshd_command[1:]],
                "executable": request["tools"]["sshd"],
                "returncode": 0,
                "stdout_sha256": digest(sshd_result.stdout),
                "stderr_sha256": digest(sshd_result.stderr),
            },
        }
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
            "verifiers": verifiers,
            "effects": {"build": True, **{name: False for name in FORBIDDEN_EFFECTS}},
            "cleanup": "REMOVED",
            "deployable": request["integration"]["status"] == "REVIEWED_EXECUTABLE" and request["credentials"]["final"] is True,
        }
    finally:
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

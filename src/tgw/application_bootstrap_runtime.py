"""Protected W09 controller runtime materialization.

This module turns a reviewed native-launcher build, the retained controller
source receipt, exact Python/native files, and immutable dependency trees into
the three fixed artifacts consumed by ``w09_controller_launcher.c``.  It does
not discover dependencies from PATH or execute the application deployment.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from tgw.application_bootstrap_bundle import (
    ControllerBundleError,
    _canonical,
    _digest,
    _open_protected_root,
    _write_once,
)
from tgw.application_bootstrap_entrypoint import (
    RUNTIME_SCHEMA,
    _elf_closure,
    _preexec_closure,
    _tree_digest,
)

SCHEMA = "tgw-w09-controller-runtime-materialization/v1"
BUILD_SCHEMA = "tgw-w09-controller-launcher-build/v1"
BUILD_ENVIRONMENT_SCHEMA = "tgw-w08-controller-build-environment/v1"
SOURCE_SCHEMA = "tgw-w09-controller-bundle-receipt/v1"
_SHA = re.compile(r"sha256:[0-9a-f]{64}")
_GIT = re.compile(r"[0-9a-f]{40}")


class ControllerRuntimeError(ValueError):
    pass


def _validate_build_environment(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {
        "schema",
        "compiler",
        "tracer",
        "cwd",
        "scratch",
        "inputs",
        "accesses",
        "discovery_trace_sha256",
        "discovery_directories",
        "environment",
        "closure_sha256",
        "receipt_sha256",
    }:
        raise ControllerRuntimeError("controller build environment schema is not exact")
    binding_fields = {"path", "sha256", "dev", "ino", "uid", "gid", "mode", "nlink", "size"}
    directory_binding_fields = binding_fields | {"mtime_ns", "ctime_ns"}
    compiler = value.get("compiler")
    tracer = value.get("tracer")
    cwd = value.get("cwd")
    scratch = value.get("scratch")
    inputs = value.get("inputs")
    accesses = value.get("accesses")
    environment = value.get("environment")
    discovery_directories = value.get("discovery_directories")
    if (
        not isinstance(compiler, Mapping)
        or set(compiler) != binding_fields
        or not isinstance(tracer, Mapping)
        or set(tracer) != binding_fields
        or not isinstance(cwd, Mapping)
        or set(cwd) != directory_binding_fields
        or cwd.get("mode") != 0o700
        or not isinstance(scratch, Mapping)
        or set(scratch) != directory_binding_fields
        or scratch.get("mode") != 0o700
        or not isinstance(inputs, list)
        or any(not isinstance(item, Mapping) or set(item) != binding_fields for item in inputs)
        or inputs != sorted(inputs, key=lambda item: str(item["path"]))
        or len({item["path"] for item in inputs}) != len(inputs)
        or not isinstance(accesses, list)
        or accesses != sorted(set(accesses))
        or any(not isinstance(item, str) or not item for item in accesses)
        or _SHA.fullmatch(str(value.get("discovery_trace_sha256"))) is None
        or not isinstance(discovery_directories, Mapping)
        or set(discovery_directories) != {"cwd", "scratch_before", "scratch_after"}
        or any(
            not isinstance(item, Mapping) or set(item) != directory_binding_fields
            for item in discovery_directories.values()
        )
        or discovery_directories["cwd"] != cwd
        or discovery_directories["scratch_after"] != scratch
        or any(
            discovery_directories["scratch_before"][name]
            != discovery_directories["scratch_after"][name]
            for name in ("path", "dev", "ino", "uid", "gid", "mode", "nlink", "sha256")
        )
        or not isinstance(environment, Mapping)
        or set(environment) != {"PATH", "LANG", "LC_ALL", "TMPDIR"}
        or environment["LANG"] != "C"
        or environment["LC_ALL"] != "C"
        or scratch.get("path") != environment["TMPDIR"]
        or cwd.get("path") == scratch.get("path")
        or value.get("closure_sha256") != _digest(_canonical(inputs))
    ):
        raise ControllerRuntimeError("controller build environment content is invalid")
    return dict(value)


def _run_build_trace(
    *,
    tracer_fd: int,
    compiler_fd: int,
    source_fd: int,
    output_path: str,
    binding_path: str,
    environment: Mapping[str, str],
    cwd_fd: int,
    extra_fds: Sequence[int] = (),
) -> tuple[bytes, bytes, list[str]]:
    from tgw.a3_preintegration_observation import _run_held_bounded

    command = [
        f"/proc/self/fd/{tracer_fd}",
        "-f",
        "-qq",
        "-s",
        "4096",
        "-e",
        "trace=%file",
        "--",
        f"/proc/self/fd/{compiler_fd}",
        "-static",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        f'-DBINDING_PATH="{binding_path}"',
        "-o",
        output_path,
        "-x",
        "c",
        f"/proc/{os.getpid()}/fd/{source_fd}",
    ]
    returncode, stdout, stderr = _run_held_bounded(
        command,
        pass_fds=tuple({tracer_fd, compiler_fd, source_fd, cwd_fd, *extra_fds}),
        timeout=120,
        limit=16 * 1024 * 1024,
        env=environment,
        cwd=f"/proc/{os.getpid()}/fd/{cwd_fd}",
    )
    if returncode != 0:
        raise ControllerRuntimeError("controller launcher compiler trace failed")
    return stdout, stderr, command


_ONE_PATH_SYSCALLS = {
    "access", "chdir", "chmod", "chown", "creat", "execve", "lchown",
    "lstat", "mkdir", "mknod", "open", "readlink", "rmdir", "stat",
    "getcwd", "statfs", "truncate", "unlink", "utime", "utimes",
    "faccessat", "faccessat2", "fchmodat", "fchownat", "mkdirat", "mknodat",
    "newfstatat", "openat", "openat2", "readlinkat", "statx", "unlinkat",
}
_TWO_PATH_SYSCALLS = {
    "link", "linkat", "rename", "renameat", "renameat2", "symlink", "symlinkat",
}
_QUOTED = re.compile(r'"(?:\\.|[^"\\])*"')


def _complete_trace_lines(raw: bytes) -> list[str]:
    pending: dict[str, str] = {}
    completed = []
    for raw_line in raw.decode("utf-8", errors="strict").splitlines():
        line = raw_line.strip()
        match = re.match(r"^(\[pid\s+[0-9]+\]\s+)?(.*)$", line)
        if match is None:
            raise ControllerRuntimeError("compiler trace line prefix is invalid")
        pid = match.group(1) or "leader"
        body = match.group(2)
        if body.startswith(("--- ", "+++ ")):
            continue
        if body.endswith(" <unfinished ...>"):
            if pid in pending:
                raise ControllerRuntimeError("compiler trace has nested unfinished records")
            pending[pid] = body.removesuffix(" <unfinished ...>")
            continue
        resumed = re.match(r"^<\.\.\.\s+([a-zA-Z0-9_]+) resumed>(.*)$", body)
        if resumed is not None:
            prefix = pending.pop(pid, None)
            if prefix is None or not prefix.startswith(resumed.group(1) + "("):
                raise ControllerRuntimeError("compiler trace resumed record is unmatched")
            body = prefix + resumed.group(2)
        completed.append(body)
    if pending:
        raise ControllerRuntimeError("compiler trace ended with unfinished records")
    return completed


def _normalize_trace_path(path: str, *, scratch: Path) -> tuple[str, Path | None]:
    if not path.startswith("/"):
        raise ControllerRuntimeError("compiler trace contains a relative file access")
    lexical = Path(os.path.normpath(path))
    if lexical == scratch:
        return "$SCRATCH", None
    if lexical.is_relative_to(scratch):
        relative = lexical.relative_to(scratch).as_posix()
        compiler_temporary = re.fullmatch(
            r"cc[A-Za-z0-9]{6}(\.(?:o|s|res|cdtor\.o|cdtor\.c))",
            relative,
        )
        if compiler_temporary is not None:
            return "$SCRATCH/$CC_TMP" + compiler_temporary.group(1), None
        return "$SCRATCH/" + relative, None
    if str(lexical).startswith("/proc/"):
        parts = list(lexical.parts)
        if len(parts) > 2 and (parts[2] == "self" or parts[2].isdecimal()):
            parts[2] = "$PID"
        if "fd" in parts:
            index = parts.index("fd")
            if len(parts) > index + 1:
                match = re.fullmatch(r"[0-9]+(.*)", parts[index + 1])
                if match is not None:
                    parts[index + 1] = "$FD" + match.group(1)
        return "/".join(parts), None
    try:
        resolved = lexical.resolve(strict=True)
    except FileNotFoundError:
        return str(lexical), None
    metadata = resolved.lstat()
    if stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
        return str(resolved), resolved
    return str(resolved), None


def _parse_trace_accesses(
    raw: bytes,
    *,
    scratch: Path,
    excluded: set[Path],
) -> tuple[list[Path], list[str]]:
    regular = set()
    accesses = set()
    excluded_resolved = {path.resolve() for path in excluded}
    for line in _complete_trace_lines(raw):
        if re.search(r'"(?:\\.|[^"\\])*"\.\.\.', line):
            raise ControllerRuntimeError("compiler trace path arguments are truncated")
        syscall = re.match(r"^([a-zA-Z0-9_]+)\(", line)
        if syscall is None:
            raise ControllerRuntimeError("compiler trace contains an unparsed record")
        name = syscall.group(1)
        if name in _ONE_PATH_SYSCALLS:
            count = 1
        elif name in _TWO_PATH_SYSCALLS:
            count = 2
        else:
            raise ControllerRuntimeError(
                "compiler trace contains an unknown file syscall: " + name
            )
        encoded = _QUOTED.findall(line)
        if len(encoded) < count or (count and "..." in encoded[0]):
            raise ControllerRuntimeError("compiler trace path arguments are incomplete")
        for token in encoded[:count]:
            try:
                decoded = ast.literal_eval(token)
            except (SyntaxError, ValueError) as exc:
                raise ControllerRuntimeError("compiler trace path escape is invalid") from exc
            if not isinstance(decoded, str):
                raise ControllerRuntimeError("compiler trace path is not text")
            lexical = Path(os.path.normpath(decoded))
            if lexical.is_absolute():
                try:
                    resolved_for_exclusion = lexical.resolve(strict=True)
                except FileNotFoundError:
                    resolved_for_exclusion = lexical
                if resolved_for_exclusion in excluded_resolved:
                    accesses.add("$OUTPUT")
                    continue
            normalized, existing = _normalize_trace_path(decoded, scratch=scratch)
            accesses.add(normalized)
            if existing is not None and existing not in excluded_resolved:
                regular.add(existing)
    return sorted(regular, key=str), sorted(accesses)


def _postcheck_binding(expected: Mapping[str, Any], fd: int, *, directory: bool) -> None:
    held = os.fstat(fd)
    named = os.stat(expected["path"], follow_symlinks=False)
    identity_checks = [
        ("dev", held.st_dev),
        ("ino", held.st_ino),
        ("uid", held.st_uid),
        ("gid", held.st_gid),
        ("mode", stat.S_IMODE(held.st_mode)),
        ("nlink", held.st_nlink),
    ]
    if directory:
        identity_checks.extend(
            [
                ("size", held.st_size),
                ("mtime_ns", held.st_mtime_ns),
                ("ctime_ns", held.st_ctime_ns),
            ]
        )
    named_values = {
        "dev": named.st_dev,
        "ino": named.st_ino,
        "uid": named.st_uid,
        "gid": named.st_gid,
        "mode": stat.S_IMODE(named.st_mode),
        "nlink": named.st_nlink,
        "size": named.st_size,
        "mtime_ns": named.st_mtime_ns,
        "ctime_ns": named.st_ctime_ns,
    }
    for name, value in identity_checks:
        if expected[name] != value or value != named_values[name]:
            raise ControllerRuntimeError("controller build input changed during use")
    if directory:
        if _tree_digest(
            Path(f"/proc/self/fd/{fd}"),
            trusted_uid=held.st_uid,
            trusted_gid=held.st_gid,
        ) != expected["sha256"]:
            raise ControllerRuntimeError("controller build directory changed during use")
        return
    raw = os.pread(fd, expected["size"] + 1, 0)
    if len(raw) != expected["size"] or _digest(raw) != expected["sha256"]:
        raise ControllerRuntimeError("controller build file changed during use")


def _observe_directory_phase(expected: Mapping[str, Any], fd: int) -> dict[str, Any]:
    """Observe one exact empty-directory boundary through held and named identities."""

    held = os.fstat(fd)
    observed = {
        "path": expected["path"],
        "dev": held.st_dev,
        "ino": held.st_ino,
        "uid": held.st_uid,
        "gid": held.st_gid,
        "mode": stat.S_IMODE(held.st_mode),
        "nlink": held.st_nlink,
        "size": held.st_size,
        "sha256": _tree_digest(
            Path(f"/proc/self/fd/{fd}"),
            trusted_uid=held.st_uid,
            trusted_gid=held.st_gid,
        ),
        "mtime_ns": held.st_mtime_ns,
        "ctime_ns": held.st_ctime_ns,
    }
    named = os.stat(expected["path"], follow_symlinks=False)
    if (
        any(
            observed[name] != expected[name]
            for name in ("path", "dev", "ino", "uid", "gid", "mode", "nlink", "sha256")
        )
        or (held.st_dev, held.st_ino, held.st_mtime_ns, held.st_ctime_ns)
        != (named.st_dev, named.st_ino, named.st_mtime_ns, named.st_ctime_ns)
    ):
        raise ControllerRuntimeError("controller build directory phase is not exact")
    return observed


def discover_launcher_build_inputs(
    *,
    launcher_source: Path,
    compiler_path: Path,
    tracer_path: Path,
    output_root: Path,
    binding_path: Path,
    environment: Mapping[str, str],
    cwd_path: Path,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Run a non-admitted discovery build and return its concrete file inputs."""
    held: list[int] = []
    root_fd = -1
    output_fd = -1
    output_created = False
    output_name = "launcher-discovery"
    try:
        if (
            set(environment) != {"PATH", "LANG", "LC_ALL", "TMPDIR"}
            or environment["LANG"] != "C"
            or environment["LC_ALL"] != "C"
        ):
            raise ControllerRuntimeError("controller build environment is not exact")
        scratch, scratch_fd = _binding(
            Path(environment["TMPDIR"]),
            directory=True,
            trusted_uid=trusted_uid,
        )
        held.append(scratch_fd)
        if scratch["mode"] != 0o700 or any(Path(environment["TMPDIR"]).iterdir()):
            raise ControllerRuntimeError("controller build scratch is not protected and empty")
        cwd, cwd_fd = _binding(cwd_path, directory=True, trusted_uid=trusted_uid)
        held.append(cwd_fd)
        if (
            cwd["path"] == scratch["path"]
            or cwd["mode"] != 0o700
            or any(cwd_path.iterdir())
        ):
            raise ControllerRuntimeError("controller build cwd is not distinct, protected, and empty")
        _source, source_fd = _binding(
            launcher_source,
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(source_fd)
        _compiler, compiler_fd = _binding(
            compiler_path,
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(compiler_fd)
        _tracer, tracer_fd = _binding(
            tracer_path,
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(tracer_fd)
        root_fd, _root_identity = _open_protected_root(Path(output_root), trusted_uid)
        output_fd = os.open(
            output_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
        output_created = True
        output_path = f"/proc/{os.getpid()}/fd/{output_fd}"
        _stdout, trace, _argv = _run_build_trace(
            tracer_fd=tracer_fd,
            compiler_fd=compiler_fd,
            source_fd=source_fd,
            output_path=output_path,
            binding_path=str(binding_path),
            environment=environment,
            cwd_fd=cwd_fd,
            extra_fds=(output_fd,),
        )
        excluded = {
            Path(output_root).resolve(),
            (Path(output_root) / output_name).resolve(),
            Path(output_path),
        }
        inputs, accesses = _parse_trace_accesses(
            trace,
            scratch=Path(environment["TMPDIR"]),
            excluded=excluded,
        )
        if any(Path(environment["TMPDIR"]).iterdir()):
            raise ControllerRuntimeError("controller build cwd retained compiler outputs")
        scratch_after = _observe_directory_phase(scratch, scratch_fd)
        _postcheck_binding(cwd, cwd_fd, directory=True)
        return {
            "inputs": inputs,
            "accesses": accesses,
            "trace_sha256": _digest(trace),
            "directories": {
                "cwd": cwd,
                "scratch_before": scratch,
                "scratch_after": scratch_after,
            },
        }
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        if root_fd >= 0 and output_created:
            try:
                os.unlink(output_name, dir_fd=root_fd)
                os.fsync(root_fd)
            except FileNotFoundError:
                pass
        if root_fd >= 0:
            os.close(root_fd)
        for fd in reversed(held):
            os.close(fd)


def issue_build_environment_manifest(
    *,
    compiler_path: Path,
    tracer_path: Path,
    discovery: Mapping[str, Any],
    environment: Mapping[str, str],
    cwd_path: Path,
    output_root: Path,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Pin the discovered compiler/subtool/header/static-link input closure."""
    held: list[int] = []
    root_fd = -1
    try:
        if (
            set(environment) != {"PATH", "LANG", "LC_ALL", "TMPDIR"}
            or environment["LANG"] != "C"
            or environment["LC_ALL"] != "C"
        ):
            raise ControllerRuntimeError("controller build environment is not exact")
        scratch, scratch_fd = _binding(
            Path(environment["TMPDIR"]),
            directory=True,
            trusted_uid=trusted_uid,
        )
        held.append(scratch_fd)
        if scratch["mode"] != 0o700 or any(Path(environment["TMPDIR"]).iterdir()):
            raise ControllerRuntimeError("controller build scratch is not protected and empty")
        cwd, cwd_fd = _binding(cwd_path, directory=True, trusted_uid=trusted_uid)
        held.append(cwd_fd)
        if (
            cwd["path"] == scratch["path"]
            or cwd["mode"] != 0o700
            or any(cwd_path.iterdir())
        ):
            raise ControllerRuntimeError("controller build cwd is not distinct, protected, and empty")
        compiler, fd = _binding(compiler_path, directory=False, trusted_uid=trusted_uid)
        held.append(fd)
        tracer, fd = _binding(tracer_path, directory=False, trusted_uid=trusted_uid)
        held.append(fd)
        discovered_inputs = discovery.get("inputs")
        discovered_accesses = discovery.get("accesses")
        discovered_directories = discovery.get("directories")
        if (
            not isinstance(discovered_inputs, list)
            or not isinstance(discovered_accesses, list)
            or discovered_accesses != sorted(set(discovered_accesses))
            or _SHA.fullmatch(str(discovery.get("trace_sha256"))) is None
            or not isinstance(discovered_directories, Mapping)
            or discovered_directories.get("cwd") != cwd
            or discovered_directories.get("scratch_after") != scratch
        ):
            raise ControllerRuntimeError("controller build discovery is invalid")
        inputs = []
        for path in sorted(set(map(Path, discovered_inputs)), key=str):
            item, fd = _binding(path, directory=False, trusted_uid=trusted_uid)
            held.append(fd)
            inputs.append(item)
        unsigned = {
            "schema": BUILD_ENVIRONMENT_SCHEMA,
            "compiler": compiler,
            "tracer": tracer,
            "cwd": cwd,
            "scratch": scratch,
            "inputs": inputs,
            "accesses": discovered_accesses,
            "discovery_trace_sha256": discovery["trace_sha256"],
            "discovery_directories": dict(discovered_directories),
            "environment": dict(environment),
            "closure_sha256": _digest(_canonical(inputs)),
        }
        receipt = {**unsigned, "receipt_sha256": _digest(_canonical(unsigned))}
        root_fd, _identity = _open_protected_root(Path(output_root), trusted_uid)
        name = "build-environment-" + receipt["receipt_sha256"].removeprefix("sha256:") + ".json"
        identity = _write_once(root_fd, name, _canonical(receipt), 0o400)
        _postcheck_binding(scratch, scratch_fd, directory=True)
        _postcheck_binding(cwd, cwd_fd, directory=True)
        return {
            **receipt,
            "path": str(Path(output_root) / name),
            "identity": list(identity),
        }
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        for fd in reversed(held):
            os.close(fd)


def produce_launcher_build(
    *,
    controller_source_receipt: Mapping[str, Any],
    build_environment_receipt: Mapping[str, Any],
    output_root: Path,
    binding_path: Path,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Compile and attest one launcher through the pinned W08 build closure."""
    held: list[int] = []
    postchecks: list[tuple[Mapping[str, Any], int, bool]] = []
    root_fd = -1
    output_name = ""
    created: list[str] = []
    try:
        source, source_receipt_fd = _read_json(
            controller_source_receipt,
            label="controller source receipt",
            trusted_uid=trusted_uid,
        )
        held.append(source_receipt_fd)
        postchecks.append((dict(controller_source_receipt), source_receipt_fd, False))
        build_environment, environment_fd = _read_json(
            build_environment_receipt,
            label="controller build environment receipt",
            trusted_uid=trusted_uid,
        )
        held.append(environment_fd)
        postchecks.append((dict(build_environment_receipt), environment_fd, False))
        source = _self_hashed(source, schema=SOURCE_SCHEMA, label="controller source receipt")
        build_environment = _self_hashed(
            build_environment,
            schema=BUILD_ENVIRONMENT_SCHEMA,
            label="controller build environment receipt",
        )
        build_environment = _validate_build_environment(build_environment)
        launcher_source = source.get("controller_launcher_source")
        if not isinstance(launcher_source, Mapping):
            raise ControllerRuntimeError("controller materialized launcher source is absent")
        identity = launcher_source.get("identity")
        if not isinstance(identity, list) or len(identity) != 7:
            raise ControllerRuntimeError("controller launcher source identity is invalid")
        source_binding = binding_from_identity(
            launcher_source["materialized_path"],
            launcher_source["sha256"],
            identity,
        )
        source_observed, source_fd = _binding(
            Path(source_binding["path"]),
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(source_fd)
        postchecks.append((source_observed, source_fd, False))
        if source_observed != source_binding:
            raise ControllerRuntimeError("controller launcher source changed before build")
        environment = build_environment.get("environment")
        if not isinstance(environment, Mapping) or set(environment) != {"PATH", "LANG", "LC_ALL", "TMPDIR"}:
            raise ControllerRuntimeError("controller build environment is invalid")
        scratch, scratch_fd = _binding(
            Path(environment["TMPDIR"]),
            directory=True,
            trusted_uid=trusted_uid,
        )
        held.append(scratch_fd)
        if scratch != build_environment.get("scratch"):
            raise ControllerRuntimeError("controller build scratch differs from its receipt")
        if scratch["mode"] != 0o700 or any(Path(environment["TMPDIR"]).iterdir()):
            raise ControllerRuntimeError("controller build scratch is not protected and empty")
        cwd_binding = build_environment["cwd"]
        cwd, cwd_fd = _binding(
            Path(cwd_binding["path"]),
            directory=True,
            trusted_uid=trusted_uid,
        )
        held.append(cwd_fd)
        postchecks.append((cwd, cwd_fd, True))
        if cwd != cwd_binding or cwd["path"] == scratch["path"] or any(
            Path(cwd["path"]).iterdir()
        ):
            raise ControllerRuntimeError("controller build cwd differs from its receipt")
        compiler_binding = build_environment["compiler"]
        tracer_binding = build_environment["tracer"]
        compiler, compiler_fd = _binding(
            Path(compiler_binding["path"]),
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(compiler_fd)
        postchecks.append((compiler, compiler_fd, False))
        tracer, tracer_fd = _binding(
            Path(tracer_binding["path"]),
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(tracer_fd)
        postchecks.append((tracer, tracer_fd, False))
        if compiler != compiler_binding or tracer != tracer_binding:
            raise ControllerRuntimeError("controller build tools changed")
        admitted_paths = {
            Path(compiler["path"]).resolve(),
            Path(tracer["path"]).resolve(),
            Path(source_binding["path"]).resolve(),
        }
        for item in build_environment.get("inputs", []):
            observed, fd = _binding(
                Path(item["path"]),
                directory=False,
                trusted_uid=trusted_uid,
            )
            held.append(fd)
            postchecks.append((observed, fd, False))
            if observed != item:
                raise ControllerRuntimeError("controller build input changed")
            admitted_paths.add(Path(item["path"]).resolve())
        root_fd, _root_identity = _open_protected_root(Path(output_root), trusted_uid)
        output_name = (
            "launcher-"
            + launcher_source["sha256"].removeprefix("sha256:")
            + "-"
            + hashlib.sha256(str(binding_path).encode()).hexdigest()
        )
        output_fd = os.open(
            output_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
        created.append(output_name)
        held.append(output_fd)
        output_proc = f"/proc/{os.getpid()}/fd/{output_fd}"
        _stdout, trace, executed_argv = _run_build_trace(
            tracer_fd=tracer_fd,
            compiler_fd=compiler_fd,
            source_fd=source_fd,
            output_path=output_proc,
            binding_path=str(binding_path),
            environment=environment,
            cwd_fd=cwd_fd,
            extra_fds=(output_fd,),
        )
        output_path = Path(output_root) / output_name
        accessed_paths, accesses = _parse_trace_accesses(
            trace,
            scratch=Path(environment["TMPDIR"]),
            excluded={Path(output_root), output_path, Path(output_proc)},
        )
        unexpected = set(accessed_paths) - admitted_paths
        if unexpected:
            raise ControllerRuntimeError(
                "controller build accessed an unadmitted file set: "
                + _digest(_canonical(sorted(map(str, unexpected))))
            )
        if accesses != build_environment["accesses"]:
            raise ControllerRuntimeError(
                "controller build file-access trace differs from discovery: "
                + json.dumps(
                    {
                        "added": sorted(set(accesses) - set(build_environment["accesses"])),
                        "removed": sorted(set(build_environment["accesses"]) - set(accesses)),
                    },
                    sort_keys=True,
                )
            )
        if any(Path(environment["TMPDIR"]).iterdir()):
            raise ControllerRuntimeError("controller compiler left unbound scratch outputs")
        scratch_after = _observe_directory_phase(scratch, scratch_fd)
        os.fchmod(output_fd, 0o500)
        os.fsync(output_fd)
        launcher_raw = os.pread(output_fd, 4 * 1024 * 1024 + 1, 0)
        output_metadata = os.fstat(output_fd)
        named = os.stat(output_name, dir_fd=root_fd, follow_symlinks=False)
        launcher_elf = _elf_closure(launcher_raw)
        if (
            len(launcher_raw) > 4 * 1024 * 1024
            or launcher_elf is None
            or launcher_elf["pt_interp"] is not None
            or launcher_elf["needed"]
            or (output_metadata.st_dev, output_metadata.st_ino)
            != (named.st_dev, named.st_ino)
        ):
            raise ControllerRuntimeError("built controller launcher is not exact static ELF")
        from tgw.a3_preintegration_observation import _run_held_bounded

        version_rc, version_stdout, version_stderr = _run_held_bounded(
            [f"/proc/self/fd/{compiler_fd}", "--version"],
            pass_fds=(compiler_fd, cwd_fd),
            timeout=10,
            limit=64 * 1024,
            env=environment,
            cwd=f"/proc/{os.getpid()}/fd/{cwd_fd}",
        )
        if version_rc != 0:
            raise ControllerRuntimeError("controller compiler version probe failed")
        launcher_binding = {
            "path": str(output_path),
            "sha256": _digest(launcher_raw),
            "dev": output_metadata.st_dev,
            "ino": output_metadata.st_ino,
            "uid": output_metadata.st_uid,
            "gid": output_metadata.st_gid,
            "mode": stat.S_IMODE(output_metadata.st_mode),
            "nlink": output_metadata.st_nlink,
            "size": output_metadata.st_size,
        }
        semantic_command = [
            "cc",
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-x",
            "c",
        ]
        trace_name = output_name + ".trace"
        trace_identity = _write_once(root_fd, trace_name, trace, 0o400)
        created.append(trace_name)
        unsigned = {
            "schema": BUILD_SCHEMA,
            "controller_source_receipt_sha256": source["receipt_sha256"],
            "application_candidate": source["application_candidate"],
            "source_sha256": launcher_source["sha256"],
            "build_contract": launcher_source["build_contract"],
            "compiler": {
                "path": compiler["path"],
                "sha256": compiler["sha256"],
                "version": (version_stdout + version_stderr).decode("utf-8", errors="strict"),
                "version_sha256": _digest(version_stdout + version_stderr),
                "closure_sha256": build_environment["closure_sha256"],
            },
            "command": semantic_command,
            "executed_argv": executed_argv,
            "executed_argv_sha256": _digest(_canonical(executed_argv)),
            "binding_path": str(binding_path),
            "build_directories": {
                "cwd": cwd,
                "scratch_before": scratch,
                "scratch_after": scratch_after,
            },
            "build_environment": dict(build_environment_receipt),
            "build_environment_receipt_sha256": build_environment["receipt_sha256"],
            "trace": {
                "path": str(Path(output_root) / trace_name),
                "sha256": _digest(trace),
                "identity": list(trace_identity),
            },
            "launcher": {
                **launcher_binding,
                "elf": {
                    **launcher_elf,
                    "pt_interp_resolved": None,
                    "resolved": [],
                },
            },
        }
        receipt = {**unsigned, "receipt_sha256": _digest(_canonical(unsigned))}
        receipt_name = output_name + ".build.json"
        receipt_identity = _write_once(root_fd, receipt_name, _canonical(receipt), 0o400)
        created.append(receipt_name)
        os.fsync(root_fd)
        for expected, fd, directory in postchecks:
            _postcheck_binding(expected, fd, directory=directory)
        _postcheck_binding(scratch_after, scratch_fd, directory=True)
        named_root = os.stat(output_root, follow_symlinks=False)
        held_root = os.fstat(root_fd)
        if (named_root.st_dev, named_root.st_ino) != (held_root.st_dev, held_root.st_ino):
            raise ControllerRuntimeError("controller build output root changed")
        return {
            **receipt,
            "receipt_path": str(Path(output_root) / receipt_name),
            "receipt_identity": list(receipt_identity),
        }
    except Exception:
        if root_fd >= 0:
            for name in reversed(created):
                try:
                    os.unlink(name, dir_fd=root_fd)
                except FileNotFoundError:
                    pass
            os.fsync(root_fd)
        raise
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        for fd in reversed(held):
            os.close(fd)


def _binding(path: Path, *, directory: bool, trusted_uid: int) -> tuple[dict[str, Any], int]:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ControllerRuntimeError("controller runtime artifact path is not absolute")
    for ancestor in (candidate.parent, *candidate.parents):
        metadata = ancestor.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, trusted_uid}
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ControllerRuntimeError("controller runtime ancestor is mutable")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    fd = os.open(candidate, flags)
    try:
        held = os.fstat(fd)
        named = os.stat(candidate, follow_symlinks=False)
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if (
            not expected_type(held.st_mode)
            or held.st_uid not in {0, trusted_uid}
            or stat.S_IMODE(held.st_mode) & 0o022
            or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ControllerRuntimeError("controller runtime artifact is not protected")
        common = {
            "path": str(candidate),
            "dev": held.st_dev,
            "ino": held.st_ino,
            "uid": held.st_uid,
            "gid": held.st_gid,
            "mode": stat.S_IMODE(held.st_mode),
            "nlink": held.st_nlink,
            "size": held.st_size,
        }
        if directory:
            common.update(
                {
                    "sha256": _tree_digest(
                        Path(f"/proc/self/fd/{fd}"),
                        trusted_uid=held.st_uid,
                        trusted_gid=held.st_gid,
                    ),
                    "mtime_ns": held.st_mtime_ns,
                    "ctime_ns": held.st_ctime_ns,
                }
            )
        else:
            raw = os.pread(fd, 64 * 1024 * 1024 + 1, 0)
            if len(raw) > 64 * 1024 * 1024:
                raise ControllerRuntimeError("controller runtime file exceeds its bound")
            common["sha256"] = _digest(raw)
        return common, fd
    except Exception:
        os.close(fd)
        raise


def _read_json(binding: Mapping[str, Any], *, label: str, trusted_uid: int) -> tuple[dict[str, Any], int]:
    observed, fd = _binding(Path(str(binding.get("path", ""))), directory=False, trusted_uid=trusted_uid)
    try:
        if any(observed.get(name) != binding.get(name) for name in observed):
            raise ControllerRuntimeError(f"{label} differs from its protected binding")
        raw = os.pread(fd, 4 * 1024 * 1024 + 1, 0)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ControllerRuntimeError(f"{label} is not an object")
        return value, fd
    except Exception:
        os.close(fd)
        raise


def _self_hashed(value: Mapping[str, Any], *, schema: str, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ControllerRuntimeError(f"{label} is invalid")
    result = dict(value)
    claimed = result.pop("receipt_sha256", None)
    if value.get("schema") != schema or claimed != _digest(_canonical(result)):
        raise ControllerRuntimeError(f"{label} schema/hash is invalid")
    return dict(value)


def _resolved_elf(
    files: list[dict[str, Any]],
    raw_by_path: Mapping[str, bytes],
) -> None:
    by_name: dict[str, list[str]] = {}
    paths = {item["path"] for item in files}
    for path in paths:
        metadata = _elf_closure(raw_by_path[path])
        name = metadata["soname"] if metadata is not None and metadata["soname"] else Path(path).name
        by_name.setdefault(name, []).append(path)
    for item in files:
        raw = raw_by_path[item["path"]]
        elf = _elf_closure(raw)
        if elf is None:
            item["elf"] = None
            continue
        interpreter_resolved = (
            str(Path(elf["pt_interp"]).resolve())
            if elf["pt_interp"] is not None
            else None
        )
        if interpreter_resolved is not None and interpreter_resolved not in paths:
            raise ControllerRuntimeError("controller ELF interpreter is outside exact native files")
        resolved = []
        for soname in elf["needed"]:
            candidates = sorted(by_name.get(soname, []))
            if len(candidates) != 1:
                raise ControllerRuntimeError("controller ELF dependency does not resolve uniquely")
            resolved.append({"soname": soname, "path": candidates[0]})
        item["elf"] = {
            **elf,
            "pt_interp_resolved": interpreter_resolved,
            "resolved": resolved,
        }


def materialize_controller_runtime(
    *,
    controller_source_receipt: Mapping[str, Any],
    launcher_build_receipt: Mapping[str, Any],
    python_path: Path,
    native_files: Sequence[Path],
    runtime_trees: Sequence[Path],
    import_roots: Sequence[Path],
    python_home: Path,
    output_root: Path,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Create one exact, all-or-cleanup controller runtime closure."""
    held: list[int] = []
    root_fd = -1
    created: list[str] = []
    try:
        source, source_fd = _read_json(
            controller_source_receipt,
            label="controller source receipt",
            trusted_uid=trusted_uid,
        )
        held.append(source_fd)
        build, build_fd = _read_json(
            launcher_build_receipt,
            label="controller launcher build receipt",
            trusted_uid=trusted_uid,
        )
        held.append(build_fd)
        source = _self_hashed(source, schema=SOURCE_SCHEMA, label="controller source receipt")
        build = _self_hashed(build, schema=BUILD_SCHEMA, label="controller launcher build receipt")
        launcher_source = source.get("controller_launcher_source")
        launcher = build.get("launcher")
        compiler = build.get("compiler")
        build_environment_binding = build.get("build_environment")
        build_directories = build.get("build_directories")
        trace_binding = build.get("trace")
        executed_argv = build.get("executed_argv")
        expected_argv_tail = [
            "-f",
            "-qq",
            "-s",
            "4096",
            "-e",
            "trace=%file",
            "--",
        ]
        if (
            not isinstance(launcher_source, Mapping)
            or not isinstance(launcher, Mapping)
            or not isinstance(compiler, Mapping)
            or not isinstance(build_environment_binding, Mapping)
            or not isinstance(build_directories, Mapping)
            or set(build_directories) != {"cwd", "scratch_before", "scratch_after"}
            or not isinstance(trace_binding, Mapping)
            or not isinstance(executed_argv, list)
            or build.get("controller_source_receipt_sha256") != source.get("receipt_sha256")
            or build.get("application_candidate") != source.get("application_candidate")
            or build.get("source_sha256") != launcher_source.get("sha256")
            or build.get("build_contract") != launcher_source.get("build_contract")
            or set(compiler) != {"path", "sha256", "version", "version_sha256", "closure_sha256"}
            or not isinstance(compiler.get("version"), str)
            or not compiler["version"]
            or compiler.get("version_sha256") != _digest(compiler["version"].encode())
            or any(_SHA.fullmatch(str(compiler[name])) is None for name in ("sha256", "version_sha256", "closure_sha256"))
            or build.get("command")
            != ["cc", "-static", "-O2", "-Wall", "-Wextra", "-Werror", "-x", "c"]
            or build.get("executed_argv_sha256") != _digest(_canonical(executed_argv))
            or len(executed_argv) != 20
            or re.fullmatch(r"/proc/self/fd/[0-9]+", str(executed_argv[0])) is None
            or executed_argv[1:8] != expected_argv_tail
            or re.fullmatch(r"/proc/self/fd/[0-9]+", str(executed_argv[8])) is None
            or executed_argv[9:14] != ["-static", "-O2", "-Wall", "-Wextra", "-Werror"]
            or executed_argv[14] != f'-DBINDING_PATH="{build.get("binding_path")}"'
            or executed_argv[15] != "-o"
            or re.fullmatch(r"/proc/[0-9]+/fd/[0-9]+", str(executed_argv[16])) is None
            or executed_argv[17:19] != ["-x", "c"]
            or re.fullmatch(r"/proc/[0-9]+/fd/[0-9]+", str(executed_argv[19])) is None
            or not isinstance(build.get("binding_path"), str)
            or not Path(build["binding_path"]).is_absolute()
        ):
            raise ControllerRuntimeError("controller launcher build evidence is underbound")
        environment_receipt, environment_receipt_fd = _read_json(
            build_environment_binding,
            label="controller build environment receipt",
            trusted_uid=trusted_uid,
        )
        held.append(environment_receipt_fd)
        environment_receipt = _self_hashed(
            environment_receipt,
            schema=BUILD_ENVIRONMENT_SCHEMA,
            label="controller build environment receipt",
        )
        environment_receipt = _validate_build_environment(environment_receipt)
        if (
            environment_receipt["receipt_sha256"]
            != build["build_environment_receipt_sha256"]
            or environment_receipt["closure_sha256"] != compiler["closure_sha256"]
            or environment_receipt["compiler"]["path"] != compiler["path"]
            or environment_receipt["compiler"]["sha256"] != compiler["sha256"]
            or build_directories["cwd"] != environment_receipt["cwd"]
            or build_directories["scratch_before"] != environment_receipt["scratch"]
            or any(
                build_directories["scratch_before"][name]
                != build_directories["scratch_after"][name]
                for name in ("path", "dev", "ino", "uid", "gid", "mode", "nlink", "sha256")
            )
        ):
            raise ControllerRuntimeError("controller build environment cross-binding differs")
        trace_identity = trace_binding.get("identity")
        if not isinstance(trace_identity, list):
            raise ControllerRuntimeError("controller compiler trace binding is absent")
        trace_expected = binding_from_identity(
            str(trace_binding.get("path", "")),
            str(trace_binding.get("sha256", "")),
            trace_identity,
        )
        trace_observed, trace_fd = _binding(
            Path(trace_expected["path"]),
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(trace_fd)
        if trace_observed != trace_expected:
            raise ControllerRuntimeError("controller compiler trace changed")
        launcher_binding, launcher_fd = _binding(
            Path(str(launcher.get("path", ""))),
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(launcher_fd)
        launcher_raw = os.pread(launcher_fd, launcher_binding["size"] + 1, 0)
        launcher_elf = _elf_closure(launcher_raw)
        if (
            any(launcher_binding.get(name) != launcher.get(name) for name in launcher_binding)
            or launcher_elf is None
            or launcher_elf["pt_interp"] is not None
            or launcher_elf["needed"]
            or launcher.get("elf")
            != {**launcher_elf, "pt_interp_resolved": None, "resolved": []}
        ):
            raise ControllerRuntimeError("controller launcher is not the exact static build")
        source_bundle = source.get("controller_bundle")
        if not isinstance(source_bundle, Mapping):
            raise ControllerRuntimeError("controller source bundle binding is absent")
        bundle_binding, bundle_fd = _binding(
            Path(str(source_bundle.get("path", ""))),
            directory=False,
            trusted_uid=trusted_uid,
        )
        held.append(bundle_fd)
        if bundle_binding["sha256"] != source_bundle.get("sha256"):
            raise ControllerRuntimeError("controller bundle differs from source receipt")

        paths = sorted(set([Path(python_path), *map(Path, native_files)]), key=str)
        if any(
            path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts
            for path in paths
        ):
            raise ControllerRuntimeError("controller native file closure contains bytecode")
        file_bindings: list[dict[str, Any]] = []
        raw_by_path: dict[str, bytes] = {}
        for path in paths:
            binding, fd = _binding(path, directory=False, trusted_uid=trusted_uid)
            held.append(fd)
            file_bindings.append(binding)
            raw_by_path[str(path)] = os.pread(fd, binding["size"] + 1, 0)
        python_resolved = str(Path(python_path))
        if python_resolved not in raw_by_path:
            raise ControllerRuntimeError("controller Python is outside native closure")
        _resolved_elf(file_bindings, raw_by_path)

        tree_bindings: list[dict[str, Any]] = []
        for path in sorted(set(map(Path, runtime_trees)), key=str):
            binding, fd = _binding(path, directory=True, trusted_uid=trusted_uid)
            held.append(fd)
            tree_bindings.append(binding)
        tree_paths = [item["path"] for item in tree_bindings]
        python_home_path = str(Path(python_home))
        if python_home_path not in tree_paths:
            raise ControllerRuntimeError("controller Python home is outside runtime trees")
        stdlib_roots = [
            item
            for item in (Path(python_home) / "lib").iterdir()
            if item.is_dir() and re.fullmatch(r"python[0-9]+\.[0-9]+", item.name)
        ]
        if len(stdlib_roots) != 1:
            raise ControllerRuntimeError("controller Python home layout is not exact")
        admitted_imports = sorted(set(map(str, import_roots)))
        if any(
            path not in tree_paths
            or Path(path).suffix in {".pyc", ".pyo"}
            or "__pycache__" in Path(path).parts
            for path in admitted_imports
        ):
            raise ControllerRuntimeError("controller import root is outside runtime trees")

        unsigned_manifest = {
            "schema": RUNTIME_SCHEMA,
            "files": file_bindings,
            "trees": tree_bindings,
            "import_roots": admitted_imports,
            "python_home": python_home_path,
        }
        manifest = {
            **unsigned_manifest,
            "manifest_sha256": _digest(_canonical(unsigned_manifest)),
        }
        manifest_raw = _canonical(manifest)
        closure_raw = _preexec_closure(file_bindings, tree_bindings)
        root_fd, _root_identity = _open_protected_root(Path(output_root), trusted_uid)
        stem = "runtime-" + manifest["manifest_sha256"].removeprefix("sha256:")
        manifest_name = f"{stem}.json"
        closure_name = f"{stem}.closure"
        config_name = f"{stem}.fds"
        receipt_name = f"{stem}.receipt.json"
        if build["binding_path"] != str(Path(output_root) / config_name):
            raise ControllerRuntimeError("controller launcher targets a different runtime config")
        manifest_identity = _write_once(root_fd, manifest_name, manifest_raw, 0o400)
        created.append(manifest_name)
        closure_identity = _write_once(root_fd, closure_name, closure_raw, 0o400)
        created.append(closure_name)
        config_raw = (
            "schema=tgw-w09-controller-launch-fds/v1\n"
            f"python={python_resolved}\n"
            f"python_home={python_home_path}\n"
            f"bundle={source_bundle['path']}\n"
            f"closure={Path(output_root) / closure_name}\n"
            f"receipt={Path(output_root) / receipt_name}\n"
        ).encode()
        config_identity = _write_once(root_fd, config_name, config_raw, 0o400)
        created.append(config_name)
        unsigned_receipt = {
            "schema": SCHEMA,
            "controller_source_receipt_sha256": source["receipt_sha256"],
            "application_candidate": source["application_candidate"],
            "launcher_build_receipt_sha256": build["receipt_sha256"],
            "launcher": launcher_binding,
            "python": {
                name: value
                for name, value in next(
                    item for item in file_bindings if item["path"] == python_resolved
                ).items()
                if name != "elf"
            },
            "bundle": bundle_binding,
            "manifest": {
                **manifest,
                "path": str(Path(output_root) / manifest_name),
                "content_sha256": _digest(manifest_raw),
                "identity": list(manifest_identity),
            },
            "closure": {"path": str(Path(output_root) / closure_name), "sha256": _digest(closure_raw), "identity": list(closure_identity)},
            "launcher_config": {"path": str(Path(output_root) / config_name), "sha256": _digest(config_raw), "identity": list(config_identity)},
        }
        receipt = {
            **unsigned_receipt,
            "receipt_sha256": _digest(_canonical(unsigned_receipt)),
        }
        _write_once(root_fd, receipt_name, _canonical(receipt), 0o400)
        created.append(receipt_name)
        for fd in held:
            os.fstat(fd)
        return {**receipt, "receipt_path": str(Path(output_root) / receipt_name)}
    except Exception:
        cleanup_error = None
        if root_fd >= 0:
            for name in reversed(created):
                try:
                    os.unlink(name, dir_fd=root_fd)
                except OSError as exc:
                    cleanup_error = exc
            try:
                os.fsync(root_fd)
            except OSError as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise ControllerRuntimeError("controller runtime cleanup is ambiguous") from cleanup_error
        raise
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        for fd in reversed(held):
            os.close(fd)


def binding_from_identity(path: str, sha256_value: str, identity: Sequence[int]) -> dict[str, Any]:
    """Convert one materialized-file identity into the entrypoint binding."""

    if len(identity) != 7 or _SHA.fullmatch(sha256_value) is None:
        raise ControllerBundleError("controller materialized identity is invalid")
    return {
        "path": path,
        "sha256": sha256_value,
        "dev": identity[0],
        "ino": identity[1],
        "uid": identity[2],
        "gid": identity[3],
        "mode": stat.S_IMODE(identity[4]),
        "nlink": identity[5],
        "size": identity[6],
    }

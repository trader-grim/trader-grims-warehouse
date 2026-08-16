"""Fixed, no-argument W09 controller composition and execution entrypoint."""

from __future__ import annotations

import json
import mmap
import os
import re
import stat
import struct
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
        "controller_source",
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


def _protected_ancestors(path: Path, trusted_uid: int = 0) -> None:
    for ancestor in (path.parent, *path.parents):
        metadata = ancestor.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, trusted_uid} or stat.S_IMODE(metadata.st_mode) & 0o022:
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
        "dev",
        "ino",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
    }:
        raise ValueError(f"{label} binding is invalid")
    path = Path(str(binding["path"]))
    if not path.is_absolute() or _SHA256.fullmatch(str(binding["sha256"])) is None:
        raise ValueError(f"{label} path or digest is invalid")
    _protected_ancestors(path, int(binding["uid"]))
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
            held.st_nlink,
            held.st_size,
        )
        if (
            len(raw) > max_bytes
            or not stat.S_ISREG(held.st_mode)
            or held.st_dev != binding["dev"]
            or held.st_ino != binding["ino"]
            or held.st_uid != binding["uid"]
            or held.st_gid != binding["gid"]
            or stat.S_IMODE(held.st_mode) != binding["mode"]
            or held.st_nlink != binding["nlink"]
            or held.st_size != binding["size"]
            or "sha256:" + sha256(raw).hexdigest() != binding["sha256"]
            or identity
            != (
                named.st_dev,
                named.st_ino,
                named.st_uid,
                named.st_gid,
                named.st_mode,
                named.st_nlink,
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
        held.st_nlink,
        held.st_size,
    )
    named_identity = (
        named.st_dev,
        named.st_ino,
        named.st_uid,
        named.st_gid,
        named.st_mode,
        named.st_nlink,
        named.st_size,
    )
    if held_identity != identity or named_identity != identity or os.pread(fd, len(raw) + 1, 0) != raw:
        raise OSError(f"controller runtime artifact changed: {path}")


def _elf_closure(raw: bytes) -> dict[str, Any] | None:
    """Extract PT_INTERP and DT_NEEDED from ELF64 little-endian bytes."""

    if not raw.startswith(b"\x7fELF"):
        return None
    if len(raw) < 64 or raw[4:6] != b"\x02\x01":
        raise ValueError("controller runtime contains an unsupported ELF image")
    try:
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", raw, 0)
        program_offset, section_offset = header[5], header[6]
        program_size, program_count = header[9], header[10]
        section_size, section_count = header[11], header[12]
        interpreter = None
        for index in range(program_count):
            values = struct.unpack_from("<IIQQQQQQ", raw, program_offset + index * program_size)
            if values[0] == 3:
                interpreter = raw[values[2] : values[2] + values[5]].rstrip(b"\0").decode()
        sections = [struct.unpack_from("<IIQQQQIIQQ", raw, section_offset + index * section_size) for index in range(section_count)]
        needed = []
        rpath = []
        runpath = []
        soname = None
        for section in sections:
            if section[1] != 6:
                continue
            strings = sections[section[6]]
            table = raw[strings[4] : strings[4] + strings[5]]
            for offset in range(section[4], section[4] + section[5], section[9] or 16):
                tag, value = struct.unpack_from("<qQ", raw, offset)
                if tag == 0:
                    break
                if tag == 1:
                    end = table.find(b"\0", value)
                    needed.append(table[value:end].decode())
                if tag in {15, 29}:
                    end = table.find(b"\0", value)
                    paths = table[value:end].decode().split(":")
                    (rpath if tag == 15 else runpath).extend(paths)
                if tag == 14:
                    end = table.find(b"\0", value)
                    soname = table[value:end].decode()
    except (IndexError, UnicodeDecodeError, struct.error, ValueError) as exc:
        raise ValueError("controller runtime ELF metadata is malformed") from exc
    return {
        "pt_interp": interpreter,
        "needed": sorted(needed),
        "rpath": sorted(set(rpath)),
        "runpath": sorted(set(runpath)),
        "soname": soname,
    }


def _tree_digest(root: Path, *, trusted_uid: int, trusted_gid: int) -> str:
    digest = sha256()
    count = 0
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        count += 1
        if count > 100_000:
            raise ValueError("controller runtime tree exceeds its entry bound")
        relative = path.relative_to(root).as_posix()
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.relative_to(root).parts:
            raise ValueError("controller runtime tree contains bytecode")
        item = os.lstat(path)
        if item.st_uid != trusted_uid or item.st_gid != trusted_gid or (not stat.S_ISLNK(item.st_mode) and item.st_mode & 0o022):
            raise ValueError("controller runtime tree content is not protected")
        digest.update(
            _canonical(
                [
                    relative,
                    stat.S_IFMT(item.st_mode),
                    stat.S_IMODE(item.st_mode),
                    item.st_nlink,
                    item.st_size,
                ]
            )
        )
        if stat.S_ISREG(item.st_mode):
            total += item.st_size
            if total > 2 * 1024 * 1024 * 1024:
                raise ValueError("controller runtime tree exceeds its byte bound")
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                held = os.fstat(fd)
                if (held.st_dev, held.st_ino, held.st_size) != (
                    item.st_dev,
                    item.st_ino,
                    item.st_size,
                ):
                    raise OSError("controller runtime tree file changed")
                content = sha256()
                offset = 0
                while True:
                    chunk = os.pread(fd, 1024 * 1024, offset)
                    if not chunk:
                        break
                    content.update(chunk)
                    offset += len(chunk)
                digest.update(content.digest())
            finally:
                os.close(fd)
        elif stat.S_ISLNK(item.st_mode):
            raise ValueError("controller runtime tree contains a symlink")
        elif not stat.S_ISDIR(item.st_mode):
            raise ValueError("controller runtime tree contains a special file")
    return "sha256:" + digest.hexdigest()


def _hold_runtime_tree(
    binding: Mapping[str, Any],
    *,
    inherited_path: Path | None = None,
) -> tuple[Path, int, str, tuple[int, ...], int, int]:
    fields = {
        "path",
        "sha256",
        "dev",
        "ino",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
        "mtime_ns",
        "ctime_ns",
    }
    if not isinstance(binding, Mapping) or set(binding) != fields:
        raise ValueError("controller runtime tree binding is invalid")
    path = Path(str(binding["path"]))
    if not path.is_absolute() or _SHA256.fullmatch(str(binding["sha256"])) is None:
        raise ValueError("controller runtime tree path/hash is invalid")
    _protected_ancestors(path, int(binding["uid"]))
    opened = inherited_path or path
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if inherited_path is None:
        flags |= os.O_NOFOLLOW
    fd = os.open(opened, flags)
    try:
        held = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        identity = (
            held.st_dev,
            held.st_ino,
            held.st_uid,
            held.st_gid,
            held.st_mode,
            held.st_nlink,
            held.st_size,
            held.st_mtime_ns,
            held.st_ctime_ns,
        )
        expected = (
            binding["dev"],
            binding["ino"],
            binding["uid"],
            binding["gid"],
            stat.S_IFDIR | binding["mode"],
            binding["nlink"],
            binding["size"],
            binding["mtime_ns"],
            binding["ctime_ns"],
        )
        named_identity = (
            named.st_dev,
            named.st_ino,
            named.st_uid,
            named.st_gid,
            named.st_mode,
            named.st_nlink,
            named.st_size,
            named.st_mtime_ns,
            named.st_ctime_ns,
        )
        if identity != expected or named_identity != expected:
            raise ValueError("controller runtime tree identity differs")
        observed = _tree_digest(
            Path(f"/proc/self/fd/{fd}"),
            trusted_uid=held.st_uid,
            trusted_gid=held.st_gid,
        )
        if observed != binding["sha256"]:
            raise ValueError("controller runtime tree content differs")
        return path, fd, observed, identity, held.st_uid, held.st_gid
    except Exception:
        os.close(fd)
        raise


def _revalidate_runtime_tree(
    tree: tuple[Path, int, str, tuple[int, ...], int, int],
) -> None:
    path, fd, expected_hash, identity, uid, gid = tree
    held = os.fstat(fd)
    named = os.stat(path, follow_symlinks=False)
    observed = (
        held.st_dev,
        held.st_ino,
        held.st_uid,
        held.st_gid,
        held.st_mode,
        held.st_nlink,
        held.st_size,
        held.st_mtime_ns,
        held.st_ctime_ns,
    )
    named_identity = (
        named.st_dev,
        named.st_ino,
        named.st_uid,
        named.st_gid,
        named.st_mode,
        named.st_nlink,
        named.st_size,
        named.st_mtime_ns,
        named.st_ctime_ns,
    )
    if observed != identity or named_identity != identity or _tree_digest(Path(f"/proc/self/fd/{fd}"), trusted_uid=uid, trusted_gid=gid) != expected_hash:
        raise OSError(f"controller runtime tree changed: {path}")


def _preexec_closure(
    files: list[Mapping[str, Any]],
    trees: list[Mapping[str, Any]] | None = None,
) -> bytes:
    lines = ["schema=tgw-w09-controller-preexec-closure/v1"]
    for binding in files:
        path = str(binding.get("path", ""))
        if not path.startswith("/") or ":" in path or "\n" in path:
            raise ValueError("controller pre-exec closure path is invalid")
        mode = stat.S_IFREG | int(binding["mode"])
        fields = (
            binding["dev"],
            binding["ino"],
            binding["uid"],
            binding["gid"],
            mode,
            binding["nlink"],
            binding["size"],
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in fields):
            raise ValueError("controller pre-exec closure identity is invalid")
        lines.append("file=" + ":".join([*(str(value) for value in fields), path]))
    for binding in trees or []:
        path = str(binding.get("path", ""))
        if not path.startswith("/") or ":" in path or "\n" in path:
            raise ValueError("controller pre-exec tree path is invalid")
        fields = (
            binding["dev"],
            binding["ino"],
            binding["uid"],
            binding["gid"],
            stat.S_IFDIR | int(binding["mode"]),
            binding["nlink"],
            binding["size"],
        )
        lines.append("tree=" + ":".join([*(str(value) for value in fields), path]))
    return ("\n".join(lines) + "\n").encode()


def _hold_controller_runtime(
    value: Any,
    *,
    require_launcher: bool,
) -> tuple[
    dict[str, Any],
    list[tuple[Path, int, bytes, tuple[int, ...]]],
    list[tuple[Path, int, str, tuple[int, ...], int, int]],
    str,
]:
    if not isinstance(value, Mapping) or set(value) != {
        "launcher",
        "python",
        "bundle",
        "launcher_config",
        "closure",
        "manifest",
        "receipt",
    }:
        raise ValueError("controller runtime binding is invalid")
    artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]] = []
    trees: list[tuple[Path, int, str, tuple[int, ...], int, int]] = []
    try:
        named = {}
        for name in (
            "launcher",
            "python",
            "bundle",
            "launcher_config",
            "closure",
            "manifest",
            "receipt",
        ):
            artifact = _hold_runtime_artifact(
                value[name],
                label=f"controller {name}",
            )
            artifacts.append(artifact)
            named[name] = artifact
        if len(sys.argv) != 1 or not sys.argv[0].startswith("/proc/self/fd/"):
            raise ValueError("controller bundle was not executed from its inherited descriptor")
        inherited = {
            "bundle": sys.argv[0],
        }
        if require_launcher:
            environment_names = {
                "launcher": "TGW_W09_LAUNCHER_FD",
                "python": "TGW_W09_PYTHON_FD",
                "bundle": "TGW_W09_BUNDLE_FD",
                "launcher_config": "TGW_W09_LAUNCH_BINDING_FD",
                "closure": "TGW_W09_CLOSURE_FD",
                "receipt": "TGW_W09_RUNTIME_RECEIPT_FD",
            }
            inherited = {}
            for name, environment_name in environment_names.items():
                raw_fd = os.environ.get(environment_name, "")
                if not raw_fd.isascii() or not raw_fd.isdecimal() or int(raw_fd) < 3:
                    raise ValueError("controller launcher descriptor handoff is invalid")
                inherited[name] = f"/proc/self/fd/{int(raw_fd)}"
            if inherited["bundle"] != sys.argv[0]:
                raise ValueError("controller bundle argument differs from launcher handoff")
        for name, proc_path in inherited.items():
            inherited_fd = os.open(proc_path, os.O_RDONLY | os.O_CLOEXEC)
            try:
                metadata = os.fstat(inherited_fd)
                raw = os.pread(inherited_fd, len(named[name][2]) + 1, 0)
                identity = (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_uid,
                    metadata.st_gid,
                    metadata.st_mode,
                    metadata.st_nlink,
                    metadata.st_size,
                )
                if raw != named[name][2] or identity != named[name][3]:
                    raise ValueError(f"executed controller {name} differs from its binding")
                artifacts.append((named[name][0], inherited_fd, raw, identity))
            except Exception:
                os.close(inherited_fd)
                raise
        manifest = json.loads(named["manifest"][2])
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema",
            "files",
            "trees",
            "import_roots",
            "python_home",
            "manifest_sha256",
        }:
            raise ValueError("controller runtime manifest is invalid")
        unsigned = dict(manifest)
        claimed = unsigned.pop("manifest_sha256")
        if (
            manifest["schema"] != RUNTIME_SCHEMA
            or claimed != "sha256:" + sha256(_canonical(unsigned)).hexdigest()
            or not isinstance(manifest["files"], list)
            or not manifest["files"]
            or not isinstance(manifest["trees"], list)
            or not manifest["trees"]
        ):
            raise ValueError("controller runtime manifest hash/schema is invalid")
        paths = [item.get("path") if isinstance(item, Mapping) else None for item in manifest["files"]]
        if any(not isinstance(path, str) or Path(path).suffix in {".pyc", ".pyo"} or "__pycache__" in Path(path).parts for path in paths):
            raise ValueError("controller runtime file set is invalid")
        if paths != sorted(set(paths)):
            raise ValueError("controller runtime file set is invalid")
        tree_paths = [item.get("path") if isinstance(item, Mapping) else None for item in manifest["trees"]]
        if any(not isinstance(path, str) for path in tree_paths) or tree_paths != sorted(set(tree_paths)):
            raise ValueError("controller runtime tree set is invalid")
        if (
            not isinstance(manifest["import_roots"], list)
            or manifest["import_roots"] != sorted(set(manifest["import_roots"]))
            or any(path not in tree_paths for path in manifest["import_roots"])
            or not isinstance(manifest["python_home"], str)
            or manifest["python_home"] not in tree_paths
        ):
            raise ValueError("controller admitted import roots are invalid")
        expected_launcher_config = (
            "schema=tgw-w09-controller-launch-fds/v1\n"
            f"python={named['python'][0]}\n"
            f"python_home={manifest['python_home']}\n"
            f"bundle={named['bundle'][0]}\n"
            f"closure={named['closure'][0]}\n"
            f"receipt={named['receipt'][0]}\n"
        ).encode()
        if named["launcher_config"][2] != expected_launcher_config:
            raise ValueError("controller launcher config does not bind exact execution inputs")
        closure_start = len(artifacts)
        for index, binding in enumerate(manifest["files"]):
            if not isinstance(binding, Mapping) or set(binding) != {
                "path",
                "sha256",
                "dev",
                "ino",
                "uid",
                "gid",
                "mode",
                "nlink",
                "size",
                "elf",
            }:
                raise ValueError("controller native closure binding is invalid")
            held_binding = {name: binding[name] for name in binding if name != "elf"}
            if require_launcher:
                base_raw = os.environ.get("TGW_W09_RUNTIME_FD_BASE", "")
                count_raw = os.environ.get("TGW_W09_RUNTIME_FD_COUNT", "")
                if not base_raw.isdecimal() or not count_raw.isdecimal():
                    raise ValueError("controller pre-exec closure handoff is absent")
                if int(count_raw) != len(manifest["files"]) + len(manifest["trees"]):
                    raise ValueError("controller pre-exec closure count differs")
                proc_path = Path(f"/proc/self/fd/{int(base_raw) + index}")
                inherited_fd = os.open(proc_path, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    metadata = os.fstat(inherited_fd)
                    raw = os.pread(inherited_fd, int(binding["size"]) + 1, 0)
                    identity = (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_uid,
                        metadata.st_gid,
                        metadata.st_mode,
                        metadata.st_nlink,
                        metadata.st_size,
                    )
                    expected_identity = (
                        binding["dev"],
                        binding["ino"],
                        binding["uid"],
                        binding["gid"],
                        stat.S_IFREG | binding["mode"],
                        binding["nlink"],
                        binding["size"],
                    )
                    if identity != expected_identity or "sha256:" + sha256(raw).hexdigest() != binding["sha256"]:
                        raise ValueError("controller inherited closure artifact differs")
                    artifact = (Path(binding["path"]), inherited_fd, raw, identity)
                except Exception:
                    os.close(inherited_fd)
                    raise
            else:
                artifact = _hold_runtime_artifact(
                    held_binding,
                    label=f"controller module {index}",
                )
            expected_elf = binding["elf"]
            if expected_elf is not None:
                if not isinstance(expected_elf, Mapping) or set(expected_elf) != {
                    "pt_interp",
                    "needed",
                    "rpath",
                    "runpath",
                    "soname",
                    "pt_interp_resolved",
                    "resolved",
                }:
                    os.close(artifact[1])
                    raise ValueError("controller resolved ELF binding is invalid")
                raw_elf = {name: expected_elf[name] for name in expected_elf if name not in {"resolved", "pt_interp_resolved"}}
            else:
                raw_elf = None
            if _elf_closure(artifact[2]) != raw_elf:
                os.close(artifact[1])
                raise ValueError("controller ELF closure differs from its binding")
            artifacts.append(artifact)
        for index, binding in enumerate(manifest["trees"]):
            inherited_path = None
            if require_launcher:
                base = int(os.environ["TGW_W09_RUNTIME_FD_BASE"])
                inherited_path = Path(f"/proc/self/fd/{base + len(manifest['files']) + index}")
            trees.append(
                _hold_runtime_tree(
                    binding,
                    inherited_path=inherited_path,
                )
            )
        if named["closure"][2] != _preexec_closure(manifest["files"], manifest["trees"]):
            raise ValueError("controller pre-exec closure differs from runtime manifest")
        materialization = json.loads(named["receipt"][2])
        if not isinstance(materialization, dict) or set(materialization) != {
            "schema",
            "controller_source_receipt_sha256",
            "application_candidate",
            "launcher_build_receipt_sha256",
            "launcher",
            "python",
            "bundle",
            "manifest",
            "closure",
            "launcher_config",
            "receipt_sha256",
        }:
            raise ValueError("controller runtime materialization receipt is invalid")
        unsigned_materialization = dict(materialization)
        materialization_hash = unsigned_materialization.pop("receipt_sha256")
        if (
            materialization["schema"] != "tgw-w09-controller-runtime-materialization/v1"
            or materialization_hash != "sha256:" + sha256(_canonical(unsigned_materialization)).hexdigest()
            or materialization["launcher"] != value["launcher"]
            or materialization["python"] != value["python"]
            or materialization["bundle"] != value["bundle"]
        ):
            raise ValueError("controller runtime materialization binding differs")

        def output_binding(item: Mapping[str, Any], content_sha256: str) -> dict[str, Any]:
            identity = item.get("identity")
            if not isinstance(identity, list) or len(identity) != 7:
                raise ValueError("controller materialized output identity is invalid")
            return {
                "path": item["path"],
                "sha256": content_sha256,
                "dev": identity[0],
                "ino": identity[1],
                "uid": identity[2],
                "gid": identity[3],
                "mode": stat.S_IMODE(identity[4]),
                "nlink": identity[5],
                "size": identity[6],
            }

        receipt_manifest = materialization["manifest"]
        receipt_closure = materialization["closure"]
        receipt_config = materialization["launcher_config"]
        if (
            not isinstance(receipt_manifest, Mapping)
            or not isinstance(receipt_closure, Mapping)
            or not isinstance(receipt_config, Mapping)
            or output_binding(receipt_manifest, receipt_manifest["content_sha256"]) != value["manifest"]
            or output_binding(receipt_closure, receipt_closure["sha256"]) != value["closure"]
            or output_binding(receipt_config, receipt_config["sha256"]) != value["launcher_config"]
        ):
            raise ValueError("controller materialized outputs differ from receipt")
        manifest["_materialization_receipt"] = materialization
        manifest_paths = {str(artifact[0].resolve(strict=True)) for artifact in artifacts[closure_start:]}
        if str(named["python"][0].resolve(strict=True)) not in manifest_paths:
            raise ValueError("controller interpreter is absent from the runtime manifest")
        for item in manifest["files"]:
            elf = item["elf"]
            if elf is None:
                continue
            if elf["pt_interp"] is not None and elf["pt_interp_resolved"] not in manifest_paths:
                raise ValueError("controller ELF interpreter is outside the held closure")
            resolved = elf["resolved"]
            if (
                not isinstance(resolved, list)
                or resolved != sorted(resolved, key=lambda value: (value.get("soname", ""), value.get("path", "")))
                or any(not isinstance(edge, Mapping) or set(edge) != {"soname", "path"} or edge["path"] not in manifest_paths for edge in resolved)
                or [edge["soname"] for edge in resolved] != elf["needed"]
            ):
                raise ValueError("controller exact ELF dependency edges are invalid")
        runtime_evidence = (
            "w09-controller-runtime:"
            + "sha256:"
            + sha256(
                _canonical(
                    {
                        "manifest_sha256": claimed,
                        "identities": [list(artifact[3]) for artifact in artifacts],
                        "trees": [{"path": str(tree[0]), "sha256": tree[2], "identity": list(tree[3])} for tree in trees],
                    }
                )
            ).hexdigest()
        )
        return manifest, artifacts, trees, runtime_evidence
    except Exception:
        for _path, fd, _raw, _identity in reversed(artifacts):
            os.close(fd)
        for _path, fd, _digest, _identity, _uid, _gid in reversed(trees):
            os.close(fd)
        raise


def _revalidate_controller_runtime(
    manifest: Mapping[str, Any],
    artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]],
    trees: list[tuple[Path, int, str, tuple[int, ...], int, int]],
    *,
    freeze_mapped: bool = False,
) -> str:
    for artifact in artifacts:
        _revalidate_runtime_artifact(artifact)
    for tree in trees:
        _revalidate_runtime_tree(tree)
    allowed_bindings = {str(Path(item["path"]).resolve(strict=True)): item for item in manifest["files"]}
    allowed = set(allowed_bindings)
    loaded = set()
    for module in tuple(sys.modules.values()):
        location = getattr(module, "__file__", None)
        if not isinstance(location, str) or location.startswith("<"):
            continue
        if location.startswith(sys.argv[0] + "/"):
            continue
        cached = getattr(module, "__cached__", None)
        if location.endswith((".pyc", ".pyo")) or (isinstance(cached, str) and Path(cached).exists()):
            raise OSError("controller imported bytecode")
        loaded.add(str(Path(location).resolve(strict=True)))
    roots = [Path(item["path"]).resolve(strict=True) for item in manifest["trees"]]
    held_mapping_identities = _held_mapping_identities(
        manifest,
        artifacts,
    )
    mapped = _mapped_runtime_identity(
        Path("/proc/self/maps").read_text(encoding="utf-8"),
        allowed_bindings=allowed_bindings,
        roots=roots,
        expected_identities=held_mapping_identities,
    )
    unexpected = {path for path in (loaded | set(mapped)) - allowed if not any(Path(path).is_relative_to(root) for root in roots)}
    unexpected |= {path for path in loaded if path.endswith((".pyc", ".pyo")) and path not in allowed}
    mapped_tree_neighbors = {path for path in mapped if path not in allowed and any(Path(path).is_relative_to(root) for root in roots)}
    unexpected |= mapped_tree_neighbors
    if unexpected:
        if os.environ.get("TGW_W09_RUNTIME_PROBE") == "1":
            sys.stderr.write("w09-runtime-probe-unexpected:" + json.dumps(sorted(unexpected)) + "\n")
        raise OSError("loaded controller modules are absent from the protected runtime manifest: " + "sha256:" + sha256(_canonical(sorted(unexpected))).hexdigest())
    mapped_evidence = (
        "sha256:"
        + sha256(
            _canonical(
                [
                    {
                        "path": path,
                        "dev": identity[0],
                        "ino": identity[1],
                        "sha256": allowed_bindings[path]["sha256"],
                    }
                    for path, identity in sorted(mapped.items())
                    if path in allowed_bindings
                ]
            )
        ).hexdigest()
    )
    prior_evidence = manifest.get("_mapped_runtime_sha256")
    if prior_evidence is not None and prior_evidence != mapped_evidence:
        raise OSError("controller native mapping set changed after admission")
    if freeze_mapped:
        if not isinstance(manifest, dict):
            raise OSError("controller runtime manifest cannot retain mapping evidence")
        manifest["_mapped_runtime_sha256"] = mapped_evidence
    return mapped_evidence


def _mapped_runtime_identity(
    maps_raw: str,
    *,
    allowed_bindings: Mapping[str, Mapping[str, Any]],
    roots: list[Path],
    expected_identities: Mapping[str, tuple[int, int]] | None = None,
) -> dict[str, tuple[int, int]]:
    mapped: dict[str, tuple[int, int]] = {}
    for line in maps_raw.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            if fields[5].endswith(" (deleted)"):
                raise OSError("controller native mapping was deleted during execution")
            try:
                major, minor = (int(value, 16) for value in fields[3].split(":", 1))
                inode = int(fields[4])
            except (TypeError, ValueError) as exc:
                raise OSError("controller native mapping identity is invalid") from exc
            resolved = str(Path(fields[5]).resolve(strict=True))
            identity = (os.makedev(major, minor), inode)
            prior = mapped.setdefault(resolved, identity)
            if prior != identity:
                raise OSError("controller native mapping path has multiple identities")
            binding = allowed_bindings.get(resolved)
            expected = expected_identities.get(resolved) if expected_identities is not None else ((binding["dev"], binding["ino"]) if binding is not None else None)
            if binding is not None and identity != expected:
                raise OSError("controller native mapping differs from its held file mapping: " + resolved + ":sha256:" + sha256(_canonical([identity, expected])).hexdigest())
    mapped_tree_neighbors = {path for path in mapped if path not in allowed_bindings and any(Path(path).is_relative_to(root) for root in roots)}
    if mapped_tree_neighbors:
        raise OSError("controller mapped an unbound native neighbor from an import root")
    return mapped


def _held_mapping_identities(
    manifest: Mapping[str, Any],
    artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]],
) -> dict[str, tuple[int, int]]:
    """Learn map-device identities by mapping the already-held exact FDs."""

    bindings = {str(Path(item["path"]).resolve(strict=True)): item for item in manifest["files"]}
    descriptors = {}
    for path, fd, raw, identity in artifacts:
        resolved = str(path.resolve(strict=True))
        binding = bindings.get(resolved)
        if binding is not None and raw and identity[0] == binding["dev"] and identity[1] == binding["ino"]:
            descriptors.setdefault(resolved, fd)
    if set(descriptors) != {path for path, item in bindings.items() if item["size"] > 0}:
        raise OSError("controller held mapping closure is incomplete")
    probes = []
    try:
        for path in sorted(descriptors):
            probes.append(mmap.mmap(descriptors[path], 1, access=mmap.ACCESS_READ))
        raw = Path("/proc/self/maps").read_text(encoding="utf-8")
        observed: dict[str, set[tuple[int, int]]] = {path: set() for path in descriptors}
        for line in raw.splitlines():
            fields = line.split(maxsplit=5)
            if len(fields) != 6 or not fields[5].startswith("/") or fields[5].endswith(" (deleted)"):
                continue
            resolved = str(Path(fields[5]).resolve(strict=True))
            if resolved not in observed:
                continue
            major, minor = (int(value, 16) for value in fields[3].split(":", 1))
            observed[resolved].add((os.makedev(major, minor), int(fields[4])))
        if any(len(identities) != 1 for identities in observed.values()):
            raise OSError("controller held file did not produce one exact mapping identity")
        result = {path: next(iter(identities)) for path, identities in observed.items()}
        if any(result[path][1] != bindings[path]["ino"] for path in result):
            raise OSError("controller held map inode differs from held file")
        return result
    finally:
        for probe in reversed(probes):
            probe.close()


def _held_import_roots(
    manifest: Mapping[str, Any],
    trees: list[tuple[Path, int, str, tuple[int, ...], int, int]],
) -> list[str]:
    by_path = {str(path): fd for path, fd, _digest, _identity, _uid, _gid in trees}
    try:
        return [f"/proc/self/fd/{by_path[path]}" for path in manifest["import_roots"]]
    except KeyError as exc:
        raise OSError("controller import root is not held") from exc


def _validate_early_python_home(
    manifest: Mapping[str, Any],
    trees: list[tuple[Path, int, str, tuple[int, ...], int, int]],
) -> None:
    raw_fd = os.environ.get("TGW_W09_PYTHON_HOME_FD", "")
    if not raw_fd.isdecimal():
        raise ValueError("controller Python-home descriptor handoff is absent")
    home_fd = int(raw_fd)
    home_path = str(manifest["python_home"])
    held = next((tree for tree in trees if str(tree[0]) == home_path), None)
    if held is None:
        raise ValueError("controller Python home is not held")
    metadata = os.fstat(home_fd)
    if (metadata.st_dev, metadata.st_ino) != (held[3][0], held[3][1]):
        raise ValueError("controller Python-home handoff differs from its manifest")
    proc_root = f"/proc/self/fd/{home_fd}"
    if sys.prefix != proc_root or sys.exec_prefix != proc_root:
        raise ValueError("controller Python initialized outside its held home")
    stdlib_root = f"{proc_root}/lib/python{sys.version_info.major}.{sys.version_info.minor}"
    for module in tuple(sys.modules.values()):
        location = getattr(module, "__file__", None)
        if not isinstance(location, str):
            continue
        if location == sys.argv[0] or location.startswith(sys.argv[0] + "/"):
            continue
        if location != stdlib_root and not location.startswith(stdlib_root + "/"):
            raise ValueError("controller loaded an early module outside its held Python home")


def _hold_controller_source(
    binding: Any,
    runtime_bundle: tuple[Path, int, bytes, tuple[int, ...]],
) -> tuple[dict[str, Any], list[tuple[Path, int, bytes, tuple[int, ...]]], str]:
    receipt_artifact = _hold_runtime_artifact(
        binding,
        label="controller source receipt",
        max_bytes=1024 * 1024,
    )
    artifacts = [receipt_artifact]
    try:
        receipt = json.loads(receipt_artifact[2])
        if not isinstance(receipt, dict) or set(receipt) != {
            "schema",
            "controller_source",
            "controller_bundle",
            "controller_launcher_source",
            "application_candidate",
            "materialization",
            "receipt_sha256",
        }:
            raise ValueError("controller source receipt is invalid")
        unsigned = dict(receipt)
        claimed = unsigned.pop("receipt_sha256")
        if receipt["schema"] != "tgw-w09-controller-bundle-receipt/v1" or claimed != "sha256:" + sha256(_canonical(unsigned)).hexdigest():
            raise ValueError("controller source receipt self-hash is invalid")
        source = receipt["controller_source"]
        bundle = receipt["controller_bundle"]
        launcher_source = receipt["controller_launcher_source"]
        if (
            not isinstance(source, Mapping)
            or not isinstance(bundle, Mapping)
            or re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit"))) is None
            or re.fullmatch(r"[0-9a-f]{40}", str(source.get("tree"))) is None
            or _SHA256.fullmatch(str(source.get("archive_sha256"))) is None
            or _SHA256.fullmatch(str(source.get("projection_sha256"))) is None
            or bundle.get("sha256") != "sha256:" + sha256(runtime_bundle[2]).hexdigest()
            or bundle.get("identity") != list(runtime_bundle[3])
            or bundle.get("bytecode_policy") != "-B-source-only-zip"
            or not isinstance(launcher_source, Mapping)
            or launcher_source.get("archive_path") != "src/tgw/w09_controller_launcher.c"
            or not isinstance(launcher_source.get("materialized_path"), str)
            or _SHA256.fullmatch(str(launcher_source.get("sha256"))) is None
            or not isinstance(launcher_source.get("size"), int)
            or not isinstance(launcher_source.get("identity"), list)
            or len(launcher_source["identity"]) != 7
            or launcher_source.get("build_contract") != "static-elf-no-interp-no-needed@1"
        ):
            raise ValueError("controller source/bundle binding differs")
        archive_identity = receipt["materialization"]["archive_identity"]
        archive_binding = {
            "path": source["archive_path"],
            "sha256": source["archive_sha256"],
            "dev": archive_identity[0],
            "ino": archive_identity[1],
            "uid": archive_identity[2],
            "gid": archive_identity[3],
            "mode": stat.S_IMODE(archive_identity[4]),
            "nlink": archive_identity[5],
            "size": source["archive_size"],
        }
        archive_artifact = _hold_runtime_artifact(
            archive_binding,
            label="controller source archive",
            max_bytes=64 * 1024 * 1024,
        )
        artifacts.append(archive_artifact)
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(archive_artifact[2]), mode="r:") as retained:
            member = retained.getmember(launcher_source["archive_path"])
            extracted = retained.extractfile(member)
            if extracted is None:
                raise ValueError("controller launcher source is absent from retained archive")
            launcher_raw = extracted.read(1024 * 1024 + 1)
        if "sha256:" + sha256(launcher_raw).hexdigest() != launcher_source["sha256"]:
            raise ValueError("controller launcher source differs from retained archive")
        launcher_identity = launcher_source["identity"]
        artifacts.append(
            _hold_runtime_artifact(
                {
                    "path": launcher_source["materialized_path"],
                    "sha256": launcher_source["sha256"],
                    "dev": launcher_identity[0],
                    "ino": launcher_identity[1],
                    "uid": launcher_identity[2],
                    "gid": launcher_identity[3],
                    "mode": stat.S_IMODE(launcher_identity[4]),
                    "nlink": launcher_identity[5],
                    "size": launcher_identity[6],
                },
                label="controller materialized launcher source",
                max_bytes=1024 * 1024,
            )
        )
        evidence = "w09-controller-source:" + claimed
        return receipt, artifacts, evidence
    except Exception:
        for _path, fd, _raw, _identity in reversed(artifacts):
            os.close(fd)
        raise


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


def _close_runtime_artifacts(
    artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]],
    errors: list[Exception],
) -> None:
    """Postcheck and close each held artifact exactly once."""

    for artifact in reversed(artifacts):
        try:
            _revalidate_runtime_artifact(artifact)
        except Exception as exc:
            errors.append(exc)
        try:
            os.close(artifact[1])
        except OSError as exc:
            errors.append(exc)


def _forbidden(_parameters: Mapping[str, str]) -> Mapping[str, Any]:
    raise ValueError("unrelated steady-state effect is not mounted in W09")


def execute_from_fixed_config(path: Path = CONFIG_PATH) -> Mapping[str, Any]:
    """Compose exact mounted authorities, execute the grant, and persist terminal output."""

    config, config_fd, config_raw, config_identity = _held_config(path)
    runtime_manifest: dict[str, Any] | None = None
    runtime_artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]] = []
    runtime_trees: list[tuple[Path, int, str, tuple[int, ...], int, int]] = []
    runtime_evidence = ""
    source_artifacts: list[tuple[Path, int, bytes, tuple[int, ...]]] = []
    source_receipt: dict[str, Any] | None = None
    source_evidence = ""
    readers: dict[Path, Any] = {}
    authority = provider = terminal = None
    try:
        runtime_manifest, runtime_artifacts, runtime_trees, runtime_evidence = _hold_controller_runtime(
            config["controller_runtime"],
            require_launcher=True,
        )
        _validate_early_python_home(runtime_manifest, runtime_trees)
        python_path = runtime_artifacts[1][0].resolve(strict=True)
        if not _isolated_runtime() or Path("/proc/self/exe").resolve(strict=True) != python_path:
            raise ValueError("W09 controller must run through its exact isolated interpreter")
        _revalidate_controller_runtime(runtime_manifest, runtime_artifacts, runtime_trees)
        for import_root in reversed(_held_import_roots(runtime_manifest, runtime_trees)):
            sys.path.insert(0, import_root)
        source_receipt, source_artifacts, source_evidence = _hold_controller_source(
            config["controller_source"],
            runtime_artifacts[2],
        )
        runtime_materialization = runtime_manifest["_materialization_receipt"]
        if (
            runtime_materialization["controller_source_receipt_sha256"] != source_receipt["receipt_sha256"]
            or runtime_materialization["application_candidate"] != source_receipt["application_candidate"]
        ):
            raise ValueError("controller runtime is cross-bound to a different source/application")

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

        mapped_evidence = _revalidate_controller_runtime(
            runtime_manifest,
            runtime_artifacts,
            runtime_trees,
            freeze_mapped=True,
        )
        runtime_evidence += ":mapped:" + mapped_evidence
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
        expected_application = source_receipt["application_candidate"]
        mounted_application = provider.descriptor["candidate"]
        if expected_application != {
            "commit": mounted_application["commit"],
            "tree": mounted_application["tree"],
            "archive_sha256": mounted_application["archive_sha256"],
            "projection_sha256": mounted_application["projection_sha256"],
        }:
            raise ValueError("controller source is cross-bound to a different application candidate")
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
                        "source": source_evidence,
                    }
                )
            ).hexdigest()
        )

        def terminal_precheck() -> None:
            _revalidate_config(path, config_fd, config_raw, config_identity)
            _revalidate_controller_runtime(
                runtime_manifest,
                runtime_artifacts,
                runtime_trees,
                freeze_mapped=True,
            )
            for artifact in source_artifacts:
                _revalidate_runtime_artifact(artifact)

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
        _close_runtime_artifacts(runtime_artifacts, cleanup_errors)
        for tree in reversed(runtime_trees):
            try:
                _revalidate_runtime_tree(tree)
            except Exception as exc:
                cleanup_errors.append(exc)
            try:
                os.close(tree[1])
            except OSError as exc:
                cleanup_errors.append(exc)
        _close_runtime_artifacts(source_artifacts, cleanup_errors)
        try:
            os.close(config_fd)
        except OSError as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            sys.stderr.write("w09-controller-cleanup:" + sha256(_canonical([type(item).__name__ for item in cleanup_errors])).hexdigest() + "\n")


def _isolated_runtime() -> bool:
    return (
        bool(sys.flags.dont_write_bytecode)
        and bool(sys.flags.no_site)
        and bool(sys.flags.no_user_site)
        and bool(sys.flags.safe_path)
        and sys.pycache_prefix == "/proc/self/fd/2147483647"
        and "PYTHONPATH" not in os.environ
        and "PYTHONUSERBASE" not in os.environ
    )


def _runtime_probe_main() -> int:
    """Exercise the exact held runtime/import closure without any authority/effect."""

    raw_fd = os.environ.get("TGW_W09_RUNTIME_RECEIPT_FD", "")
    if not raw_fd.isdecimal():
        raise SystemExit("W09 runtime probe receipt handoff is absent")
    inherited_path = Path(f"/proc/self/fd/{int(raw_fd)}")
    receipt_raw = inherited_path.read_bytes()
    receipt = json.loads(receipt_raw)
    receipt_path = Path(os.readlink(inherited_path))

    def output_binding(item: Mapping[str, Any], digest: str) -> dict[str, Any]:
        identity = item["identity"]
        return {
            "path": item["path"],
            "sha256": digest,
            "dev": identity[0],
            "ino": identity[1],
            "uid": identity[2],
            "gid": identity[3],
            "mode": stat.S_IMODE(identity[4]),
            "nlink": identity[5],
            "size": identity[6],
        }

    metadata = receipt_path.stat()
    runtime = {
        "launcher": receipt["launcher"],
        "python": receipt["python"],
        "bundle": receipt["bundle"],
        "manifest": output_binding(
            receipt["manifest"],
            receipt["manifest"]["content_sha256"],
        ),
        "closure": output_binding(receipt["closure"], receipt["closure"]["sha256"]),
        "launcher_config": output_binding(
            receipt["launcher_config"],
            receipt["launcher_config"]["sha256"],
        ),
        "receipt": {
            "path": str(receipt_path),
            "sha256": "sha256:" + sha256(receipt_raw).hexdigest(),
            "dev": metadata.st_dev,
            "ino": metadata.st_ino,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
            "nlink": metadata.st_nlink,
            "size": metadata.st_size,
        },
    }
    manifest, artifacts, trees, evidence = _hold_controller_runtime(
        runtime,
        require_launcher=True,
    )
    _validate_early_python_home(manifest, trees)
    errors: list[Exception] = []
    try:
        for import_root in reversed(_held_import_roots(manifest, trees)):
            sys.path.insert(0, import_root)
        for module in (
            "promptcraft.handoff",
            "tgw.application_deployment_contract",
            "tgw.application_release_provider",
            "tgw.bootstrap_authority",
            "tgw.candidate_receipt_sink",
            "tgw.deployment_runtime",
            "tgw.effect_completion_store",
        ):
            __import__(module)
        mapped_evidence = _revalidate_controller_runtime(
            manifest,
            artifacts,
            trees,
            freeze_mapped=True,
        )
        sys.stdout.buffer.write(
            _canonical(
                {
                    "schema": "tgw-w09-runtime-probe/v1",
                    "evidence": evidence + ":mapped:" + mapped_evidence,
                }
            )
            + b"\n"
        )
        return 0
    finally:
        _close_runtime_artifacts(artifacts, errors)
        for tree in reversed(trees):
            try:
                _revalidate_runtime_tree(tree)
            except Exception as exc:
                errors.append(exc)
            os.close(tree[1])
        if errors:
            raise OSError("W09 runtime probe cleanup/postcheck failed")


def main() -> int:
    if len(sys.argv) != 1:
        raise SystemExit("tgw-w09-application-bootstrap accepts no arguments")
    if not _isolated_runtime():
        raise SystemExit("tgw-w09-application-bootstrap requires its exact isolated launcher")
    if os.environ.get("TGW_W09_RUNTIME_PROBE") == "1":
        return _runtime_probe_main()
    result = execute_from_fixed_config()
    sys.stdout.buffer.write(_canonical(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

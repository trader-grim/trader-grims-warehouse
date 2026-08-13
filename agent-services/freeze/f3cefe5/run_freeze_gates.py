#!/usr/bin/env python3
"""Execute and record the literal f3cefe5 freeze gate matrix.

The runner deliberately uses a cleared environment for each gate.  It opens
executables and declared inputs once, retains the descriptors through child
exit, and executes the exact held executable through the stable parent procfd.
Generated outputs are parsed and protected from one held descriptor.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SOURCE_COMMIT = "f3cefe544a9f81422b57707c4289f2974c6dca51"
SOURCE_TREE = "2c6cc6199827aa8ce87686c02cdccb1c0373cca3"
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
PLAN_SOLUTION = "sha256:d28650c26c6a3d26d6c943597ccb7abd7c6670b1703d9ce941ac5ed7a2d73a4d"
PLAN_CLOSURE = "sha256:bc0c53b2574fc359c629bd213e078fdd2824e5e1c4a98c0c7a347de869d9e6f8"
LUET_SHA256 = "c227742324a92eef4767961a9e49f687195b13356881336cc83d006e43d86c87"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


IDENTITY_KEYS = (
    "sha256", "size", "dev", "inode", "uid", "gid", "mode", "nlink",
    "mtime_ns", "ctime_ns",
)


def _open_component_safe(path: Path) -> tuple[Path, int]:
    resolved = path.resolve(strict=True)
    if not resolved.is_absolute():
        raise RuntimeError(f"held path is not absolute: {resolved}")
    parts = resolved.parts[1:]
    directory = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for component in parts[:-1]:
            following = os.open(
                component,
                os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            os.close(directory)
            directory = following
        held = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory,
        )
    finally:
        os.close(directory)
    return resolved, held


def _snapshot_fd(held: int) -> tuple[dict[str, Any], bytes]:
    observed = os.fstat(held)
    if not stat.S_ISREG(observed.st_mode):
        raise RuntimeError("held evidence input is not regular")
    chunks: list[bytes] = []
    offset = 0
    while True:
        raw = os.pread(held, 1024 * 1024, offset)
        if not raw:
            break
        chunks.append(raw)
        offset += len(raw)
    content = b"".join(chunks)
    return {
        "sha256": "sha256:" + digest(content),
        "size": observed.st_size, "dev": observed.st_dev, "inode": observed.st_ino,
        "uid": observed.st_uid, "gid": observed.st_gid,
        "mode": f"{stat.S_IMODE(observed.st_mode):04o}", "nlink": observed.st_nlink,
        "mtime_ns": observed.st_mtime_ns, "ctime_ns": observed.st_ctime_ns,
    }, content


def _require_unchanged(before: dict[str, Any], after: dict[str, Any], label: str) -> None:
    changed = [key for key in IDENTITY_KEYS if before[key] != after[key]]
    if changed:
        raise RuntimeError(f"{label} changed while held: {','.join(changed)}")


def _snapshot_tree(root: Path, *, require_protected: bool) -> dict[str, Any]:
    resolved = root.resolve(strict=True)
    entries: list[dict[str, Any]] = []
    for path in [resolved, *sorted(resolved.rglob("*"))]:
        observed = path.lstat()
        relative = "." if path == resolved else path.relative_to(resolved).as_posix()
        if stat.S_ISLNK(observed.st_mode):
            raise RuntimeError(f"tree symlink refused: {path}")
        common = {
            "path": relative, "dev": observed.st_dev, "inode": observed.st_ino,
            "uid": observed.st_uid, "gid": observed.st_gid,
            "mode": f"{stat.S_IMODE(observed.st_mode):04o}", "nlink": observed.st_nlink,
            "mtime_ns": observed.st_mtime_ns, "ctime_ns": observed.st_ctime_ns,
        }
        if stat.S_ISDIR(observed.st_mode):
            if require_protected and (observed.st_uid != 0 or observed.st_gid != 0 or stat.S_IMODE(observed.st_mode) != 0o555):
                raise RuntimeError(f"protected tree directory identity invalid: {path}")
            entries.append({**common, "type": "directory"})
        elif stat.S_ISREG(observed.st_mode):
            _, held = _open_component_safe(path)
            try:
                file_meta, _ = _snapshot_fd(held)
            finally:
                os.close(held)
            if require_protected and (observed.st_uid != 0 or observed.st_gid != 0 or stat.S_IMODE(observed.st_mode) != 0o444):
                raise RuntimeError(f"protected tree file identity invalid: {path}")
            entries.append({**common, "type": "file", "sha256": file_meta["sha256"],
                            "size": file_meta["size"]})
        else:
            raise RuntimeError(f"tree special file refused: {path}")
    content_entries = [
        {key: item[key] for key in ("path", "type", "mode", "sha256", "size") if key in item}
        for item in entries
    ]
    tree_hash = "sha256:" + digest(canonical({"schema": "tgw-protected-tree-content/v1",
                                                "entries": content_entries}))
    return {"path": str(resolved), "tree_hash": tree_hash, "entries": entries,
            "content_entries": content_entries}


_PROTECT = r'''import hashlib,os,stat,sys
src,root,want=sys.argv[1:]
raw=open(src,'rb').read()
assert hashlib.sha256(raw).hexdigest()==want
rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
    try:
        fd=os.open(want,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444,dir_fd=rootfd)
    except FileExistsError:
        fd=os.open(want,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=rootfd)
        old=os.read(fd,len(raw)+1)
        s=os.fstat(fd)
        assert old==raw and s.st_uid==0 and s.st_gid==0 and stat.S_IMODE(s.st_mode)==0o444 and s.st_nlink==1
        os.close(fd)
    else:
        view=memoryview(raw)
        while view:
            count=os.write(fd,view)
            assert count>0
            view=view[count:]
        os.fsync(fd)
        os.fchmod(fd,0o444)
        os.fchown(fd,0,0)
        os.close(fd)
        os.fsync(rootfd)
finally:
    os.close(rootfd)
'''

_PROTECT_TREE = r'''import base64,ctypes,json,os,shutil,stat,sys
payload=json.load(sys.stdin)
root=payload['root']; want=payload['tree_hash'].removeprefix('sha256:')
os.makedirs(root,mode=0o755,exist_ok=True)
for parent in (os.path.dirname(root),root):
    s=os.lstat(parent)
    assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and s.st_gid==0 and not stat.S_ISLNK(s.st_mode)
os.chmod(root,0o555)
target=os.path.join(root,want)
if not os.path.exists(target):
    temporary=os.path.join(root,'.incoming-'+str(os.getpid())+'-'+want)
    os.mkdir(temporary,0o700)
    try:
        for entry in payload['files']:
            relative=entry['path']; assert relative and not relative.startswith('/') and '..' not in relative.split('/')
            destination=os.path.join(temporary,relative)
            os.makedirs(os.path.dirname(destination),mode=0o755,exist_ok=True)
            raw=base64.b64decode(entry['content'],validate=True)
            fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
            view=memoryview(raw)
            while view:
                count=os.write(fd,view); assert count>0; view=view[count:]
            os.fsync(fd); os.fchmod(fd,0o444); os.fchown(fd,0,0); os.close(fd)
        for directory,subdirs,_files in os.walk(temporary,topdown=False):
            for name in subdirs:
                path=os.path.join(directory,name); os.chown(path,0,0); os.chmod(path,0o555)
            os.chown(directory,0,0); os.chmod(directory,0o555)
        libc=ctypes.CDLL(None,use_errno=True)
        result=libc.syscall(316,-100,temporary.encode(),-100,target.encode(),1)
        if result != 0:
            error=ctypes.get_errno()
            if error != 17: raise OSError(error,os.strerror(error))
            shutil.rmtree(temporary)
    except BaseException:
        if os.path.exists(temporary):
            os.chmod(temporary,0o700); shutil.rmtree(temporary)
        raise
'''


class FreezeRunner:
    def __init__(self, source: Path, plan: Path, repo: Path, output: Path, store: Path, luet: Path):
        self.source = source
        self.plan = plan
        self.repo = repo
        self.output = output
        self.store = store
        self.luet = luet
        self.records = output / "records"
        self.work = Path("/tmp/tgw-freeze-f3cefe5-gates")
        self.home = self.work / "home"
        self.tmp = self.work / "tmp"
        for item in (self.records, self.work, self.home, self.tmp):
            item.mkdir(parents=True, exist_ok=True)

    def protect_bytes(self, raw: bytes) -> dict[str, Any]:
        want = digest(raw)
        temporary = self.work / (want + ".incoming")
        temporary.write_bytes(raw)
        subprocess.run(
            ["sudo", "-n", "/usr/bin/python3", "-c", _PROTECT, str(temporary), str(self.store), want],
            check=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        )
        temporary.unlink()
        return {"ref": "artifact:sha256:" + want, "sha256": "sha256:" + want, "bytes": len(raw)}

    def protect_tree(self, source: Path) -> tuple[Path, dict[str, Any]]:
        files = []
        content_entries = [{"path": ".", "type": "directory", "mode": "0555"}]
        directories = sorted(path for path in source.rglob("*") if path.is_dir())
        for directory in directories:
            if directory.is_symlink():
                raise RuntimeError(f"tree symlink refused: {directory}")
            content_entries.append({"path": directory.relative_to(source).as_posix(),
                                    "type": "directory", "mode": "0555"})
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"tree symlink refused: {path}")
            if path.is_file():
                raw = path.read_bytes()
                relative = path.relative_to(source).as_posix()
                content_entries.append({"path": relative, "type": "file", "mode": "0444",
                                        "sha256": "sha256:" + digest(raw), "size": len(raw)})
                files.append({"path": relative, "content": base64.b64encode(raw).decode("ascii")})
        content_entries.sort(key=lambda item: (item["path"], item["type"]))
        tree_hash = "sha256:" + digest(canonical({"schema": "tgw-protected-tree-content/v1",
                                                   "entries": content_entries}))
        tree_root = self.store.parent / "trees/sha256"
        payload = {"root": str(tree_root), "tree_hash": tree_hash, "files": files}
        subprocess.run(
            ["sudo", "-n", "/usr/bin/python3", "-c", _PROTECT_TREE],
            input=json.dumps(payload).encode(), check=True,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        )
        target = tree_root / tree_hash.removeprefix("sha256:")
        snapshot = _snapshot_tree(target, require_protected=True)
        if snapshot["tree_hash"] != tree_hash:
            raise RuntimeError("protected tree content hash mismatch")
        return target, snapshot

    def env(self, **extra: str) -> dict[str, str]:
        value = {
            "HOME": str(self.home), "LC_ALL": "C.UTF-8", "NO_COLOR": "1",
            "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0", "PYTHONPATH": str(self.source / "src"),
            "TMPDIR": str(self.tmp),
        }
        value.update(extra)
        return value

    def run(
        self, gate_id: str, executable: Path, args: list[str], *, cwd: Path,
        env: dict[str, str],
        semantic: Callable[[bytes, bytes, dict[str, bytes]], dict[str, Any]],
        inputs: list[Path] | None = None, generated: list[Path] | None = None,
        input_trees: list[Path] | None = None,
        substitute_input_argv: bool = True,
        after_executable_open: Callable[[Path, int], None] | None = None,
        after_inputs_open: Callable[[list[dict[str, Any]]], None] | None = None,
        after_child_exit: Callable[[Path, int], None] | None = None,
        after_generated_open: Callable[[Path, int], None] | None = None,
        during_child: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        executable, exe_fd = _open_component_safe(executable)
        parent_pid = os.getpid()
        actual_executable = f"/proc/{parent_pid}/fd/{exe_fd}"
        exe_before, _ = _snapshot_fd(exe_fd)
        held_inputs: list[dict[str, Any]] = []
        tree_inputs: list[dict[str, Any]] = []
        completed: subprocess.CompletedProcess[bytes] | None = None
        try:
            if after_executable_open:
                after_executable_open(executable, exe_fd)
            for input_path in inputs or []:
                resolved, input_fd = _open_component_safe(input_path)
                before_meta, _ = _snapshot_fd(input_fd)
                held_inputs.append({
                    "logical_path": str(resolved), "held_fd": input_fd,
                    "actual_fd_path": f"/proc/{parent_pid}/fd/{input_fd}",
                    "before": before_meta,
                })
            for tree in input_trees or []:
                tree_before = _snapshot_tree(tree, require_protected=True)
                tree_inputs.append({"logical_path": str(tree.resolve(strict=True)),
                                    "before": tree_before})
            if after_inputs_open:
                after_inputs_open(held_inputs)
            substitutions: dict[str, str] = {}
            if substitute_input_argv:
                for item in held_inputs:
                    substitutions[item["logical_path"]] = item["actual_fd_path"]
            for item in tree_inputs:
                substitutions[item["logical_path"]] = item["logical_path"]
            actual_args = [substitutions.get(str(Path(arg).resolve()), arg) if arg.startswith("/") else arg
                           for arg in args]
            pass_fds = tuple([exe_fd, *(item["held_fd"] for item in held_inputs)])
            before = timestamp()
            started = time.monotonic_ns()
            if during_child is None:
                completed = subprocess.run(
                    [actual_executable, *actual_args], cwd=cwd, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                    pass_fds=pass_fds, timeout=300,
                )
            else:
                process = subprocess.Popen(
                    [actual_executable, *actual_args], cwd=cwd, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=pass_fds,
                )
                try:
                    during_child()
                    child_stdout, child_stderr = process.communicate(timeout=300)
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
                completed = subprocess.CompletedProcess(
                    [actual_executable, *actual_args], process.returncode,
                    child_stdout, child_stderr,
                )
            ended = time.monotonic_ns()
            after = timestamp()
            if after_child_exit:
                after_child_exit(executable, exe_fd)
            exe_after, _ = _snapshot_fd(exe_fd)
            _require_unchanged(exe_before, exe_after, f"gate {gate_id} executable")
            named_executable_path, named_executable_fd = _open_component_safe(executable)
            try:
                exe_named_after, _ = _snapshot_fd(named_executable_fd)
            finally:
                os.close(named_executable_fd)
            _require_unchanged(exe_before, exe_named_after,
                               f"gate {gate_id} named executable {named_executable_path}")

            input_records = []
            for item in held_inputs:
                input_after, _ = _snapshot_fd(item["held_fd"])
                _require_unchanged(item["before"], input_after,
                                   f"gate {gate_id} input {item['logical_path']}")
                named_path, named_fd = _open_component_safe(Path(item["logical_path"]))
                try:
                    named_after, _ = _snapshot_fd(named_fd)
                finally:
                    os.close(named_fd)
                _require_unchanged(item["before"], named_after,
                                   f"gate {gate_id} named input {named_path}")
                input_records.append({
                    "logical_path": item["logical_path"],
                    "actual_fd_path": item["actual_fd_path"],
                    "held_fd": item["held_fd"], "before": item["before"],
                    "after": input_after, "named_after": named_after,
                    "unchanged": True,
                    "argv_substituted": substitute_input_argv and item["logical_path"] in args,
                    **({"tree": item["tree"]} if "tree" in item else {}),
                })
            tree_records = []
            for item in tree_inputs:
                tree_after = _snapshot_tree(Path(item["logical_path"]), require_protected=True)
                if item["before"] != tree_after:
                    raise RuntimeError(f"gate {gate_id} protected input tree changed")
                tree_records.append({"logical_path": item["logical_path"],
                                     "actual_argv_path": item["logical_path"],
                                     "before": item["before"], "after": tree_after,
                                     "unchanged": True, "protected": True,
                                     "argv_substituted": False})

            generated_bytes: dict[str, bytes] = {}
            generated_refs = []
            for path in generated or []:
                resolved, generated_fd = _open_component_safe(path)
                try:
                    generated_before, raw = _snapshot_fd(generated_fd)
                    if after_generated_open:
                        after_generated_open(resolved, generated_fd)
                    protected = self.protect_bytes(raw)
                    generated_after, _ = _snapshot_fd(generated_fd)
                    _require_unchanged(generated_before, generated_after,
                                       f"gate {gate_id} generated {resolved}")
                    named_path, named_fd = _open_component_safe(resolved)
                    try:
                        named_after, _ = _snapshot_fd(named_fd)
                    finally:
                        os.close(named_fd)
                    _require_unchanged(generated_before, named_after,
                                       f"gate {gate_id} named generated {named_path}")
                finally:
                    os.close(generated_fd)
                generated_bytes[path.name] = raw
                protected.update({
                    "role": path.name, "source_path": str(resolved),
                    "before": generated_before, "after": generated_after,
                    "named_after": named_after, "unchanged": True,
                })
                generated_refs.append(protected)

            result = semantic(completed.stdout, completed.stderr, generated_bytes)
            if completed.returncode != 0 or result.get("status") != "PASS":
                raise RuntimeError(f"gate {gate_id} HOLD rc={completed.returncode} semantic={result}")
            stdout = self.protect_bytes(completed.stdout)
            stderr = self.protect_bytes(completed.stderr)
            record: dict[str, Any] = {
                "schema": "tgw-freeze-execution-record/v2", "gate_id": gate_id,
                "unsigned_hash_scheme": "sha256 of canonical JSON excluding unsigned_sha256; final file sha256 is external in catalog",
                "source": {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "status": "CLEAN_DETACHED"},
                "executable": {
                    "logical_path": str(executable), "actual_fd_path": actual_executable,
                    "held_fd": exe_fd, "before": exe_before, "after": exe_after,
                    "named_after": exe_named_after, "unchanged": True,
                    "component_safe_open": True,
                },
                "actual_execve_argv": [actual_executable, *actual_args],
                "logical_replay_argv": [str(executable), *args],
                "descriptor_execution": {
                    "parent_pid": parent_pid, "pass_fds": list(pass_fds),
                    "executable_fd": exe_fd,
                },
                "environment": {"clear_inherited": True, "values": env}, "cwd": str(cwd),
                "started_at": before, "ended_at": after, "duration_ns": ended - started,
                "rc": completed.returncode, "stdout": stdout, "stderr": stderr,
                "inputs": input_records, "input_trees": tree_records,
                "generated_artifacts": generated_refs, "semantic": result,
            }
            record["unsigned_sha256"] = "sha256:" + digest(canonical(record))
            target = self.records / f"{gate_id}.json"
            target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
            return record
        finally:
            for item in held_inputs:
                os.close(item["held_fd"])
            os.close(exe_fd)

    @staticmethod
    def pass_empty(stdout: bytes, stderr: bytes, _generated: dict[str, bytes]) -> dict[str, Any]:
        return {"status": "PASS", "stdout_empty": not stdout, "stderr_empty": not stderr}

    @staticmethod
    def pytest_semantic(stdout: bytes, _stderr: bytes, _generated: dict[str, bytes]) -> dict[str, Any]:
        text = stdout.decode(errors="replace")
        match = re.search(r"(\d+) passed(?:, (\d+) skipped)?.* in ([0-9.]+)s", text)
        return {"status": "PASS" if match else "FAIL", "passed": int(match.group(1)) if match else None,
                "skipped": int(match.group(2) or 0) if match else None,
                "reported_seconds": float(match.group(3)) if match else None}

    @classmethod
    def full_pytest_semantic(
        cls, stdout: bytes, stderr: bytes, generated: dict[str, bytes]
    ) -> dict[str, Any]:
        summary = cls.pytest_semantic(stdout, stderr, generated)
        raw = generated.get("full-junit.xml")
        if raw is None:
            return {**summary, "status": "FAIL", "reason": "JUnit artifact absent"}
        root = ET.fromstring(raw)
        suites = [root] if root.tag == "testsuite" else list(root.findall("./testsuite"))
        totals = {
            key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
        totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
        totals["time"] = sum(float(suite.attrib.get("time", "0")) for suite in suites)
        exact = totals == {
            "tests": 3730, "failures": 0, "errors": 0, "skipped": 5,
            "passed": 3725, "time": totals["time"],
        }
        cross_checked = summary.get("passed") == totals["passed"] and summary.get("skipped") == totals["skipped"]
        return {**summary, "status": "PASS" if exact and cross_checked else "FAIL",
                "junit": totals, "stdout_junit_cross_check": cross_checked}

    def execute(self) -> None:
        pytest = self.repo / ".venv/bin/pytest"
        ruff = self.repo / ".venv/bin/ruff"
        python = Path(sys.executable)
        git = Path("/usr/bin/git")
        gcc = Path("/usr/bin/gcc")
        loader = Path("/lib64/ld-linux-x86-64.so.2")
        helper = self.repo / "agent-services/freeze/f3cefe5/freeze_gate_tools.py"
        a3 = [
            "nix/a3-platform-bootstrap.nix", "nix/a3-platform-bootstrap-package.nix",
            "src/native/tgw_nix_observer_render_transport.c", "src/tgw/nix_observer_render_helper.py",
            "src/tgw/nix_observer_render_remote.py", "src/tgw/nixos_observer_render_evaluation.py",
            "src/tgw/platform_bootstrap.py", "src/tgw/bootstrap_authority.py",
            "src/tgw/deployment_runtime.py", "src/tgw/effect_handlers.py", "flake.nix",
        ]
        focused = [
            "tests/test_platform_bootstrap.py", "tests/test_bootstrap_authority.py",
            "tests/test_effect_handlers.py", "tests/test_deployment_runtime.py",
            "tests/test_nixos_observer_render_evaluation.py",
        ]
        junit = self.work / "full-junit.xml"
        self.run("full_pytest_junit", pytest, ["-q", f"--junitxml={junit}"], cwd=self.source,
                 env=self.env(), semantic=self.full_pytest_semantic, generated=[junit])
        self.run("focused_pytest", pytest, ["-q", *focused], cwd=self.source,
                 env=self.env(), semantic=self.pytest_semantic)
        lint_paths = [*a3[3:10], *focused]
        self.run("ruff_explicit", ruff, ["check", *lint_paths], cwd=self.source, env=self.env(),
                 semantic=lambda out, err, _gen: {"status": "PASS" if b"All checks passed" in out and not err else "FAIL",
                                           "checked_paths": lint_paths})
        compile_paths = [str(self.source / item) for item in a3[3:10]]
        self.run("py_compile_explicit", python, ["-m", "py_compile", *compile_paths], cwd=self.source,
                 env=self.env(), semantic=self.pass_empty)
        self.run("git_diff_check", git,
                 ["-c", f"safe.directory={self.source}", "diff", "--check", f"{SOURCE_COMMIT}^", SOURCE_COMMIT, "--", *a3],
                 cwd=self.source, env=self.env(), semantic=self.pass_empty)

        native_source = self.source / "src/native/tgw_nix_observer_render_transport.c"
        native_werror = self.work / "native-werror"
        self.run("native_gcc_werror", gcc,
                 ["-Wall", "-Wextra", "-Werror", "-o", str(native_werror),
                  "-x", "c", str(native_source), "-x", "none", "-lcrypto"],
                 cwd=self.source, env=self.env(), semantic=self.pass_empty,
                 inputs=[native_source], generated=[native_werror])
        maximal = "/" + "a" * 4094
        config_values = [
            ("schema", "tgw-nixos-observer-render-wrapper/v2"), ("uid", str(os.getuid())),
            ("gid", str(os.getgid())), ("python", maximal), ("python_sha256", "sha256:" + "1" * 64),
            ("ip", maximal), ("ip_sha256", "sha256:" + "2" * 64), ("bootstrap", maximal),
            ("bootstrap_sha256", "sha256:" + "3" * 64), ("helper", maximal),
            ("helper_sha256", "sha256:" + "4" * 64), ("wrapper_sha256", "sha256:" + "5" * 64),
            ("request_sha256", "sha256:" + "6" * 64),
            ("prerequisite_receipt_sha256", "sha256:" + "7" * 64), ("signing_key", maximal),
            ("public_key_sha256", "sha256:" + "8" * 64), ("packet_magic_hex", "5447574132504b54"),
            ("packet_version", "1"), ("max_output_bytes", "16777216"), ("python_exe", maximal),
        ]
        valid_config = self.work / "native-positive.conf"
        invalid_config = self.work / "native-negative.conf"
        for prior in (valid_config, invalid_config):
            if prior.exists():
                os.chmod(prior, 0o644)
        valid_config.write_text("".join(f"{key}={value}\n" for key, value in config_values))
        invalid_config.write_text(valid_config.read_text() + "uid=1\n")
        os.chmod(valid_config, 0o444)
        os.chmod(invalid_config, 0o444)
        sanitizer_positive = self.work / "native-sanitizer-positive"
        sanitizer_negative = self.work / "native-sanitizer-negative"
        common = ["-Wall", "-Wextra", "-Werror", "-DTGW_RENDER_TEST_BUILD",
                  "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-x", "c"]
        self.run("native_asan_ubsan_build_positive", gcc,
                 [*common, f'-DTGW_RENDER_WRAPPER_CONFIG="{valid_config}"', "-o", str(sanitizer_positive),
                  str(native_source), "-x", "none", "-lcrypto"],
                 cwd=self.source, env=self.env(), semantic=self.pass_empty,
                 inputs=[native_source, valid_config], generated=[sanitizer_positive])
        self.run("native_asan_ubsan_build_negative", gcc,
                 [*common, f'-DTGW_RENDER_WRAPPER_CONFIG="{invalid_config}"', "-o", str(sanitizer_negative),
                  str(native_source), "-x", "none", "-lcrypto"],
                 cwd=self.source, env=self.env(), semantic=self.pass_empty,
                 inputs=[native_source, invalid_config], generated=[sanitizer_negative])
        sanenv = self.env(TGW_RENDER_TEST_PARSE_ONLY="1", ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
                          UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        self.run("native_sanitizer_positive", sanitizer_positive, [], cwd=self.source, env=sanenv,
                 semantic=self.pass_empty, inputs=[valid_config, native_source])
        code = ("import json,subprocess,sys; p=subprocess.run(sys.argv[1:],env={"
                "'TGW_RENDER_TEST_PARSE_ONLY':'1','ASAN_OPTIONS':'detect_leaks=1:abort_on_error=1',"
                "'UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'},stdout=subprocess.PIPE,stderr=subprocess.PIPE); "
                "sys.stdout.buffer.write(json.dumps({'child_rc':p.returncode,'stderr':p.stderr.decode(errors='replace')},sort_keys=True).encode()+b'\\n'); "
                "raise SystemExit(0 if p.returncode==125 and b'configuration' in p.stderr else 1)")
        self.run("native_sanitizer_negative", python,
                 ["-c", code, str(sanitizer_negative)], cwd=self.source, env=self.env(),
                 semantic=lambda out, _err, _gen: {"status": "PASS" if json.loads(out)["child_rc"] == 125 else "FAIL",
                                             "expected_child_rc": 125, "observed_child_rc": json.loads(out)["child_rc"],
                                             "condition": "duplicate key rejected"},
                 inputs=[sanitizer_negative, invalid_config, native_source])

        graph = self.work / "plan-graph.json"
        solution = self.work / "plan-solution.json"
        protected_runtime_source, _ = self.protect_tree(self.source / "src")
        plan_env = self.env(PYTHONPATH=str(protected_runtime_source))
        self.run("plan_graph_generation", python,
                 [str(helper), "generate-graph", "--execution",
                  str(self.plan / "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml"),
                  "--plan-commit", PLAN_COMMIT,
                  "--catalog", str(self.source / "agent-services/catalogs/governed-execution-platform-v1.json"),
                  "--output", str(graph), "--runtime-source-tree", str(protected_runtime_source)],
                 cwd=self.source, env=plan_env,
                 semantic=lambda _o, _e, gen: {
                     "status": "PASS" if json.loads(gen["plan-graph.json"])["plan_commit"] == PLAN_COMMIT else "FAIL",
                                          "plan_commit": PLAN_COMMIT},
                 inputs=[helper, self.plan / "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
                         self.source / "agent-services/catalogs/governed-execution-platform-v1.json"],
                 input_trees=[protected_runtime_source],
                 generated=[graph])
        protected_luet = self.store / LUET_SHA256
        luet_exec = self.work / "luet-protected-exec"
        if luet_exec.exists():
            os.chmod(luet_exec, 0o755)
        luet_exec.write_text(
            "#!/bin/sh\nexec /lib64/ld-linux-x86-64.so.2 "
            + str(protected_luet)
            + ' "$@"\n'
        )
        os.chmod(luet_exec, 0o555)
        self.run("luet_version", loader, [str(protected_luet), "--version"], cwd=self.source, env=self.env(),
                 semantic=lambda out, _err, _gen: {"status": "PASS" if out == b"luet version 0.9.26-g \n" else "FAIL",
                                             "version": out.decode().strip()}, inputs=[protected_luet])
        luet_tree = self.work / "luet-tree"
        self.run("plan_luet_tree_generation", python,
                 [str(helper), "generate-luet-tree", "--graph", str(graph), "--output", str(luet_tree),
                  "--runtime-source-tree", str(protected_runtime_source)],
                 cwd=self.source, env=plan_env, semantic=self.pass_empty, inputs=[helper, graph],
                 input_trees=[protected_runtime_source])
        protected_luet_tree, _ = self.protect_tree(luet_tree)
        raw = self.run("luet_raw_package_list", loader,
                 [str(protected_luet), "tree", "pkglist", "--tree", str(protected_luet_tree), "--deps",
                  "--matches", "^tgw-target/closure$", "--output", "json"], cwd=self.source, env=self.env(),
                 semantic=lambda out, _err, _gen: {"status": "PASS" if isinstance(json.loads(out).get("packages"), list) else "FAIL",
                                             "package_count": len(json.loads(out).get("packages", [])),
                                             "raw_cli": True}, inputs=[protected_luet],
                 input_trees=[protected_luet_tree])
        self.run("plan_solution_generation", python,
                 [str(helper), "generate-solution", "--graph", str(graph),
                  "--luet", str(luet_exec), "--output", str(solution),
                  "--runtime-source-tree", str(protected_runtime_source)],
                 cwd=self.source, env=plan_env,
                 semantic=lambda _o, _e, gen: {
                     "status": "PASS" if json.loads(gen["plan-solution.json"]).get("solution_hash") == PLAN_SOLUTION else "FAIL",
                     "solution_hash": json.loads(gen["plan-solution.json"]).get("solution_hash"),
                     "closure_hash": json.loads(gen["plan-solution.json"]).get("closure_hash")},
                 inputs=[helper, luet_exec, protected_luet, graph],
                 input_trees=[protected_runtime_source],
                 generated=[solution])
        self.run("plan_solution_verification", python,
                 [str(helper), "verify-solution", "--solution", str(solution), "--plan-commit", PLAN_COMMIT,
                  "--runtime-source-tree", str(protected_runtime_source)],
                 cwd=self.source, env=plan_env,
                 semantic=lambda out, _err, _gen: {"status": "PASS" if json.loads(out).get("status") == "PASS" else "FAIL",
                                             **json.loads(out)}, inputs=[helper, solution],
                 input_trees=[protected_runtime_source])
        self.run("luet_derived_tgw_receipt", python,
                 [str(helper), "derive-luet-receipt", "--graph", str(graph),
                  "--luet", str(luet_exec), "--luet-sha256", "sha256:" + LUET_SHA256,
                  "--source-commit", SOURCE_COMMIT, "--source-tree", SOURCE_TREE,
                  "--runtime-source-tree", str(protected_runtime_source)],
                 cwd=self.source, env=plan_env,
                 semantic=lambda out, _err, _gen: {"status": "PASS" if json.loads(out).get("status") == "AGREEMENT" else "FAIL",
                                             "conformance": json.loads(out).get("status"),
                                             "raw_cli_record": raw["unsigned_sha256"]},
                 inputs=[helper, luet_exec, protected_luet, graph],
                 input_trees=[protected_runtime_source])
        integrity_tests = self.repo / "agent-services/freeze/f3cefe5/test_freeze_evidence.py"
        integrity_snapshot_source = self.work / "integrity-test-snapshot-source"
        if integrity_snapshot_source.exists():
            for prior in sorted(integrity_snapshot_source.iterdir()):
                prior.chmod(0o644)
                prior.unlink()
        else:
            integrity_snapshot_source.mkdir()
        integrity_snapshot_test = integrity_snapshot_source / "test_freeze_evidence.py"
        integrity_snapshot_runner = integrity_snapshot_source / "run_freeze_gates.py"
        integrity_snapshot_test.write_bytes(integrity_tests.read_bytes())
        integrity_snapshot_runner.write_bytes(Path(__file__).resolve().read_bytes())
        protected_integrity_tree, _ = self.protect_tree(integrity_snapshot_source)
        protected_integrity_test = protected_integrity_tree / "test_freeze_evidence.py"
        integrity_env = self.env(PYTHONPATH=str(protected_runtime_source))
        integrity_basetemp = self.tmp / "integrity-basetemp"
        if integrity_basetemp.exists():
            shutil.rmtree(integrity_basetemp)
        integrity_expression = (
            "swapped_executable_path_is_fail_closed or post_use_executable_mutation "
            "or input_and_generated_path_replacements or protected_tree_transient"
        )
        self.run(
            "freeze_integrity_adversarial_tests", pytest,
            ["-q", str(protected_integrity_test), "--override-ini", f"cache_dir={self.tmp}/integrity-cache",
             "--basetemp", str(integrity_basetemp),
             "-k", integrity_expression],
            cwd=self.repo, env=integrity_env, semantic=self.pytest_semantic,
            input_trees=[protected_integrity_tree, protected_runtime_source],
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--luet", type=Path, required=True)
    args = parser.parse_args()
    FreezeRunner(args.source, args.plan, args.repo, args.output, args.store, args.luet).execute()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

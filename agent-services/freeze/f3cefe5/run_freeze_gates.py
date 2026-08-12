#!/usr/bin/env python3
"""Execute and record the literal f3cefe5 freeze gate matrix.

The runner deliberately uses a cleared environment for each gate and executes
the opened executable through /proc/self/fd.  Gate outputs are copied, without
overwrite, to the root-owned content-addressed evidence store.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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


def metadata(path: Path) -> dict[str, Any]:
    held = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        observed = os.fstat(held)
        raw_hash = hashlib.sha256()
        while raw := os.read(held, 1024 * 1024):
            raw_hash.update(raw)
    finally:
        os.close(held)
    return {
        "path": str(path), "sha256": "sha256:" + raw_hash.hexdigest(),
        "size": observed.st_size, "dev": observed.st_dev, "inode": observed.st_ino,
        "uid": observed.st_uid, "gid": observed.st_gid,
        "mode": f"{stat.S_IMODE(observed.st_mode):04o}", "nlink": observed.st_nlink,
    }


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

    def protect_file(self, path: Path) -> dict[str, Any]:
        return self.protect_bytes(path.read_bytes())

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
        env: dict[str, str], semantic: Callable[[bytes, bytes], dict[str, Any]],
        inputs: list[Path] | None = None, generated: list[Path] | None = None,
    ) -> dict[str, Any]:
        executable = executable.resolve()
        exe_fd = os.open(executable, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        exe_meta = metadata(executable)
        before = timestamp()
        started = time.monotonic_ns()
        try:
            completed = subprocess.run(
                [f"/proc/self/fd/{exe_fd}", *args], cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                pass_fds=(exe_fd,), timeout=300,
            )
        finally:
            os.close(exe_fd)
        ended = time.monotonic_ns()
        after = timestamp()
        stdout = self.protect_bytes(completed.stdout)
        stderr = self.protect_bytes(completed.stderr)
        result = semantic(completed.stdout, completed.stderr)
        if completed.returncode != 0 or result.get("status") != "PASS":
            raise RuntimeError(f"gate {gate_id} HOLD rc={completed.returncode} semantic={result}")
        generated_refs = []
        for path in generated or []:
            protected = self.protect_file(path)
            protected.update({"role": path.name, "source_path": str(path)})
            generated_refs.append(protected)
        record: dict[str, Any] = {
            "schema": "tgw-freeze-execution-record/v1", "gate_id": gate_id,
            "unsigned_hash_scheme": "sha256 of canonical JSON excluding unsigned_sha256; final file sha256 is external in catalog",
            "source": {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "status": "CLEAN_DETACHED"},
            "executable": exe_meta, "held_executable_via_proc_fd": True,
            "argv": [str(executable), *args],
            "environment": {"clear_inherited": True, "values": env}, "cwd": str(cwd),
            "started_at": before, "ended_at": after, "duration_ns": ended - started,
            "rc": completed.returncode, "stdout": stdout, "stderr": stderr,
            "inputs": [metadata(path.resolve()) for path in inputs or []],
            "generated_artifacts": generated_refs, "semantic": result,
        }
        record["unsigned_sha256"] = "sha256:" + digest(canonical(record))
        target = self.records / f"{gate_id}.json"
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        return record

    @staticmethod
    def pass_empty(stdout: bytes, stderr: bytes) -> dict[str, Any]:
        return {"status": "PASS", "stdout_empty": not stdout, "stderr_empty": not stderr}

    @staticmethod
    def pytest_semantic(stdout: bytes, _stderr: bytes) -> dict[str, Any]:
        text = stdout.decode(errors="replace")
        match = re.search(r"(\d+) passed(?:, (\d+) skipped)?.* in ([0-9.]+)s", text)
        return {"status": "PASS" if match else "FAIL", "passed": int(match.group(1)) if match else None,
                "skipped": int(match.group(2) or 0) if match else None,
                "reported_seconds": float(match.group(3)) if match else None}

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
                 env=self.env(),
                 semantic=lambda out, err: {
                     **self.pytest_semantic(out, err),
                     "junit": {
                         key: ET.parse(junit).getroot().attrib.get(key)
                         for key in ("tests", "failures", "errors", "skipped", "time")
                     },
                 }, generated=[junit])
        self.run("focused_pytest", pytest, ["-q", *focused], cwd=self.source,
                 env=self.env(), semantic=self.pytest_semantic)
        lint_paths = [*a3[3:10], *focused]
        self.run("ruff_explicit", ruff, ["check", *lint_paths], cwd=self.source, env=self.env(),
                 semantic=lambda out, err: {"status": "PASS" if b"All checks passed" in out and not err else "FAIL",
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
                 ["-Wall", "-Wextra", "-Werror", "-o", str(native_werror), str(native_source), "-lcrypto"],
                 cwd=self.source, env=self.env(), semantic=self.pass_empty, generated=[native_werror])
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
                  "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        self.run("native_asan_ubsan_build_positive", gcc,
                 [*common, f'-DTGW_RENDER_WRAPPER_CONFIG="{valid_config}"', "-o", str(sanitizer_positive), str(native_source), "-lcrypto"],
                 cwd=self.source, env=self.env(), semantic=self.pass_empty, generated=[sanitizer_positive])
        self.run("native_asan_ubsan_build_negative", gcc,
                 [*common, f'-DTGW_RENDER_WRAPPER_CONFIG="{invalid_config}"', "-o", str(sanitizer_negative), str(native_source), "-lcrypto"],
                 cwd=self.source, env=self.env(), semantic=self.pass_empty, generated=[sanitizer_negative])
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
                 semantic=lambda out, _err: {"status": "PASS" if json.loads(out)["child_rc"] == 125 else "FAIL",
                                             "expected_child_rc": 125, "observed_child_rc": json.loads(out)["child_rc"],
                                             "condition": "duplicate key rejected"},
                 inputs=[sanitizer_negative, invalid_config, native_source])

        graph = self.work / "plan-graph.json"
        solution = self.work / "plan-solution.json"
        self.run("plan_graph_generation", python,
                 [str(helper), "generate-graph", "--plan-root", str(self.plan), "--plan-commit", PLAN_COMMIT,
                  "--catalog", str(self.source / "agent-services/catalogs/governed-execution-platform-v1.json"),
                  "--output", str(graph)], cwd=self.source, env=self.env(),
                 semantic=lambda _o, _e: {"status": "PASS" if json.loads(graph.read_text())["plan_commit"] == PLAN_COMMIT else "FAIL",
                                          "plan_commit": PLAN_COMMIT},
                 inputs=[helper, self.plan / "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
                         self.source / "agent-services/catalogs/governed-execution-platform-v1.json"],
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
                 semantic=lambda out, _err: {"status": "PASS" if out == b"luet version 0.9.26-g \n" else "FAIL",
                                             "version": out.decode().strip()}, inputs=[protected_luet])
        luet_tree = self.work / "luet-tree"
        self.run("plan_luet_tree_generation", python,
                 [str(helper), "generate-luet-tree", "--graph", str(graph), "--output", str(luet_tree)],
                 cwd=self.source, env=self.env(), semantic=self.pass_empty, inputs=[helper, graph])
        raw = self.run("luet_raw_package_list", loader,
                 [str(protected_luet), "tree", "pkglist", "--tree", str(luet_tree), "--deps",
                  "--matches", "^tgw-target/closure$", "--output", "json"], cwd=self.source, env=self.env(),
                 semantic=lambda out, _err: {"status": "PASS" if isinstance(json.loads(out).get("packages"), list) else "FAIL",
                                             "package_count": len(json.loads(out).get("packages", [])),
                                             "raw_cli": True}, inputs=[protected_luet])
        self.run("plan_solution_generation", python,
                 [str(self.source / "scripts/solve_governed_platform.py"), "--plan-root", str(self.plan),
                  "--catalog", str(self.source / "agent-services/catalogs/governed-execution-platform-v1.json"),
                  "--luet", str(luet_exec), "--output", str(solution)], cwd=self.source, env=self.env(),
                 semantic=lambda _o, _e: {"status": "PASS" if json.loads(solution.read_text()).get("solution_hash") == PLAN_SOLUTION else "FAIL",
                                          "solution_hash": json.loads(solution.read_text()).get("solution_hash"),
                                          "closure_hash": json.loads(solution.read_text()).get("closure_hash")},
                 inputs=[self.source / "scripts/solve_governed_platform.py", luet_exec,
                         protected_luet, graph,
                         self.source / "agent-services/catalogs/governed-execution-platform-v1.json"],
                 generated=[solution])
        self.run("plan_solution_verification", python,
                 [str(helper), "verify-solution", "--solution", str(solution), "--plan-commit", PLAN_COMMIT],
                 cwd=self.source, env=self.env(),
                 semantic=lambda out, _err: {"status": "PASS" if json.loads(out).get("status") == "PASS" else "FAIL",
                                             **json.loads(out)}, inputs=[helper, solution])
        self.run("luet_derived_tgw_receipt", python,
                 [str(self.source / "scripts/run_luet_conformance.py"), "--graph", str(graph),
                  "--luet", str(luet_exec), "--repo", str(self.source), "--candidate", SOURCE_COMMIT],
                 cwd=self.source, env=self.env(),
                 semantic=lambda out, _err: {"status": "PASS" if json.loads(out[out.index(b'{'):]).get("status") == "AGREEMENT" else "FAIL",
                                             "conformance": json.loads(out[out.index(b'{'):]).get("status"),
                                             "raw_cli_record": raw["unsigned_sha256"]},
                 inputs=[self.source / "scripts/run_luet_conformance.py", luet_exec,
                         protected_luet, graph])


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

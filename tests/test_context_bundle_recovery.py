from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import textwrap
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts/tgw_context_debian_stdio.py"

import tgw  # noqa: E402

tgw.__path__.insert(0, str(ROOT / "src/tgw"))
from tgw.current_context_snapshot import (  # noqa: E402
    MAX_SNAPSHOT_BYTES,
    MAX_TASK_BYTES,
    CurrentContextError,
    publish_bytes,
    serialized_bytes,
)
from tgw.current_context_snapshot import (  # noqa: E402
    build as build_context_snapshot,
)
from tgw.current_context_snapshot import (  # noqa: E402
    parse as parse_context_snapshot,
)
from tgw.current_context_snapshot import (  # noqa: E402
    parse_bytes as parse_context_bytes,
)

_DOCTOR_SPEC = importlib.util.spec_from_file_location(
    "todo_1738_doctor_cli", ROOT / "src/tgw/doctor_cli.py"
)
assert _DOCTOR_SPEC and _DOCTOR_SPEC.loader
doctor_cli = importlib.util.module_from_spec(_DOCTOR_SPEC)
sys.modules[_DOCTOR_SPEC.name] = doctor_cli
_DOCTOR_SPEC.loader.exec_module(doctor_cli)


def test_worktree_is_the_exact_environment_bound_candidate() -> None:
    expected_commit = os.environ.get("TGW_EXPECTED_CANDIDATE_COMMIT")
    if expected_commit is None:
        pytest.skip("exact candidate binding is supplied by the review harness")
    expected_tree = os.environ.get("TGW_EXPECTED_CANDIDATE_TREE")
    assert expected_tree is not None
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    ).stdout

    assert commit == expected_commit
    assert tree == expected_tree
    assert status == ""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _legacy_canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _snapshot(
    source_commit: str = "b" * 40, source_tree: str = "c" * 40
) -> dict[str, Any]:
    plan_commit = "a" * 40
    capability = "PP-WORKFLOW-001"
    task = {
        "schema": "tgw-current-task/v1",
        "plan": {"approved_commit": plan_commit},
        "implementation": {
            "development_source": {
                "commit": source_commit,
                "next_leaf": capability,
            }
        },
    }
    cursor = {
        "schema": "tgw-plan-execution-cycle-cursor/v1",
        "plan_commit": plan_commit,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "resolved": {"next_treatment": f"todo:{capability}"},
    }
    value = {
        "schema": "tgw-current-context-snapshot/v1",
        "plan_commit": plan_commit,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "active_capability": capability,
        "active_treatment": f"todo:{capability}",
        "task": task,
        "cursor": cursor,
    }
    value["snapshot_sha256"] = "sha256:" + hashlib.sha256(_legacy_canonical(value)).hexdigest()
    return value


def test_snapshot_wire_contract_round_trips_non_ascii_newlines_and_history() -> None:
    legacy = _snapshot()
    task = dict(legacy["task"])
    task.update({"objective": "café\n東京", "archived_history": [{"event": "x" * 1000}] * 4000})
    compact = build_context_snapshot(task, legacy["cursor"])
    raw = serialized_bytes(compact)

    assert raw == _canonical(compact) + b"\n"
    assert len(raw) <= MAX_SNAPSHOT_BYTES
    assert "task" not in compact
    assert parse_context_bytes(raw)["task"] == task


def test_predecessor_inline_non_ascii_keeps_exact_legacy_hash_and_wire() -> None:
    legacy = _snapshot()
    legacy["task"]["operator_note"] = "café 東京"
    body = dict(legacy)
    body.pop("snapshot_sha256")
    legacy["snapshot_sha256"] = "sha256:" + hashlib.sha256(_legacy_canonical(body)).hexdigest()
    wire = json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    assert b"caf\\u00e9" in wire
    assert parse_context_bytes(wire)["task"] == legacy["task"]
    utf8_wire = _canonical(legacy) + b"\n"
    assert utf8_wire != wire
    with pytest.raises(CurrentContextError, match="not canonical"):
        parse_context_bytes(utf8_wire)


def test_real_publisher_emits_only_publish_bytes_and_enforces_task_boundary(
    tmp_path: Path,
) -> None:
    legacy = _snapshot()
    task = dict(legacy["task"], operator_note="café 東京")
    task_path = tmp_path / "task.json"
    cursor_path = tmp_path / "cursor.json"
    output = tmp_path / "snapshot.json"
    task_path.write_bytes(_canonical(task))
    cursor_path.write_bytes(_canonical(legacy["cursor"]))
    command = [str(ROOT / "scripts/tgw_context_publish.py"), "--task", str(task_path), "--cursor", str(cursor_path), "--output", str(output)]
    completed = subprocess.run(
        command, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if os.geteuid() != 0:
        assert completed.returncode != 0
        assert "must run as root" in completed.stderr
        pytest.skip("root-only publisher success path requires root")
    assert completed.returncode == 0, completed.stderr
    assert output.read_bytes() == publish_bytes(task, legacy["cursor"])

    base = dict(legacy["task"], padding="")
    overhead = len(_canonical(base))
    exact = dict(base, padding="x" * (MAX_TASK_BYTES - overhead))
    assert len(_canonical(exact)) == MAX_TASK_BYTES
    publish_bytes(exact, legacy["cursor"])
    with pytest.raises(CurrentContextError, match="current task exceeds"):
        publish_bytes(dict(exact, padding=exact["padding"] + "x"), legacy["cursor"])


def test_real_publisher_isolated_bootstrap_rejects_cwd_and_pythonpath_shadowing(
    tmp_path: Path,
) -> None:
    legacy = _snapshot()
    task = tmp_path / "task.json"
    cursor = tmp_path / "cursor.json"
    output = tmp_path / "snapshot.json"
    hostile_cwd = tmp_path / "cwd"
    hostile_pythonpath = tmp_path / "pythonpath"
    hostile_cwd.mkdir()
    hostile_pythonpath.mkdir()
    for directory in (hostile_cwd, hostile_pythonpath):
        for module in ("argparse", "json", "pathlib", "tempfile"):
            (directory / f"{module}.py").write_text(
                "raise RuntimeError('ambient shadow module executed')\n"
            )
    task.write_bytes(_canonical(legacy["task"]))
    cursor.write_bytes(_canonical(legacy["cursor"]))

    completed = subprocess.run(
        [
            str(ROOT / "scripts/tgw_context_publish.py"),
            "--task",
            str(task),
            "--cursor",
            str(cursor),
            "--output",
            str(output),
        ],
        cwd=hostile_cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(hostile_pythonpath)},
    )
    if os.geteuid() != 0:
        assert completed.returncode != 0
        assert "must run as root" in completed.stderr
        pytest.skip("root-only publisher success path requires root")
    assert completed.returncode == 0, completed.stderr
    assert output.read_bytes() == publish_bytes(legacy["task"], legacy["cursor"])


def test_publisher_retains_interpreter_zip_stdlib_and_dynload_before_runtime() -> None:
    script = (ROOT / "scripts/tgw_context_publish.py").read_text()
    bootstrap = (
        "exec /opt/TGW/.venvs/controller/bin/python3 -I -S \"$0\" \"$@\""
    )
    assert bootstrap in script
    completed = subprocess.run(
        [
            "/opt/TGW/.venvs/controller/bin/python3",
            "-I",
            "-S",
            "-c",
            "import json,sys; print(json.dumps(sys.path))",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "/hostile/site-packages"},
    )
    paths = json.loads(completed.stdout)
    assert paths[0].endswith(".zip")
    assert any(path.endswith("/lib-dynload") for path in paths)
    assert not any("site-packages" in path for path in paths)
    assert "/hostile/site-packages" not in paths


def test_publisher_snapshot_api_keeps_zip_import_ahead_of_exact_runtime(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    package = runtime / "tgw"
    package.mkdir(parents=True)
    archive = tmp_path / "stdlib.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("publisher_zip_probe.py", "VALUE = 'zip-stdlib'\n")
    (package / "current_context_snapshot.py").write_text(
        "import publisher_zip_probe\n"
        "MAX_TASK_BYTES = 17\n"
        "def publish_bytes(task, cursor):\n"
        "    return publisher_zip_probe.VALUE.encode()\n"
    )
    helper = textwrap.dedent(
        f"""
        import os, runpy, sys
        archive = {str(archive)!r}
        runtime = {str(runtime)!r}
        original = [*sys.path]
        sys.path.append(archive)
        os.environ['PYTHONPATH'] = '/hostile/site-packages'
        namespace = runpy.run_path({str(ROOT / 'scripts/tgw_context_publish.py')!r})
        api = namespace['_snapshot_api']
        api.__globals__['_selected_runtime'] = lambda: namespace['Path'](runtime)
        maximum, publish = api()
        assert maximum == 17
        assert publish({{}}, {{}}) == b'zip-stdlib'
        assert sys.path == [*original, archive]
        assert os.environ['PYTHONPATH'] == '/hostile/site-packages'
        """
    )
    completed = subprocess.run(
        ["/opt/TGW/.venvs/controller/bin/python3", "-I", "-S", "-c", helper],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "/ignored/by/isolated/mode"},
    )
    assert completed.returncode == 0, completed.stderr


def test_real_publisher_subprocess_root_guard(tmp_path: Path) -> None:
    legacy = _snapshot()
    task = tmp_path / "task.json"
    cursor = tmp_path / "cursor.json"
    output = tmp_path / "snapshot.json"
    task.write_bytes(_canonical(legacy["task"]))
    cursor.write_bytes(_canonical(legacy["cursor"]))
    command = [str(ROOT / "scripts/tgw_context_publish.py"), "--task", str(task), "--cursor", str(cursor), "--output", str(output)]
    if os.geteuid() == 0:
        command = ["/usr/sbin/runuser", "-u", "nobody", "--", *command]
    completed = subprocess.run(command, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"})
    assert completed.returncode != 0
    assert "must run as root" in completed.stderr
    assert not output.exists()


def test_snapshot_rejects_dual_task_trailing_stream_tamper_and_malformed_hash() -> None:
    legacy = _snapshot()
    compact = build_context_snapshot(legacy["task"], legacy["cursor"])
    dual = dict(compact, task=legacy["task"])
    body = dict(dual)
    body.pop("snapshot_sha256")
    dual["snapshot_sha256"] = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    with pytest.raises(CurrentContextError, match="two task"):
        parse_context_snapshot(dual)
    with pytest.raises(CurrentContextError, match="invalid"):
        parse_context_bytes(serialized_bytes(compact) + b"{}\n")
    for projection in (
        {**compact["task_projection"], "sha256": "sha256:not-a-hash"},
        {**compact["task_projection"], "data": compact["task_projection"]["data"] + "!"},
    ):
        changed = dict(compact, task_projection=projection)
        body = dict(changed)
        body.pop("snapshot_sha256")
        changed["snapshot_sha256"] = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
        with pytest.raises(CurrentContextError):
            parse_context_snapshot(changed)


def test_task_expansion_rejects_bomb_truncation_and_trailing_zlib_stream() -> None:
    legacy = _snapshot()
    compact = build_context_snapshot(legacy["task"], legacy["cursor"])
    cases = [
        zlib.compress(b'"' + b"x" * (MAX_TASK_BYTES + 1) + b'"'),
        zlib.compress(_canonical(legacy["task"]))[:-1],
        zlib.compress(_canonical(legacy["task"])) + zlib.compress(b"{}"),
    ]
    for compressed in cases:
        projection = dict(compact["task_projection"], data=base64.b64encode(compressed).decode("ascii"))
        changed = dict(compact, task_projection=projection)
        body = dict(changed)
        body.pop("snapshot_sha256")
        changed["snapshot_sha256"] = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
        with pytest.raises(CurrentContextError):
            parse_context_snapshot(changed)

    alternate = zlib.compress(_canonical(legacy["task"]), 1)
    projection = dict(
        compact["task_projection"],
        data=base64.b64encode(alternate).decode("ascii"),
    )
    changed = dict(compact, task_projection=projection)
    body = dict(changed)
    body.pop("snapshot_sha256")
    changed["snapshot_sha256"] = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    with pytest.raises(CurrentContextError, match="canonical zlib level 9"):
        parse_context_snapshot(changed)


def test_incompressible_snapshot_is_rejected_at_exact_wire_boundary() -> None:
    legacy = _snapshot()
    task = dict(legacy["task"])
    # Hex digests are deterministic, high-entropy task history that does not compress away.
    task["archived_history"] = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(20_000)]
    with pytest.raises(CurrentContextError, match="256 KiB"):
        build_context_snapshot(task, legacy["cursor"])


def _launcher_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tamper_runtime: str | None = None,
    preflight_task_updates: dict[str, Any] | None = None,
    legacy_parser_api: str | None = None,
    snapshot_raw: bytes | None = None,
    snapshot_task_updates: dict[str, Any] | None = None,
) -> ModuleType:
    context_source = tmp_path / "source"
    context_source.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=context_source,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=context_source, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=context_source,
        check=True,
    )
    source_runtime = context_source / "src/tgw"
    source_runtime.mkdir(parents=True)
    for name in (
        "__init__.py",
        "context_mcp_server.py",
        "current_context_snapshot.py",
        "dependency.py",
        "local_context_runtime.py",
    ):
        if name == "current_context_snapshot.py":
            if legacy_parser_api is not None:
                source = subprocess.run(
                    ["git", "show", "99416bfb:src/tgw/current_context_snapshot.py"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout + legacy_parser_api
                source_runtime.joinpath(name).write_text(source, encoding="utf-8")
            else:
                shutil.copyfile(
                    ROOT / "src/tgw/current_context_snapshot.py", source_runtime / name
                )
        else:
            (source_runtime / name).write_text("# immutable test runtime\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=context_source, check=True)
    subprocess.run(
        ["git", "commit", "-m", "runtime"],
        cwd=context_source,
        check=True,
        capture_output=True,
    )
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"],
        cwd=context_source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    source_tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=context_source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    snapshot = _snapshot(source_commit, source_tree)
    if snapshot_task_updates is not None:
        snapshot["task"].update(snapshot_task_updates)
        body = dict(snapshot)
        body.pop("snapshot_sha256")
        snapshot["snapshot_sha256"] = (
            "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
        )
    preflight_fd = -1
    preflight_raw = b""
    real_fstat = os.fstat
    if preflight_task_updates is not None:
        preflight = json.loads(json.dumps(snapshot))
        preflight["task"].update(preflight_task_updates)
        body = dict(preflight)
        body.pop("snapshot_sha256")
        preflight["snapshot_sha256"] = (
            "sha256:" + hashlib.sha256(_legacy_canonical(body)).hexdigest()
        )
        preflight_path = tmp_path / "tgw-context-preflight.json"
        preflight_raw = _legacy_canonical(preflight) + b"\n"
        preflight_path.write_bytes(preflight_raw)
        preflight_path.chmod(0o400)
        preflight_fd = os.open(preflight_path, os.O_RDONLY)

        def root_bound_preflight_fstat(descriptor: int) -> Any:
            observed = real_fstat(descriptor)
            if descriptor != preflight_fd:
                return observed
            return SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_size=observed.st_size,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
                st_mode=observed.st_mode,
                st_uid=0,
                st_gid=0,
            )

        monkeypatch.setattr(os, "fstat", root_bound_preflight_fstat)
        monkeypatch.setenv("TGW_CONTEXT_PREFLIGHT_SNAPSHOT_FD", str(preflight_fd))
    releases = tmp_path / "releases"
    runtime = releases / snapshot["source_commit"] / "src/tgw"
    runtime.mkdir(parents=True)
    for name in (
        "__init__.py",
        "context_mcp_server.py",
        "current_context_snapshot.py",
        "dependency.py",
        "local_context_runtime.py",
    ):
        path = runtime / name
        shutil.copyfile(source_runtime / name, path)
        path.chmod(0o444)
    if tamper_runtime:
        tampered = runtime / tamper_runtime
        tampered.chmod(0o644)
        tampered.write_text("# tampered\n", encoding="utf-8")
        tampered.chmod(0o444)
    for directory in (runtime, runtime.parent, runtime.parent.parent):
        directory.chmod(0o555)
    current = tmp_path / "tgw-context-current.json"
    current.write_bytes(snapshot_raw if snapshot_raw is not None else _canonical(snapshot) + b"\n")
    current.chmod(0o444)
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    source = LAUNCHER.read_text(encoding="utf-8")
    replacements = {
        'RUNTIME_RELEASES = Path("/opt/TGW/tgw-lib/coding-runtime/releases")': (
            f"RUNTIME_RELEASES = Path({str(releases)!r})"
        ),
        'CONTEXT_SOURCE = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")': (
            f"CONTEXT_SOURCE = Path({str(context_source)!r})"
        ),
        'CATALOG = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")': (
            f"CATALOG = Path({str(catalog)!r})"
        ),
        'CURRENT_CONTEXT = Path("/opt/TGW/tgw-lib/config/tgw-context-current.json")': (
            f"CURRENT_CONTEXT = Path({str(current)!r})"
        ),
        "RUNTIME_OWNER_UID = 0": f"RUNTIME_OWNER_UID = {os.getuid()}",
        "RUNTIME_OWNER_GID = 0": f"RUNTIME_OWNER_GID = {os.getgid()}",
    }
    for old, new in replacements.items():
        assert old in source
        source = source.replace(old, new)
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid())
    module = ModuleType("context_launcher_test")
    module.__file__ = str(LAUNCHER)
    exec(compile(source, str(LAUNCHER), "exec"), module.__dict__)
    module._test_snapshot = snapshot
    module._test_snapshot_raw = _canonical(snapshot) + b"\n"
    module._test_preflight_raw = preflight_raw
    module._test_preflight_snapshot = (
        parse_context_bytes(preflight_raw) if preflight_raw else snapshot
    )
    module._test_preflight_fd = preflight_fd
    return module


def _server(snapshot: dict[str, Any]) -> SimpleNamespace:
    plan_tree = "d" * 40
    solution = "sha256:" + "e" * 64
    evidence_head = "f" * 40
    evidence_tree = "1" * 40
    freshness = "sha256:" + "2" * 64
    current_context = {
        key: snapshot[key]
        for key in (
            "active_capability",
            "active_treatment",
            "plan_commit",
            "source_commit",
            "source_tree",
            "snapshot_sha256",
        )
    }
    status = {
        "actor": "codex",
        "generation_status": {"state": "CURRENT"},
        "plan": {
            "approved_commit": snapshot["plan_commit"],
            "approved_tree": plan_tree,
            "approved_solution_hash": solution,
            "evidence_head": evidence_head,
            "evidence_tree": evidence_tree,
        },
        "source": {
            "commit": snapshot["source_commit"],
            "tree": snapshot["source_tree"],
        },
        "code_graph": {
            "commit": snapshot["source_commit"],
            "tree": snapshot["source_tree"],
            "freshness_hash": freshness,
        },
        "current_context": current_context,
    }

    class ContextError(RuntimeError):
        pass

    return SimpleNamespace(
        ContextError=ContextError,
        context_status=lambda: status,
        plan_graph=lambda task, receiver, operation, limit: {
            "task": task,
            "receiver": receiver,
            "limit": limit,
            "plan_commit": snapshot["plan_commit"],
            "plan_tree": plan_tree,
            "approved_solution_hash": solution,
            "current_context": current_context,
        },
        runbooks=lambda query, path, start, lines, limit, authority: {
            "query": query,
            "authority": authority,
            "revisions": [
                {
                    "authority": "canonical-plan-runbook",
                    "commit": evidence_head,
                    "tree": evidence_tree,
                },
                {
                    "authority": "committed-application-runbook",
                    "commit": snapshot["source_commit"],
                    "tree": snapshot["source_tree"],
                },
            ],
        },
        code_graph=lambda operation, query, limit: {
            "operation": operation,
            "limit": limit,
            "binding": {
                "commit": snapshot["source_commit"],
                "tree": snapshot["source_tree"],
                "freshness_hash": freshness,
            },
        },
        _json_call=lambda function: json.dumps(function(), sort_keys=True),
    )


def _selected(tmp_path: Path) -> dict[str, Any]:
    release = tmp_path / "release"
    launcher = release / "scripts/tgw_context_debian_stdio.py"
    publisher = release / "scripts/tgw_context_publish.py"
    runtime = release / "src/tgw"
    launcher.parent.mkdir(parents=True)
    runtime.mkdir(parents=True)
    launcher.write_bytes(b"#!/bin/sh\nexit 0\n")
    launcher.chmod(0o555)
    publisher.write_bytes(b"#!/bin/sh\nexit 0\n")
    publisher.chmod(0o555)
    modules = {}
    for name in (
        "context_mcp_server",
        "current_context_snapshot",
        "local_context_runtime",
    ):
        path = runtime / f"{name}.py"
        if name == "current_context_snapshot":
            shutil.copyfile(ROOT / "src/tgw/current_context_snapshot.py", path)
        else:
            path.write_text(f"# {name}\n", encoding="utf-8")
        path.chmod(0o444)
        modules[name] = path
    required = {"launcher": launcher, "publisher": publisher, **modules}
    selected = {
        "commit": "a" * 40,
        "release": release,
        "release_tree": {"tree": "b" * 40},
        "launcher": launcher,
        "publisher": publisher,
        "runtime_source": release / "src",
        "modules": modules,
        "hashes": {
            name: doctor_cli._file_hash(path) for name, path in required.items()
        },
    }
    selected["runtime_inventory"] = doctor_cli._context_runtime_inventory(
        selected["runtime_source"], uid=os.getuid(), gid=os.getgid()
    )
    return selected


def _doctor_paths(tmp_path: Path) -> doctor_cli.DoctorPaths:
    installed = tmp_path / "bin/tgw-context-mcp"
    installed.parent.mkdir(exist_ok=True)
    installed.write_bytes(b"old\n")
    installed.chmod(0o755)
    publisher = installed.parent / "tgw-context-publish"
    publisher.write_bytes(b"old publisher\n")
    publisher.chmod(0o755)
    snapshot = _snapshot()
    task_path = tmp_path / "context-input/current-task.json"
    cursor_path = tmp_path / "context-input/plan-cycle-cursor.json"
    snapshot_path = tmp_path / "config/tgw-context-current.json"
    task_path.parent.mkdir(parents=True)
    snapshot_path.parent.mkdir(parents=True)
    task_path.write_text(json.dumps(snapshot["task"]), encoding="utf-8")
    cursor_path.write_text(json.dumps(snapshot["cursor"]), encoding="utf-8")
    snapshot_path.write_bytes(_legacy_canonical(snapshot) + b"\n")
    return doctor_cli.DoctorPaths(
        context_snapshot=snapshot_path,
        context_task=task_path,
        context_cursor=cursor_path,
        context_launcher=installed,
        context_publisher=publisher,
        local_bin=installed.parent,
        context_generation_root=tmp_path / "context-entrypoints/generations",
        context_generation_pointer=tmp_path / "context-entrypoints/current",
        receipts=tmp_path / "receipts",
        context_install_uid=os.getuid(),
        context_install_gid=os.getgid(),
    )


_LAUNCHER_REPAIR_TESTS = {
    "test_doctor_context_launcher_check_and_receipted_atomic_repair",
    "test_generation_pointer_is_the_only_pair_visibility_boundary",
    "test_repair_rejects_hostile_preexisting_runtime_tree_before_pointer_cas",
    "test_context_launcher_repair_replay_preserves_selected_pointer",
    "test_context_launcher_adversarial_phase_replay_matrix",
    "test_context_launcher_replay_converges_one_shim_one_legacy",
    "test_first_migration_refuses_hostile_preexisting_generation",
    "test_shim_migration_keeps_pointer_on_exact_old_pair_until_final_switch",
    "test_doctor_context_launcher_refuses_a_concurrent_destination_change",
    "test_doctor_context_launcher_rolls_back_if_runtime_changes_after_replace",
    "test_doctor_context_launcher_failure_is_durably_receipted",
}


@pytest.fixture(autouse=True)
def _successful_launcher_probe_for_repair_fixtures(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if getattr(request.node, "originalname", request.node.name) in _LAUNCHER_REPAIR_TESTS:
        monkeypatch.setattr(
            doctor_cli,
            "_probe_context_stdio",
            lambda launcher, actor, expected, **_kwargs: {
                "launcher": str(launcher),
                "actor": actor,
                "generation": "CURRENT",
                "snapshot_sha256": expected["snapshot_sha256"],
            },
        )


def test_launcher_declares_only_narrow_bundle_and_immutable_release() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert (
        'RETIRED_TOOLS = ("tgw_context_bundle", "tgw_context_confirm_rebind")' in source
    )
    assert "challenge:" not in source
    assert "card_json:" not in source
    assert "grant_json:" not in source
    assert 'Path("/opt/TGW/tgw-lib/context-runtime/src")' not in source
    assert "RUNTIME_RELEASES / source_commit" in source
    assert 'def tgw_context_bundle(task: str = "current", limit: int = 12)' in source


def test_real_release_launcher_is_executable_with_trusted_bootstrap() -> None:
    trusted_bootstrap = (
        b"#!/opt/TGW/.venvs/controller/bin/python3\n"
        b'"""Launch the shared read-only TGW context MCP on the Debian development host."""\n'
    )

    assert LAUNCHER.read_bytes().startswith(trusted_bootstrap)
    assert LAUNCHER.stat().st_mode & stat.S_IXUSR

    tracked = subprocess.run(
        ["git", "ls-files", "--stage", "--", LAUNCHER.relative_to(ROOT)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(tracked) == 1
    mode, object_id, stage_and_path = tracked[0].split(maxsplit=2)
    stage, path = stage_and_path.split("\t", maxsplit=1)
    assert mode == "100755"
    assert len(object_id) == 40
    assert stage == "0"
    assert path == "scripts/tgw_context_debian_stdio.py"


def test_atomic_snapshot_rejects_a_non_git_source_tree() -> None:
    snapshot = _snapshot()
    snapshot["source_tree"] = "not-a-tree"
    snapshot["cursor"]["source_tree"] = "not-a-tree"
    body = dict(snapshot)
    body.pop("snapshot_sha256")
    snapshot["snapshot_sha256"] = (
        "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    )

    with pytest.raises(CurrentContextError, match="different Plan context"):
        parse_context_snapshot(snapshot)


def test_launcher_bootstrap_binds_the_atomic_snapshot_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_harness_actor", lambda: "codex")

    assert module.SERVER_SOURCE == Path(
        f"/proc/self/fd/{module._RUNTIME_RELEASE_DESCRIPTOR}/src"
    )
    assert module.RUNTIME_RELEASE == (
        tmp_path / "releases" / module._test_snapshot["source_commit"]
    )
    assert (
        module._current_context()["source_commit"]
        == module._test_snapshot["source_commit"]
    )
    current_task = json.loads(module._current_task())
    assert current_task["implementation"] == module._test_snapshot["task"]["implementation"]
    for key, expected in module._test_snapshot["task"].items():
        assert current_task[key] == expected
    assert "durable_recovery_projection" in current_task
    assert "task_projection" not in current_task


def test_launcher_preflight_descriptor_is_process_lifetime_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(
        tmp_path,
        monkeypatch,
        preflight_task_updates={"preflight_marker": "staged-not-live"},
    )
    descriptor = module._test_preflight_fd
    try:
        monkeypatch.setattr(module, "_harness_actor", lambda: "codex")
        assert "TGW_CONTEXT_PREFLIGHT_SNAPSHOT_FD" not in os.environ
        assert module._STARTUP_CONTEXT_RAW == module._test_preflight_raw
        assert module._STARTUP_CONTEXT_RAW != module.CURRENT_CONTEXT.read_bytes()
        assert module._protected_snapshot_raw() == module._STARTUP_CONTEXT_RAW
        assert module._protected_snapshot_raw() == module._STARTUP_CONTEXT_RAW

        current = module._current_context()
        task = json.loads(module._current_task())
        bundle = json.loads(
            module.context_server_bundle(
                _server(module._test_preflight_snapshot), "current", 7
            )
        )

        assert current["task"]["preflight_marker"] == "staged-not-live"
        assert task["preflight_marker"] == "staged-not-live"
        assert bundle["status"]["current_context"]["snapshot_sha256"] == (
            module._test_preflight_snapshot["snapshot_sha256"]
        )
        assert bundle["current_context"]["snapshot_sha256"] == (
            module._test_preflight_snapshot["snapshot_sha256"]
        )
        assert module._test_snapshot["snapshot_sha256"] != (
            module._test_preflight_snapshot["snapshot_sha256"]
        )
    finally:
        os.close(descriptor)


def test_launcher_preflight_descriptor_reuse_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(
        tmp_path, monkeypatch, preflight_task_updates={"preflight_marker": "selected"}
    )
    descriptor = module._test_preflight_fd
    os.close(descriptor)
    replacement = os.open(
        module.CURRENT_CONTEXT, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    duplicate = replacement != descriptor
    try:
        if duplicate:
            os.dup2(replacement, descriptor)
        with pytest.raises(ValueError, match="not stable protected root data"):
            module._protected_snapshot_raw()
    finally:
        os.close(replacement)
        if duplicate:
            os.close(descriptor)


def test_launcher_rejects_an_invalid_preflight_descriptor_at_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TGW_CONTEXT_PREFLIGHT_SNAPSHOT_FD", "2")
    with pytest.raises(ValueError, match="descriptor is invalid"):
        _launcher_module(tmp_path, monkeypatch)


def _bootstrapped_real_99416_legacy_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    module = _launcher_module(
        tmp_path,
        monkeypatch,
        legacy_parser_api="",
        snapshot_task_updates={"operator_note": "café 東京"},
    )
    expected_parser = subprocess.run(
        ["git", "show", "99416bfb:src/tgw/current_context_snapshot.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    assert module.SERVER_SOURCE.joinpath(
        "tgw/current_context_snapshot.py"
    ).read_bytes() == expected_parser
    assert module.RUNTIME_RELEASE.stat().st_mode & 0o222 == 0
    assert module._selected_snapshot_parser(module._test_snapshot_raw)["task"] == (
        module._test_snapshot["task"]
    )
    return module


def test_launcher_cold_starts_with_exact_real_99416_legacy_api_non_ascii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _bootstrapped_real_99416_legacy_launcher(tmp_path, monkeypatch)
    assert module._current_context()["task"]["operator_note"] == "café 東京"
    assert module._current_context()["snapshot_sha256"] == module._test_snapshot[
        "snapshot_sha256"
    ]


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda raw: b" " + raw, "wire format is invalid"),
        (lambda raw: raw[:-1] + b" \n", "wire format is invalid"),
        (lambda raw: raw[:-1], "wire format is invalid"),
        (lambda _raw: b"{malformed}\n", "snapshot is invalid"),
        (lambda raw: raw + b"{}\n", "snapshot is invalid"),
        (lambda _raw: b"x" * (256 * 1024 + 1), "wire format is invalid"),
        (
            lambda raw: json.dumps(
                json.loads(raw),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode() + b"\n",
            "wire format is invalid",
        ),
    ],
    ids=[
        "leading", "trailing", "missing-lf", "malformed", "extra-stream",
        "oversized", "alternate-non-ascii-escaping",
    ],
)
def test_launcher_selected_real_99416_legacy_branch_rejects_noncanonical_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any, error: str
) -> None:
    module = _bootstrapped_real_99416_legacy_launcher(tmp_path, monkeypatch)
    with pytest.raises(module.CurrentContextError, match=error):
        module._selected_snapshot_parser(mutate(module._test_snapshot_raw))


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda parser: setattr(parser, "parse_bytes", None), "parser API is invalid"),
        (lambda parser: setattr(parser, "parse_bytes", parser.parse), "parser API is invalid"),
        (
            lambda parser: (
                setattr(parser, "parse_bytes", parser.parse),
                setattr(parser, "MAX_SNAPSHOT_BYTES", 1),
            ),
            "size bounds differ",
        ),
        (lambda parser: delattr(parser, "parse"), "parser API is invalid"),
        (lambda parser: setattr(parser, "parse", None), "parser API is invalid"),
    ],
    ids=[
        "non-callable-parse-bytes", "parse-bytes-without-maximum",
        "wrong-maximum", "missing-legacy-parse", "non-callable-legacy-parse",
    ],
)
def test_launcher_selected_parser_rejects_invalid_api_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: Any, error: str
) -> None:
    module = _bootstrapped_real_99416_legacy_launcher(tmp_path, monkeypatch)
    mutation(module._snapshot_runtime)
    with pytest.raises(ValueError, match=error):
        module._selected_snapshot_parser(module._test_snapshot_raw)


def test_differing_staged_snapshot_binds_status_and_task_through_cold_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    module = _launcher_module(fixture_root, monkeypatch)
    staged = json.loads(json.dumps(module._test_snapshot))
    staged["task"]["durable_history"] = {"snapshot": "staged-not-live"}
    body = dict(staged)
    body.pop("snapshot_sha256")
    staged["snapshot_sha256"] = (
        "sha256:" + hashlib.sha256(_legacy_canonical(body)).hexdigest()
    )
    staged_path = tmp_path / "cold-staged-snapshot.json"
    staged_raw = _legacy_canonical(staged) + b"\n"
    staged_path.write_bytes(staged_raw)
    staged_path.chmod(0o400)
    descriptor = os.open(staged_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)

    source = LAUNCHER.read_text(encoding="utf-8")
    actor = pwd.getpwuid(os.geteuid()).pw_name
    replacements = {
        "#!/opt/TGW/.venvs/controller/bin/python3": f"#!{sys.executable}",
        'RUNTIME_RELEASES = Path("/opt/TGW/tgw-lib/coding-runtime/releases")': (
            f"RUNTIME_RELEASES = Path({str(module.RUNTIME_RELEASES)!r})"
        ),
        'CONTEXT_SOURCE = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")': (
            f"CONTEXT_SOURCE = Path({str(module.CONTEXT_SOURCE)!r})"
        ),
        'CATALOG = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")': (
            f"CATALOG = Path({str(module.CATALOG)!r})"
        ),
        'CURRENT_CONTEXT = Path("/opt/TGW/tgw-lib/config/tgw-context-current.json")': (
            f"CURRENT_CONTEXT = Path({str(module.CURRENT_CONTEXT)!r})"
        ),
        'HARNESS_ACTORS = frozenset({"codex", "claude", "deepseek"})': (
            f"HARNESS_ACTORS = frozenset({{{actor!r}}})"
        ),
        "RUNTIME_OWNER_UID = 0": f"RUNTIME_OWNER_UID = {os.getuid()}",
        "RUNTIME_OWNER_GID = 0": f"RUNTIME_OWNER_GID = {os.getgid()}",
        "before.st_uid != 0": f"before.st_uid != {os.getuid()}",
        "before.st_gid != 0": f"before.st_gid != {os.getgid()}",
    }
    for old, new in replacements.items():
        assert old in source
        source = source.replace(old, new)
    main_start = source.index("def main() -> None:\n")
    main_guard = source.index('\n\nif __name__ == "__main__":', main_start)
    cold_main = '''def main() -> None:
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        if request.get("method") == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "serverInfo": {"name": "cold-fixture", "version": "1"},
            }
        else:
            name = request["params"]["name"]
            if name == "tgw_context_status":
                context = _current_context()
                value = {
                    "ok": True,
                    "actor": _harness_actor(),
                    "generation_status": {"state": "CURRENT"},
                    "current_context": {
                        key: context[key]
                        for key in (
                            "active_capability",
                            "active_treatment",
                            "plan_commit",
                            "source_commit",
                            "source_tree",
                            "snapshot_sha256",
                        )
                    },
                }
                text = json.dumps(value)
            else:
                text = _current_task()
            result = {
                "isError": False,
                "content": [{"type": "text", "text": text}],
            }
        print(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
            flush=True,
        )
'''
    source = source[:main_start] + cold_main + source[main_guard:]
    launcher = tmp_path / "cold-tgw-context-mcp"
    launcher.write_text(source, encoding="utf-8")
    launcher.chmod(0o555)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "tgw_context_status", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "tgw_context_current_task", "arguments": {}},
        },
    ]
    payload = "".join(json.dumps(request) + "\n" for request in requests)
    try:
        completed = subprocess.run(
            [str(launcher)],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TGW_CONTEXT_PREFLIGHT_SNAPSHOT_FD": str(descriptor),
            },
            pass_fds=(descriptor,),
            timeout=10,
        )
    finally:
        os.close(descriptor)
        os.close(module._RUNTIME_RELEASE_DESCRIPTOR)
    assert completed.returncode == 0, completed.stderr
    responses = {
        response["id"]: response
        for response in map(json.loads, completed.stdout.splitlines())
    }
    status = json.loads(responses[2]["result"]["content"][0]["text"])
    task = json.loads(responses[3]["result"]["content"][0]["text"])
    expected_binding = {
        key: staged[key]
        for key in (
            "active_capability",
            "active_treatment",
            "plan_commit",
            "source_commit",
            "source_tree",
            "snapshot_sha256",
        )
    }
    assert status["current_context"] == expected_binding
    assert task["context"] == expected_binding
    assert task["durable_history"] == {"snapshot": "staged-not-live"}
    assert staged_raw != module.CURRENT_CONTEXT.read_bytes()


def test_launcher_current_task_rejects_unregistered_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)
    monkeypatch.setattr(module.pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_name="controller"))
    with pytest.raises(ValueError, match="not a registered TGW harness actor"):
        module._current_task()


def test_launcher_bootstrap_rejects_a_tampered_transitive_runtime_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="bytes differ from Git"):
        _launcher_module(
            tmp_path,
            monkeypatch,
            tamper_runtime="dependency.py",
        )


def test_bundle_agrees_with_every_local_binding_and_refuses_malformed_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)
    snapshot = _snapshot()
    monkeypatch.setattr(module, "_harness_actor", lambda: "codex")
    monkeypatch.setattr(module, "_current_context", lambda: dict(snapshot))
    server = _server(snapshot)

    value = json.loads(module.context_server_bundle(server, "current", 7))

    assert value["actor"] == value["receiver"] == "codex"
    assert value["task"] == "PP-WORKFLOW-001"
    assert value["plan_graph"]["receiver"] == "codex"
    assert value["current_context"]["snapshot_sha256"] == snapshot["snapshot_sha256"]
    assert all(effect is False for effect in value["dependencies"].values())
    with pytest.raises(server.ContextError, match="ordinary non-empty"):
        module.context_server_bundle(server, "", 7)
    with pytest.raises(server.ContextError, match="between 1 and 50"):
        module.context_server_bundle(server, "current", 0)


def test_bundle_fails_closed_on_a_mixed_source_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)
    snapshot = _snapshot()
    monkeypatch.setattr(module, "_harness_actor", lambda: "codex")
    monkeypatch.setattr(module, "_current_context", lambda: dict(snapshot))
    server = _server(snapshot)
    server.context_status()["source"]["tree"] = "9" * 40

    with pytest.raises(server.ContextError, match="source identity differs"):
        module.context_server_bundle(server, "current", 7)


def test_bundle_fails_closed_when_status_changes_during_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)
    snapshot = _snapshot()
    monkeypatch.setattr(module, "_harness_actor", lambda: "codex")
    monkeypatch.setattr(module, "_current_context", lambda: dict(snapshot))
    server = _server(snapshot)
    stable_status = server.context_status()
    calls = 0

    def changing_status() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return stable_status
        return {**stable_status, "context_sha256": "sha256:" + "9" * 64}

    server.context_status = changing_status

    with pytest.raises(server.ContextError, match="status changed during retrieval"):
        module.context_server_bundle(server, "current", 7)


def test_doctor_context_launcher_check_and_receipted_atomic_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    monkeypatch.setattr(
        doctor_cli, "_selected_context_artifacts", lambda _paths: selected
    )
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    probes: list[tuple[Path, str, dict[str, Any]]] = []

    def probe(
        launcher: Path, actor: str, expected: dict[str, Any], **_kwargs: Any
    ) -> dict[str, Any]:
        probes.append((launcher, actor, expected))
        assert launcher.name == "tgw-context-mcp"
        assert launcher == paths.context_launcher
        if len(probes) == 1:
            assert not list(paths.receipts.glob("*-context-launcher.json"))
        assert expected["task"] == json.loads(paths.context_task.read_text())
        assert expected["cursor"] == json.loads(paths.context_cursor.read_text())
        return {
            "actor": actor,
            "methods": [
                "initialize",
                "tools/list",
                "tgw_context_status",
                "tgw_context_current_task",
            ],
        }

    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        probe,
    )
    monkeypatch.setattr(
        doctor_cli,
        "_context_processes",
        lambda _paths: [
            {"pid": 41, "predates_launcher": True},
            {"pid": 42, "predates_launcher": False},
        ],
    )

    assert doctor_cli.check_context_launcher(paths)["state"] == "FAIL"
    result = doctor_cli.repair_context_launcher(paths)

    installed = doctor_cli._context_pair(paths)["launcher"]
    assert result["changed"] is True
    assert result["restart_required"] == [41]
    assert result["client_processes_mutated"] is False
    assert Path(result["receipt"]).is_file()
    assert installed["raw"] == selected["launcher"].read_bytes()
    assert installed["mode"] == 0o555
    assert installed["uid"] == os.getuid()
    assert installed["gid"] == os.getgid()
    assert set(result["runtime_hashes"]) == {
        "launcher",
        "publisher",
        "context_mcp_server",
        "current_context_snapshot",
        "local_context_runtime",
    }
    assert doctor_cli.check_context_launcher(paths)["state"] == "PASS"
    assert [probe[0] for probe in probes] == [
        paths.context_launcher,
        paths.context_launcher,
    ]


def test_generation_pointer_is_the_only_pair_visibility_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    monkeypatch.setattr(
        doctor_cli, "_selected_context_artifacts", lambda _paths: selected
    )
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli, "_context_processes", lambda _paths: [])
    doctor_cli.repair_context_launcher(paths)
    first = doctor_cli._context_pair(paths)
    assert first["launcher"]["sha256"] == selected["hashes"]["launcher"]
    assert first["publisher"]["sha256"] == selected["hashes"]["publisher"]
    assert paths.context_launcher.read_bytes() == doctor_cli._CONTEXT_DISPATCH_SHIM
    assert paths.context_publisher.read_bytes() == doctor_cli._CONTEXT_DISPATCH_SHIM


@pytest.mark.parametrize("attack", ["parser-bytes", "unexpected-file", "wrong-mode"])
def test_repair_rejects_hostile_preexisting_runtime_tree_before_pointer_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli, "_context_processes", lambda _paths: [])
    doctor_cli.repair_context_launcher(paths)
    pointer = paths.context_generation_pointer
    before = doctor_cli._surface_snapshot(pointer)
    generation = doctor_cli._resolved_context_generation(paths)
    runtime = generation / "runtime"
    if attack == "parser-bytes":
        parser = runtime / "tgw/current_context_snapshot.py"
        parser.chmod(0o644)
        parser.write_bytes(b"hostile\n")
        parser.chmod(0o444)
    elif attack == "unexpected-file":
        runtime.chmod(0o755)
        hostile = runtime / "hostile.py"
        hostile.write_bytes(b"hostile\n")
        hostile.chmod(0o444)
        runtime.chmod(0o555)
    else:
        parser = runtime / "tgw/current_context_snapshot.py"
        parser.chmod(0o644)

    with pytest.raises(doctor_cli.DoctorError, match="runtime type, owner, mode, or hash"):
        doctor_cli.repair_context_launcher(paths)

    assert doctor_cli._same_link_identity(
        doctor_cli._surface_snapshot(pointer), before
    )


def test_context_launcher_repair_replay_preserves_selected_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli, "_context_processes", lambda _paths: [])

    probes: list[Path] = []
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda launcher, *_args, **_kwargs: probes.append(launcher)
        or {"generation": "CURRENT"},
    )
    doctor_cli.repair_context_launcher(paths)
    target = os.readlink(paths.context_generation_pointer)
    doctor_cli.repair_context_launcher(paths)

    assert os.readlink(paths.context_generation_pointer) == target
    assert doctor_cli._context_pair(paths)["generation"].name == Path(target).name
    assert probes == [paths.context_launcher, paths.context_launcher]


def test_context_launcher_failed_live_shim_probe_restores_pointer_and_is_receipted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    old = {
        "launcher": paths.context_launcher.read_bytes(),
        "publisher": paths.context_publisher.read_bytes(),
    }
    old_name = doctor_cli._context_old_generation_name(old)
    candidate_name = doctor_cli._context_generation_name(selected)
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda launcher, *_args, **_kwargs: (_ for _ in ()).throw(
            doctor_cli.DoctorError(
                "installed Context MCP cold probe timed out after "
                f"{doctor_cli._CONTEXT_COLD_PROBE_BUDGET_SECONDS:g}s and was "
                f"terminated: {launcher}"
            )
        ),
    )

    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context-launcher", paths)

    assert os.readlink(paths.context_generation_pointer) == str(
        Path("generations") / old_name
    )
    assert (paths.context_generation_root / candidate_name).is_dir()
    receipts = list(paths.receipts.iterdir())
    assert any(path.name.endswith("-context-launcher-failed.json") for path in receipts)
    assert not any(path.name.endswith("-context-launcher.json") for path in receipts)


def test_context_launcher_failed_live_probe_never_overwrites_concurrent_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    candidate_name = doctor_cli._context_generation_name(selected)
    concurrent_target = Path("generations/context-concurrent")
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)

    def failed_probe(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        paths.context_generation_pointer.unlink()
        paths.context_generation_pointer.symlink_to(concurrent_target)
        raise doctor_cli.DoctorError("concurrent probe failure")

    monkeypatch.setattr(doctor_cli, "_probe_context_stdio", failed_probe)

    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context-launcher", paths)

    assert os.readlink(paths.context_generation_pointer) == str(concurrent_target)
    assert (paths.context_generation_root / candidate_name).is_dir()
    assert list(paths.receipts.glob("*-context-launcher-failed.json"))
    assert not list(paths.receipts.glob("*-context-launcher.json"))


def test_context_launcher_failed_live_probe_preserves_same_target_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    candidate_name = doctor_cli._context_generation_name(selected)
    candidate_target = Path("generations") / candidate_name
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)

    def failed_probe(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        paths.context_generation_pointer.unlink()
        paths.context_generation_pointer.symlink_to(candidate_target)
        raise doctor_cli.DoctorError("same-target concurrent probe failure")

    monkeypatch.setattr(doctor_cli, "_probe_context_stdio", failed_probe)

    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context-launcher", paths)

    assert os.readlink(paths.context_generation_pointer) == str(candidate_target)
    assert (paths.context_generation_root / candidate_name).is_dir()
    assert list(paths.receipts.glob("*-context-launcher-failed.json"))
    assert not list(paths.receipts.glob("*-context-launcher.json"))


@pytest.mark.parametrize("same_target", [True, False])
def test_context_launcher_exchange_to_observation_replacement_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_target: bool
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    pointer_tmp = paths.context_generation_pointer.with_name(
        "." + paths.context_generation_pointer.name + ".new"
    )
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    prior: dict[str, Any] = {}
    replacement: dict[str, Any] = {}

    def replace_after_exchange(phase: str, _paths: doctor_cli.DoctorPaths) -> None:
        if phase != "exchange":
            return
        prior.update(doctor_cli._surface_snapshot(pointer_tmp))
        target = os.readlink(paths.context_generation_pointer)
        paths.context_generation_pointer.unlink()
        paths.context_generation_pointer.symlink_to(
            target if same_target else "generations/context-concurrent"
        )
        replacement.update(doctor_cli._surface_snapshot(paths.context_generation_pointer))

    monkeypatch.setattr(doctor_cli, "_context_repair_phase", replace_after_exchange)

    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context-launcher", paths)

    assert doctor_cli._same_link_identity(
        doctor_cli._surface_snapshot(paths.context_generation_pointer), replacement
    )
    assert doctor_cli._same_link_identity(
        doctor_cli._surface_snapshot(pointer_tmp), prior
    )
    assert list(paths.receipts.glob("*-context-launcher-failed.json"))
    assert not list(paths.receipts.glob("*-context-launcher.json"))


def test_context_launcher_first_post_exchange_observation_failure_preserves_both_pointers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    pointer_tmp = paths.context_generation_pointer.with_name(
        "." + paths.context_generation_pointer.name + ".new"
    )
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    original_snapshot = doctor_cli._surface_snapshot
    exchanged = False
    live: dict[str, Any] = {}
    displaced: dict[str, Any] = {}

    def mark_exchange(phase: str, _paths: doctor_cli.DoctorPaths) -> None:
        nonlocal exchanged
        if phase == "exchange":
            live.update(original_snapshot(paths.context_generation_pointer))
            displaced.update(original_snapshot(pointer_tmp))
            exchanged = True

    def fail_first_observation(path: Path) -> dict[str, Any]:
        nonlocal exchanged
        if exchanged and path == paths.context_generation_pointer:
            exchanged = False
            raise OSError("injected first live-pointer observation failure")
        return original_snapshot(path)

    monkeypatch.setattr(doctor_cli, "_context_repair_phase", mark_exchange)
    monkeypatch.setattr(doctor_cli, "_surface_snapshot", fail_first_observation)

    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context-launcher", paths)

    assert doctor_cli._same_link_identity(
        original_snapshot(paths.context_generation_pointer), live
    )
    assert doctor_cli._same_link_identity(
        original_snapshot(pointer_tmp), displaced
    )
    assert list(paths.receipts.glob("*-context-launcher-failed.json"))
    assert not list(paths.receipts.glob("*-context-launcher.json"))


def test_context_launcher_exchange_wrapper_exception_preserves_and_replays_exact_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    pointer_tmp = paths.context_generation_pointer.with_name(
        "." + paths.context_generation_pointer.name + ".new"
    )
    candidate_name = doctor_cli._context_generation_name(selected)
    candidate = paths.context_generation_root / candidate_name
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli, "_context_processes", lambda _paths: [])
    real_exchange = doctor_cli._rename_exchange
    exchanged_live: dict[str, Any] = {}
    exchanged_displaced: dict[str, Any] = {}

    def exchange_then_raise(parent: int, first: str, second: str) -> None:
        real_exchange(parent, first, second)
        exchanged_live.update(doctor_cli._surface_snapshot(paths.context_generation_pointer))
        exchanged_displaced.update(doctor_cli._surface_snapshot(pointer_tmp))
        raise OSError("exchange wrapper raised after kernel success")

    monkeypatch.setattr(doctor_cli, "_rename_exchange", exchange_then_raise)
    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context-launcher", paths)

    assert doctor_cli._same_link_identity(
        doctor_cli._surface_snapshot(paths.context_generation_pointer), exchanged_live
    )
    assert doctor_cli._same_link_identity(
        doctor_cli._surface_snapshot(pointer_tmp), exchanged_displaced
    )
    assert candidate.is_dir()
    assert list(paths.receipts.glob("*-context-launcher-failed.json"))
    assert not list(paths.receipts.glob("*-context-launcher.json"))

    monkeypatch.setattr(doctor_cli, "_rename_exchange", real_exchange)
    cold_probes: list[Path] = []
    concurrent_target = "generations/context-concurrent-replacement"

    def cold_probe(launcher: Path, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        cold_probes.append(launcher)
        pointer_tmp.unlink()
        pointer_tmp.symlink_to(concurrent_target)
        return {"generation": "CURRENT"}

    monkeypatch.setattr(doctor_cli, "_probe_context_stdio", cold_probe)
    replay = doctor_cli.repair_context_launcher(paths)
    replacement = doctor_cli._surface_snapshot(pointer_tmp)

    assert cold_probes == [paths.context_launcher]
    assert replay["changed"] is False
    assert replay["receipt"] is None
    assert replay["retained_displaced_pointer"]["identity"] == replacement
    assert "installed-shim cold proof" in replay["retained_displaced_pointer"]["reason"]
    assert os.readlink(pointer_tmp) == concurrent_target
    assert os.readlink(paths.context_generation_pointer) == str(
        Path("generations") / candidate_name
    )
    assert candidate.is_dir()
    assert list(paths.receipts.glob("*-context-launcher-failed.json"))
    assert not list(paths.receipts.glob("*-context-launcher.json"))


@pytest.mark.parametrize(
    "phase",
    [
        "old-generation-created",
        "old-generation-files",
        "old-generation-fsync",
        "old-generation-chmod",
        "old-generation-rename",
        "old-pointer",
        "shim-1",
        "shim-2",
        "runtime-copy",
        "runtime-fsync",
        "generation-rename",
        "staged-pointer",
        "exchange",
        "displaced-pointer-unlink",
        "parent-fsync",
    ],
)
def test_context_launcher_adversarial_phase_replay_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    captured = tmp_path.parent / f"captured-{phase}"

    def capture(observed_phase: str, _paths: doctor_cli.DoctorPaths) -> None:
        if observed_phase == phase and not captured.exists():
            shutil.copytree(tmp_path, captured, symlinks=True)

    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli, "_context_processes", lambda _paths: [])
    monkeypatch.setattr(doctor_cli, "_context_repair_phase", capture)
    doctor_cli.repair_context_launcher(paths)
    assert captured.is_dir(), phase

    completed = tmp_path.parent / f"completed-{phase}"
    tmp_path.rename(completed)
    shutil.copytree(captured, tmp_path, symlinks=True)
    for directory in (completed, *completed.rglob("*")):
        if directory.is_dir() and not directory.is_symlink():
            directory.chmod(0o755)
    shutil.rmtree(completed)
    monkeypatch.setattr(doctor_cli, "_context_repair_phase", lambda *_args: None)
    first = doctor_cli.repair_context_launcher(paths)
    pointer_before = paths.context_generation_pointer.lstat()
    target_before = os.readlink(paths.context_generation_pointer)
    second = doctor_cli.repair_context_launcher(paths)
    pointer_after = paths.context_generation_pointer.lstat()

    pair = doctor_cli._context_pair(paths)
    assert pair["launcher"]["sha256"] == selected["hashes"]["launcher"]
    assert pair["publisher"]["sha256"] == selected["hashes"]["publisher"]
    assert os.readlink(paths.context_generation_pointer) == target_before
    assert (pointer_after.st_dev, pointer_after.st_ino) == (
        pointer_before.st_dev,
        pointer_before.st_ino,
    )
    assert first["ok"] is second["ok"] is True
    assert second["changed"] is False
    assert second["receipt"] is None


@pytest.mark.parametrize("legacy_key", ["launcher", "publisher"])
def test_context_launcher_replay_converges_one_shim_one_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_key: str
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    old = {
        "launcher": paths.context_launcher.read_bytes(),
        "publisher": paths.context_publisher.read_bytes(),
    }
    old_name = doctor_cli._context_old_generation_name(old)
    generation = paths.context_generation_root / old_name
    generation.mkdir(parents=True)
    for key, name in (("launcher", "tgw-context-mcp"), ("publisher", "tgw-context-publish")):
        (generation / name).write_bytes(old[key])
        (generation / name).chmod(0o555)
    generation.chmod(0o555)
    paths.context_generation_pointer.parent.mkdir(parents=True, exist_ok=True)
    paths.context_generation_pointer.symlink_to(Path("generations") / old_name)
    shim_key = "publisher" if legacy_key == "launcher" else "launcher"
    getattr(paths, f"context_{shim_key}").write_bytes(doctor_cli._CONTEXT_DISPATCH_SHIM)
    getattr(paths, f"context_{shim_key}").chmod(0o555)
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli, "_context_processes", lambda _paths: [])

    doctor_cli.repair_context_launcher(paths)

    assert paths.context_launcher.read_bytes() == doctor_cli._CONTEXT_DISPATCH_SHIM
    assert paths.context_publisher.read_bytes() == doctor_cli._CONTEXT_DISPATCH_SHIM
    assert doctor_cli._context_pair(paths)["launcher"]["sha256"] == selected["hashes"]["launcher"]


def test_first_migration_refuses_hostile_preexisting_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    original = {
        "launcher": paths.context_launcher.read_bytes(),
        "publisher": paths.context_publisher.read_bytes(),
    }
    paths.context_generation_root.mkdir(parents=True)
    (paths.context_generation_root / "hostile").mkdir()
    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)

    with pytest.raises(doctor_cli.DoctorError, match="ambiguous pre-existing generations"):
        doctor_cli.repair_context_launcher(paths)

    assert paths.context_launcher.read_bytes() == original["launcher"]
    assert paths.context_publisher.read_bytes() == original["publisher"]


def test_shim_migration_keeps_pointer_on_exact_old_pair_until_final_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    old = {
        "launcher": paths.context_launcher.read_bytes(),
        "publisher": paths.context_publisher.read_bytes(),
    }
    observed: list[tuple[bytes, bytes]] = []
    original_atomic = doctor_cli._atomic_bytes

    def atomic(path: Path, raw: bytes, **kwargs: Any) -> None:
        original_atomic(path, raw, **kwargs)
        if path in {paths.context_launcher, paths.context_publisher}:
            generation = doctor_cli._resolved_context_generation(paths)
            observed.append(
                (
                    (generation / "tgw-context-mcp").read_bytes(),
                    (generation / "tgw-context-publish").read_bytes(),
                )
            )

    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", lambda _paths: selected)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli, "_context_processes", lambda _paths: [])
    monkeypatch.setattr(doctor_cli, "_atomic_bytes", atomic)

    doctor_cli.repair_context_launcher(paths)

    assert observed == [(old["launcher"], old["publisher"])] * 2


def test_doctor_context_launcher_check_rejects_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    paths.context_launcher.write_bytes(selected["launcher"].read_bytes())
    paths.context_launcher.chmod(0o555)
    paths = replace(
        paths,
        context_install_uid=os.getuid() + 1,
    )
    monkeypatch.setattr(
        doctor_cli, "_selected_context_artifacts", lambda _paths: selected
    )

    result = doctor_cli.check_context_launcher(paths)

    assert result["state"] == "FAIL"
    assert "generation pointer" in result["detail"]


def test_doctor_context_launcher_refuses_a_concurrent_destination_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    monkeypatch.setattr(
        doctor_cli, "_selected_context_artifacts", lambda _paths: selected
    )
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    original_snapshot = doctor_cli._surface_snapshot
    calls = 0

    def raced(path: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 3:
            paths.context_launcher.write_bytes(b"concurrent\n")
            paths.context_launcher.chmod(0o555)
        return original_snapshot(path)

    monkeypatch.setattr(doctor_cli, "_surface_snapshot", raced)

    with pytest.raises(doctor_cli.DoctorError, match="concurrent"):
        doctor_cli.repair_context_launcher(paths)
    assert paths.context_launcher.read_bytes() == b"concurrent\n"


def test_doctor_context_launcher_rolls_back_if_runtime_changes_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    changed_selection = {**selected, "commit": "c" * 40}
    paths = _doctor_paths(tmp_path)
    original = paths.context_launcher.read_bytes()
    calls = 0

    def selection(_paths: doctor_cli.DoctorPaths) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return changed_selection if calls == 3 else selected

    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", selection)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)

    with pytest.raises(doctor_cli.DoctorError, match="selected Context runtime changed"):
        doctor_cli.repair_context_launcher(paths)

    assert paths.context_launcher.read_bytes() == original


def test_doctor_context_launcher_failure_is_durably_receipted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    changed_selection = {**selected, "commit": "c" * 40}
    paths = _doctor_paths(tmp_path)
    calls = 0

    def selection(_paths: doctor_cli.DoctorPaths) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return changed_selection if calls == 3 else selected

    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", selection)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)

    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context-launcher", paths)

    assert list(paths.receipts.glob("*context-launcher-started.json"))
    assert list(paths.receipts.glob("*context-launcher-failed.json"))

"""Literal replayability and independent closure tests for freeze evidence."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def _verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_freeze_evidence", HERE / "verify_freeze_evidence.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "run_freeze_gates", HERE / "run_freeze_gates.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner(tmp_path: Path):
    module = _runner_module()
    runner = module.FreezeRunner(
        tmp_path, tmp_path, REPO, tmp_path / "evidence", tmp_path / "store", tmp_path / "luet"
    )
    runner.protect_bytes = lambda raw: {
        "ref": "artifact:sha256:" + module.digest(raw),
        "sha256": "sha256:" + module.digest(raw), "bytes": len(raw),
    }
    return runner


def test_closed_structure_and_filesystem_metadata():
    result = _verifier().verify(REPO, Path("/opt/TGW/evidence/codex/sha256"))
    assert result["status"] == "PASS"
    assert result["gate_count"] >= 17


def test_literal_argv_and_environment_are_replayable_without_shell_parsing():
    catalog = json.loads(
        (REPO / "agent-services/catalogs/f3cefe5-closed-freeze-evidence.json").read_text()
    )
    for ref in catalog["gate_records"].values():
        record = json.loads((REPO / ref["path"]).read_text())
        assert record["environment"]["clear_inherited"] is True
        assert record["schema"] == "tgw-freeze-execution-record/v2"
        assert record["logical_replay_argv"][0] == record["executable"]["logical_path"]
        assert record["actual_execve_argv"][0] == record["executable"]["actual_fd_path"]
        assert record["executable"]["logical_path"].startswith("/")
        assert record["executable"]["before"] == record["executable"]["after"]
        assert record["descriptor_execution"]["executable_fd"] in record["descriptor_execution"]["pass_fds"]
        assert record["cwd"].startswith("/")
        assert all(isinstance(item, str) and "\x00" not in item for item in record["actual_execve_argv"])
        assert all(isinstance(item, str) and "\x00" not in item for item in record["logical_replay_argv"])
        assert set(record["environment"]["values"]) == {
            "HOME", "LC_ALL", "NO_COLOR", "PATH", "PYTHONDONTWRITEBYTECODE",
            "PYTHONHASHSEED", "PYTHONPATH", "TMPDIR",
        } or set(record["environment"]["values"]) == {
            "HOME", "LC_ALL", "NO_COLOR", "PATH", "PYTHONDONTWRITEBYTECODE",
            "PYTHONHASHSEED", "PYTHONPATH", "TMPDIR", "TGW_RENDER_TEST_PARSE_ONLY",
            "ASAN_OPTIONS", "UBSAN_OPTIONS",
        }


def test_closed_documents_contain_no_private_key_or_grant_material():
    paths = [
        REPO / "agent-services/catalogs/f3cefe5-closed-freeze-evidence.json",
        REPO / "agent-services/candidates/integrated-f3cefe5-CLOSED-FREEZE.json",
        REPO / "agent-services/candidates/platform-bootstrap-prerequisite-f3cefe5-CLOSED-NOT-EXECUTABLE.json",
        REPO / "agent-services/receipts/source-audit-f3cefe5-closed-freeze.json",
        REPO / "agent-services/receipts/f3cefe5-closed-store-readiness.json",
        *sorted((HERE / "records").glob("*.json")),
    ]
    forbidden = (b"BEGIN PRIVATE KEY", b"BEGIN OPENSSH PRIVATE KEY", b'"grant": {', b'"request": {')
    for path in paths:
        raw = path.read_bytes()
        assert not any(marker in raw for marker in forbidden), path


def test_swapped_executable_path_is_fail_closed_without_a_record(tmp_path):
    runner = _runner(tmp_path)
    executable = tmp_path / "gate.sh"
    replacement = tmp_path / "replacement.sh"
    held_name = tmp_path / "held.sh"
    executable.write_text("#!/bin/sh\nprintf 'HELD\\n'\n")
    replacement.write_text("#!/bin/sh\nprintf 'REPLACED\\n'\n")
    executable.chmod(0o755)
    replacement.chmod(0o755)

    def swap(_path: Path, _fd: int) -> None:
        executable.rename(held_name)
        replacement.rename(executable)

    with pytest.raises(RuntimeError, match="(executable .*changed|named executable)"):
        runner.run(
            "held-executable-swap", executable, [], cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
            semantic=lambda out, err, _gen: {
                "status": "PASS" if out == b"HELD\n" and not err else "FAIL"
            },
            after_executable_open=swap,
        )
    assert held_name.stat().st_ino != executable.stat().st_ino
    assert not (runner.records / "held-executable-swap.json").exists()


def test_post_use_executable_mutation_is_refused(tmp_path):
    runner = _runner(tmp_path)
    executable = tmp_path / "gate.sh"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)

    def mutate(_path: Path, _fd: int) -> None:
        executable.write_text("#!/bin/sh\nprintf MUTATED\n")
        executable.chmod(0o755)

    with pytest.raises(RuntimeError, match="executable changed while held"):
        runner.run(
            "mutated-executable", executable, [], cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
            semantic=lambda _out, _err, _gen: {"status": "PASS"},
            after_child_exit=mutate,
        )


def test_input_and_generated_path_replacements_are_refused(tmp_path):
    runner = _runner(tmp_path)
    input_path = tmp_path / "input"
    replacement = tmp_path / "replacement"
    input_path.write_text("HELD\n")
    replacement.write_text("REPLACED\n")

    def swap_input(_items: list[dict]) -> None:
        os.replace(replacement, input_path)

    with pytest.raises(RuntimeError, match="(input .* changed|named input)"):
        runner.run(
            "swapped-input", Path("/usr/bin/cat"), [str(input_path)], cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
            semantic=lambda _out, _err, _gen: {"status": "PASS"}, inputs=[input_path],
            after_inputs_open=swap_input,
        )

    generated = tmp_path / "generated"
    replacement_generated = tmp_path / "generated-replacement"
    generated.write_text("HELD-GENERATED\n")
    replacement_generated.write_text("REPLACED-GENERATED\n")

    def swap_generated(_path: Path, _fd: int) -> None:
        os.replace(replacement_generated, generated)

    with pytest.raises(RuntimeError, match="(generated .* changed|named generated)"):
        runner.run(
            "swapped-generated", Path("/usr/bin/true"), [], cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
            semantic=lambda _out, _err, _gen: {"status": "PASS"}, generated=[generated],
            after_generated_open=swap_generated,
        )


def test_protected_tree_transient_replace_read_restore_is_impossible(tmp_path):
    runner = _runner(tmp_path)
    attack_root = Path("/tmp/tgw-freeze-f3cefe5-tree-attack")
    cleanup = (
        "import os,shutil,stat,sys; p=sys.argv[1]; "
        "assert p == '/tmp/tgw-freeze-f3cefe5-tree-attack'; "
        "s=os.lstat(p); assert stat.S_ISDIR(s.st_mode); shutil.rmtree(p)"
    )
    if attack_root.exists():
        subprocess.run(
            ["sudo", "-n", "/usr/bin/python3", "-c", cleanup, str(attack_root)],
            check=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        )
    attack_root.mkdir()
    runner.store = attack_root / "store"
    runner.store.mkdir(parents=True)
    source_tree = tmp_path / "source-tree"
    source_tree.mkdir()
    (source_tree / "entry").write_text("HELD-TREE\n")
    protected, before = runner.protect_tree(source_tree)
    entry = protected / "entry"
    replacement = tmp_path / "replacement-entry"
    replacement.write_text("REPLACED-TREE\n")
    attempt: dict[str, str] = {}

    def attack() -> None:
        try:
            os.replace(replacement, entry)
        except OSError as exc:
            attempt["result"] = type(exc).__name__
        else:
            attempt["result"] = "UNEXPECTED_SUCCESS"

    sleeper = tmp_path / "reader.py"
    sleeper.write_text(
        "import pathlib,sys,time\n"
        "time.sleep(0.2)\n"
        "sys.stdout.buffer.write((pathlib.Path(sys.argv[1])/'entry').read_bytes())\n"
    )
    record = runner.run(
        "protected-tree-attack", Path(__import__("sys").executable),
        [str(sleeper), str(protected)], cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        semantic=lambda out, err, _gen: {
            "status": "PASS" if out == b"HELD-TREE\n" and not err else "FAIL",
            "attack_result": attempt.get("result"),
        },
        inputs=[sleeper], input_trees=[protected], during_child=attack,
    )
    assert attempt["result"] in {"PermissionError", "OSError"}
    assert record["input_trees"][0]["before"] == record["input_trees"][0]["after"]
    assert record["input_trees"][0]["before"]["tree_hash"] == before["tree_hash"]
    subprocess.run(
        ["sudo", "-n", "/usr/bin/python3", "-c", cleanup, str(attack_root)],
        check=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
    )

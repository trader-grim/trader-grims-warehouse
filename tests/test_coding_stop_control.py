"""Race and identity tests for the exact local coding stop channel."""

from __future__ import annotations

import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

from tgw import coding_cli
from tgw.queue.worker_base import JobCancelled
from tgw.workers.coding import _run_bounded_process_group, _write_json_atomic


def _protected_manifest(tmp_path: Path, name: str = "runner.json") -> Path:
    os.chown(tmp_path, -1, grp.getgrnam("tgw-coders").gr_gid)
    tmp_path.chmod(0o2770)
    return tmp_path / name


def test_group_protected_manifest_is_atomic_and_shared(tmp_path):
    manifest = _protected_manifest(tmp_path)
    _write_json_atomic(manifest, {"writer": "first"})
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    manifest.unlink()
    os.link(victim, manifest)
    _write_json_atomic(manifest, {"writer": "second"})
    assert victim.read_text() == "unchanged"
    assert json.loads(manifest.read_text()) == {"writer": "second"}
    assert manifest.stat().st_mode & 0o777 == 0o660
    assert manifest.stat().st_nlink == 1


@pytest.mark.parametrize("mode", [0o0770, 0o2777])
def test_runner_manifest_rejects_unprotected_group_directory(tmp_path, mode):
    os.chown(tmp_path, -1, grp.getgrnam("tgw-coders").gr_gid)
    tmp_path.chmod(mode)
    with pytest.raises(OSError, match="not protected"):
        _write_json_atomic(tmp_path / "runner.json", {})


def test_runner_manifest_rejects_wrong_group_and_parent_symlink(tmp_path):
    assert os.getegid() != grp.getgrnam("tgw-coders").gr_gid
    os.chown(tmp_path, -1, os.getegid())
    tmp_path.chmod(0o2770)
    with pytest.raises(OSError, match="not protected"):
        _write_json_atomic(tmp_path / "runner.json", {})
    protected = tmp_path / "protected"
    protected.mkdir()
    os.chown(protected, -1, grp.getgrnam("tgw-coders").gr_gid)
    protected.chmod(0o2770)
    link = tmp_path / "linked"
    link.symlink_to(protected, target_is_directory=True)
    with pytest.raises(OSError):
        _write_json_atomic(link / "runner.json", {})


@pytest.mark.skipif(os.geteuid() != 0, reason="requires local identity switching")
def test_real_codex_and_db_identities_share_source_bound_runner_manifests():
    """Exercise the checked-out writer as both installed tgw-coders workers."""
    group = grp.getgrnam("tgw-coders")
    assert {"codex", "db"}.issubset(group.gr_mem)
    root = Path(tempfile.mkdtemp(prefix=".runner-control-test-", dir="/opt/TGW/var/worktrees"))
    control = root / "control"
    source_root = str(Path(__file__).parents[1] / "src")
    program = (
        "import json,sys; from pathlib import Path; "
        "from tgw.workers.coding import _write_json_atomic; "
        "_write_json_atomic(Path(sys.argv[1]), {'writer':sys.argv[2]})"
    )
    try:
        os.chown(root, 0, group.gr_gid)
        root.chmod(0o2770)
        for actor in ("codex", "db"):
            result = subprocess.run(
                ["runuser", "-u", actor, "--", sys.executable, "-c", program,
                 str(control / "shared.json"), actor],
                cwd=Path(__file__).parents[1],
                env={**os.environ, "PYTHONPATH": source_root},
                text=True, capture_output=True, check=False,
            )
            assert result.returncode == 0, result.stderr
        assert json.loads((control / "shared.json").read_text()) == {"writer": "db"}
        assert control.stat().st_uid == pwd.getpwnam("codex").pw_uid
        assert control.stat().st_gid == group.gr_gid
        assert control.stat().st_mode & 0o2777 == 0o2770
    finally:
        shutil.rmtree(root)


def _job(tmp_path: Path, state: str = "running", *, job_id: str | None = None):
    identity = job_id or str(uuid.uuid4())
    return {
        "job_id": identity, "queue_name": "codex-implement", "state": state,
        "lease_owner": "worker:local:123", "lease_token": str(uuid.uuid4()),
        "payload_json": {"worktree": str(tmp_path / "todo")},
    }


def _install(monkeypatch, tmp_path, job):
    config = {"coding": {"worktree_root": str(tmp_path)}}
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: config)
    monkeypatch.setattr(coding_cli.state_machine, "get_job", lambda _job_id: job)
    return tmp_path / ".tgw-runner-control" / f"{job['job_id']}.json"


def _manifest(job, state="running", pid=99999999):
    return {"schema": "tgw-coding-runner/v1", "job_id": job["job_id"],
            "queue_name": job["queue_name"], "lease_owner": job["lease_owner"],
            "lease_token": job["lease_token"],
            "worktree": job["payload_json"]["worktree"], "state": state,
            "pid": pid, "pgid": pid}


def test_queued_cancel_does_not_signal(monkeypatch, tmp_path):
    job = _job(tmp_path, "queued")
    _install(monkeypatch, tmp_path, job)
    monkeypatch.setattr(coding_cli.state_machine, "cancel_job", lambda *_a: {**job, "state": "cancelled"})
    result = coding_cli.stop(job["job_id"])
    assert result["stop_state"] == "cancelled_queued"


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "dead_letter"])
def test_completion_winner_is_returned_truthfully(monkeypatch, tmp_path, terminal):
    job = _job(tmp_path, "running")
    _install(monkeypatch, tmp_path, job)
    monkeypatch.setattr(
        coding_cli.state_machine, "cancel_job",
        lambda *_a: {**job, "state": terminal, "payload_json": {"result": {"ok": terminal == "succeeded"}}},
    )
    result = coding_cli.stop(job["job_id"])
    assert result["state"] == terminal
    assert result["ok"] is False
    assert result["stop_state"] == f"completion_won_{terminal}"


def test_running_cancel_requests_exact_job_only(monkeypatch, tmp_path):
    job = _job(tmp_path)
    path = _install(monkeypatch, tmp_path, job)
    path.parent.mkdir()
    path.symlink_to(tmp_path / "operator-controlled-proof")
    calls = []
    def cancel(*args):
        calls.append(args)
        return {**job, "state": "cancelled"}
    monkeypatch.setattr(coding_cli.state_machine, "cancel_job", cancel)
    assert coding_cli.stop(job["job_id"])["stop_state"] == "cancellation_requested"
    assert calls[0][0] == job["job_id"]
    assert "pid" not in json.dumps(calls)


def test_malformed_or_replayed_proof_is_never_an_input(monkeypatch, tmp_path):
    job = _job(tmp_path)
    path = _install(monkeypatch, tmp_path, job)
    path.parent.mkdir()
    path.write_text("{malformed replayed pid proof")
    def cancel(*_args):
        return {**job, "state": "cancelled"}
    monkeypatch.setattr(coding_cli.state_machine, "cancel_job", cancel)
    assert coding_cli.stop(job["job_id"])["stop_state"] == "cancellation_requested"


@pytest.mark.parametrize("pid", [1, 99999999])
def test_operator_supplied_pid_or_pgid_is_ignored(monkeypatch, tmp_path, pid):
    job = _job(tmp_path)
    path = _install(monkeypatch, tmp_path, job)
    path.parent.mkdir()
    proof = _manifest(job, pid=pid)
    proof["pgid"] = pid
    path.write_text(json.dumps(proof))
    cancelled = []
    monkeypatch.setattr(coding_cli.state_machine, "cancel_job", lambda *a: cancelled.append(a) or {**job, "state": "cancelled"})
    coding_cli.stop(job["job_id"])
    assert cancelled[0][0] == job["job_id"]


def test_no_proof_still_requests_database_cancellation(monkeypatch, tmp_path):
    job = _job(tmp_path)
    _install(monkeypatch, tmp_path, job)
    cancelled = []
    monkeypatch.setattr(coding_cli.state_machine, "cancel_job", lambda *_a: cancelled.append(True))
    monkeypatch.setattr(coding_cli.state_machine, "cancel_job", lambda *a: cancelled.append(a) or {**job, "state": "cancelled"})
    coding_cli.stop(job["job_id"])
    assert cancelled


def test_late_result_is_suppressed_and_unrelated_process_survives(tmp_path):
    cancelled = threading.Event()
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    manifest = _protected_manifest(tmp_path)
    timer = threading.Timer(0.15, cancelled.set)
    timer.start()
    try:
        with pytest.raises(JobCancelled):
            _run_bounded_process_group(
                [sys.executable, "-c", "import time; time.sleep(10); print('{}')"],
                cwd=tmp_path, env={}, timeout=5,
                cancellation_check=cancelled.is_set, runner_manifest=manifest,
                runner_identity={"job_id": "job-a", "queue_name": "codex-implement",
                                 "lease_owner": "owner-a", "lease_token": "token-a",
                                 "worktree": str(tmp_path)},
            )
        assert unrelated.poll() is None
        assert json.loads(manifest.read_text())["state"] == "stopped"
    finally:
        unrelated.terminate()
        unrelated.wait()


def test_repeat_stop_is_idempotent(monkeypatch, tmp_path):
    job = _job(tmp_path, "cancelled")
    job["payload_json"]["result"] = {
        "stop_control": {"schema": "tgw-coding-stop/v1", "kind": "queued_cancel"}
    }
    _install(monkeypatch, tmp_path, job)
    assert coding_cli.stop(job["job_id"])["stop_state"] == "already_cancelled_queued"


def test_repeat_stop_distinguishes_request_from_worker_ack(monkeypatch, tmp_path):
    job = _job(tmp_path, "cancelled")
    request_identity = {key: job[key] for key in (
        "job_id", "queue_name", "lease_owner", "lease_token"
    )}
    control = {"schema": "tgw-coding-stop/v1", "kind": "runner_cancel_requested",
               "request_identity": request_identity}
    job["payload_json"]["result"] = {"stop_control": control}
    _install(monkeypatch, tmp_path, job)
    assert coding_cli.stop(job["job_id"])["stop_state"] == "cancellation_requested"
    control["acknowledgement"] = {
        "schema": "tgw-coding-stop-ack/v1", "job_id": job["job_id"],
        "ack_id": str(uuid.uuid4()), "worker": job["lease_owner"],
        "observed_at": "2026-08-26T07:00:00+00:00",
        "reason": "stopped", "reaped": True,
        "runner": {
            "schema": "tgw-coding-runner/v2", "job_id": job["job_id"],
            "queue_name": job["queue_name"], "lease_owner": job["lease_owner"],
            "lease_token": job["lease_token"],
        },
    }
    assert coding_cli.stop(job["job_id"])["stop_state"] == "worker_confirmed_stopped"


@pytest.mark.parametrize("mutation", ["schema", "job_id", "reaped", "runner"])
def test_repeat_stop_rejects_unbound_or_unreaped_ack(monkeypatch, tmp_path, mutation):
    job = _job(tmp_path, "cancelled")
    acknowledgement = {
        "schema": "tgw-coding-stop-ack/v1", "job_id": job["job_id"],
        "ack_id": str(uuid.uuid4()), "worker": job["lease_owner"],
        "observed_at": "2026-08-26T07:00:00+00:00", "reason": "stopped",
        "reaped": True,
        "runner": {"schema": "tgw-coding-runner/v2", "job_id": job["job_id"],
                   "queue_name": job["queue_name"], "lease_owner": job["lease_owner"],
                   "lease_token": job["lease_token"]},
    }
    if mutation == "schema":
        acknowledgement["schema"] = "lookalike"
    elif mutation == "job_id":
        acknowledgement["job_id"] = str(uuid.uuid4())
    elif mutation == "reaped":
        acknowledgement["reaped"] = False
    else:
        acknowledgement["runner"] = {}
    job["payload_json"]["result"] = {"stop_control": {
        "schema": "tgw-coding-stop/v1", "kind": "runner_cancel_requested",
        "request_identity": {key: job[key] for key in (
            "job_id", "queue_name", "lease_owner", "lease_token"
        )},
        "acknowledgement": acknowledgement,
    }}
    _install(monkeypatch, tmp_path, job)
    assert coding_cli.stop(job["job_id"])["stop_state"] == "cancellation_requested"


def test_manifest_publication_failure_terminates_and_reaps(monkeypatch, tmp_path):
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr("tgw.workers.coding._write_json_atomic", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        _run_bounded_process_group(
            ["unused"], cwd=tmp_path, env={}, timeout=5,
            runner_manifest=tmp_path / "proof.json",
        )
    assert process.returncode is not None


def test_source_keeps_session_leader_unreaped_until_final_group_kill():
    source = Path("src/tgw/workers/coding.py").read_text()
    section = source[source.index("def _terminate_process_group"):source.index("def validated_coding_worktree")]
    assert "process.poll" not in section
    assert section.index("signal.SIGKILL") < section.index("process.communicate")


def test_timeout_publishes_truthful_terminal_evidence(tmp_path):
    manifest = _protected_manifest(tmp_path)
    with pytest.raises(subprocess.TimeoutExpired):
        _run_bounded_process_group(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path, env={}, timeout=0.05, runner_manifest=manifest,
            runner_identity={"job_id": "job-timeout", "lease_token": "lease-timeout"},
        )
    proof = json.loads(manifest.read_text())
    assert proof["state"] == "timeout"
    assert proof["returncode"] is not None


def test_cli_source_has_no_signal_target_or_manifest_read():
    source = Path("src/tgw/coding_cli.py").read_text()
    section = source[source.index("def stop("):source.index("def _target(")]
    assert "kill" not in section
    assert "pid" not in section
    assert "runner_state_root" not in section

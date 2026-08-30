"""Queue worker for the executable coding-treatment lane.

Each configured instance claims exactly one treatment queue (for example,
``claude-review``).  The launcher is deliberately a narrow seam: deployments
provide an argv list for each treatment and this worker executes it in the
target worktree, then persists a receipt which the coding snapshot reads.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tgw.development.partial_resume import (
    append_attempt,
    candidate_changed_paths,
    classify,
    history,
    make_attempt,
    preservation_manifest,
    recover_implementation_receipt_projection,
    source_fingerprint,
    source_tree,
    validate_closed_candidate,
    validate_implementation_lineage,
)
from tgw.development.plan_binding import MalformedPlanBindingError, validate_plan_binding
from tgw.development.worktree_lease import exclusive_worktree_lease
from tgw.errors import TreatmentFailure
from tgw.queue import worker_base
from tgw.queue.worker_base import HardFailure, JobCancelled, QueueWorker
from tgw.workflow_kernel.contracts import (
    OUTCOME_CONFLICT,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_SATISFIED,
)

CODING_TREATMENTS = frozenset({"codex-implement", "claude-review", "controller-verify", "hermes-stitch"})

DEFAULT_WORKTREE_ROOT = Path("/opt/TGW/var/worktrees")
DEFAULT_REPOSITORY_ROOT = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
DEFAULT_RUNTIME_ROOT = Path("/opt/TGW/tgw-lib/coding-runtime")
RUNNER_CONTROL_GROUP = "tgw-coders"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")

_RECEIPT_FILES = {
    "codex-implement": "implementation-receipt.json",
    "claude-review": "review-receipt.json",
    "controller-verify": "controller-harness-receipt.json",
    "hermes-stitch": "stitch-receipt.json",
}

Launcher = Callable[[str, dict[str, Any], Path], dict[str, Any] | None]

_MAY_ESTABLISH = {
    "codex-implement": frozenset({"implemented", "tested"}),
    "claude-review": frozenset({"reviewed"}),
    "controller-verify": frozenset({"tested", "linted", "controller_verified"}),
    "hermes-stitch": frozenset({"committed"}),
}
_VALID_OUTCOMES = frozenset(
    {
        OUTCOME_SATISFIED,
        OUTCOME_FAILED,
        OUTCOME_PARTIAL,
        OUTCOME_CONFLICT,
    }
)


def _run_bounded_process_group(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int | float,
    pass_fds: tuple[int, ...] = (),
    cancellation_check: Callable[[], bool] | None = None,
    runner_manifest: Path | None = None,
    runner_identity: dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one launcher and terminate its complete descendant group on timeout."""
    with tempfile.TemporaryFile(mode="w+t") as stdout_file, tempfile.TemporaryFile(mode="w+t") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        identity = dict(runner_identity or {})
        try:
            if runner_manifest is not None:
                identity.update({"schema": "tgw-coding-runner/v2", "pid": process.pid, "pgid": process.pid, "state": "running"})
                _write_json_atomic(runner_manifest, identity)
            deadline = time.monotonic() + timeout
            while True:
                # WNOWAIT observes exit without reaping.  The zombie reserves
                # its PID/PGID until the cancellation decision is final.
                exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                cancelled = cancellation_check is not None and cancellation_check()
                if cancelled:
                    _terminate_process_group(process)
                    if runner_manifest is not None:
                        identity.update({"state": "stopped", "returncode": process.returncode})
                        _write_json_atomic(runner_manifest, identity)
                    raise JobCancelled(
                        "durable cancellation won exact process scope",
                        reason="stopped",
                        reaped=process.returncode is not None,
                        runner=dict(identity),
                    )
                if exited is not None:
                    # Close any descendants that outlived the launcher while
                    # the unreaped leader still reserves this exact PGID.
                    _terminate_process_group(process)
                    break
                if time.monotonic() >= deadline:
                    _terminate_process_group(process)
                    if runner_manifest is not None:
                        identity.update({"state": "timeout", "returncode": process.returncode})
                        _write_json_atomic(runner_manifest, identity)
                    raise subprocess.TimeoutExpired(command, timeout)
                time.sleep(0.05)
        except BaseException:
            # Popen transferred ownership of an exact, unreaped session leader
            # to this channel.  Every post-spawn failure closes that scope.
            if process.returncode is None:
                _terminate_process_group(process)
            raise
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout, stderr = stdout_file.read(), stderr_file.read()
        if runner_manifest is not None:
            identity.update({"state": "exited", "returncode": process.returncode})
            _write_json_atomic(runner_manifest, identity)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    """Publish diagnostics inside the protected shared coding-group boundary."""
    coding_group = grp.getgrnam(RUNNER_CONTROL_GROUP)
    created = False
    try:
        path.parent.mkdir(mode=0o2770, parents=True)
        created = True
    except FileExistsError:
        pass
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".{path.name}.{os.getpid()}.tmp"
    try:
        parent = os.fstat(directory)
        try:
            owner = pwd.getpwuid(parent.st_uid)
        except KeyError:
            owner_is_trusted = False
        else:
            owner_is_trusted = (
                parent.st_uid == 0
                or owner.pw_gid == coding_group.gr_gid
                or owner.pw_name in coding_group.gr_mem
            )
        if created and parent.st_uid == os.geteuid() and parent.st_gid == coding_group.gr_gid:
            # mkdir honors the caller's umask; establish the exact shared mode
            # only on the directory this call created and owns.
            os.fchmod(directory, 0o2770)
            parent = os.fstat(directory)
        protected_mode = stat.S_ISDIR(parent.st_mode) and (parent.st_mode & 0o2777) == 0o2770
        if parent.st_gid != coding_group.gr_gid or not owner_is_trusted or not protected_mode:
            raise OSError("runner diagnostic directory is not protected")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o660,
            dir_fd=directory,
        )
        try:
            os.fchmod(descriptor, 0o660)
            content = (json.dumps(value, sort_keys=True) + "\n").encode()
            os.write(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Kill the scope while its unreaped leader reserves the PID and PGID."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    # Do not poll, wait, or communicate between signals.  Even if the leader
    # exits on SIGTERM it remains an unreaped zombie, so Linux cannot reuse its
    # PID as another process-group identity before our SIGKILL.
    time.sleep(0.25)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        raise RuntimeError("launcher process survived SIGKILL") from exc


def validated_coding_worktree(
    worktree_value: str | Path,
    object_id: str | Path,
    coding_config: dict[str, Any] | None = None,
) -> Path:
    """Prove that a coding worktree is a direct child of the canonical repo.

    This is deliberately shared by the foreman and worker: validation must
    happen before either layer runs project commands or writes a receipt.
    """
    config = coding_config or {}
    if not isinstance(config, dict):
        raise HardFailure("coding configuration must be an object")
    if not isinstance(worktree_value, (str, Path)) or not str(worktree_value):
        raise HardFailure("coding job has no worktree")
    if not isinstance(object_id, (str, Path)) or not str(object_id):
        raise HardFailure("coding job has no object_id")
    root_value = config.get("worktree_root", DEFAULT_WORKTREE_ROOT)
    if not isinstance(root_value, (str, Path)):
        raise HardFailure("coding.worktree_root must be a path")
    root = Path(root_value).resolve()
    worktree = Path(worktree_value).resolve()
    try:
        worktree.relative_to(root)
    except ValueError as exc:
        raise HardFailure(f"coding worktree escapes configured root: {worktree}") from exc
    if worktree.parent != root:
        raise HardFailure("coding worktree must be a top-level child of coding.worktree_root")
    if worktree != Path(object_id).resolve():
        raise HardFailure("coding worktree does not match payload.object_id")
    if not worktree.is_dir():
        raise HardFailure(f"coding worktree does not exist: {worktree}")
    repository_value = config.get("repository_root", DEFAULT_REPOSITORY_ROOT)
    if not isinstance(repository_value, (str, Path)):
        raise HardFailure("coding.repository_root must be a path")
    repository = Path(repository_value).resolve()
    worktree_top, worktree_common = CodingWorker._git_identity(worktree)
    repository_top, repository_common = CodingWorker._git_identity(repository)
    if worktree_top is None or worktree_common is None:
        raise HardFailure(f"coding worktree is not a Git worktree: {worktree}")
    if repository_top is None or repository_common is None:
        raise HardFailure("coding.repository_root is not a Git worktree")
    if worktree_top != worktree:
        raise HardFailure("coding worktree must equal its Git top-level")
    if repository_top != repository:
        raise HardFailure("coding.repository_root must equal its Git top-level")
    if worktree_common != repository_common:
        raise HardFailure("coding worktree does not belong to configured TGW repository")
    return worktree


def receipt_path_for_treatment(worktree: Path, treatment_id: str) -> Path:
    """Return the durable receipt path for a supported coding treatment."""
    try:
        return worktree / _RECEIPT_FILES[treatment_id]
    except KeyError as exc:
        raise HardFailure(f"unsupported coding treatment: {treatment_id}") from exc


def _regular_file_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise HardFailure("coding receipt is not one regular file")
        chunks = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _archive_prior_implementation_receipt(
    path: Path,
    *,
    receipt: dict[str, Any],
    predecessor: str,
) -> bool:
    """Validate the prior projection and say whether it must be replaced.

    Lifecycle-bound generations rotate their exact fenced receipt. The older
    record-less lane deliberately keeps its first negative projection while
    append-only attempt history and the queue retain later outcomes.
    """

    try:
        existing_bytes = _regular_file_bytes(path)
        existing = json.loads(existing_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HardFailure("prior coding implementation receipt is unreadable") from exc
    if not isinstance(existing, dict):
        raise HardFailure("prior coding implementation receipt is malformed")
    old_lifecycle = existing.get("coding_lifecycle")
    new_lifecycle = receipt.get("coding_lifecycle")
    stable_fence_fields = (
        "root_id",
        "binding_hash",
        "plan_binding_hash",
        "execution_root_identity",
        "card_idempotency_key",
        "closure_hash",
    )
    if (
        existing.get("status") != "FAIL"
        or existing.get("treatment_id") != "codex-implement"
        or existing.get("outcome") not in {OUTCOME_PARTIAL, OUTCOME_FAILED}
        or existing.get("plan_binding") != receipt.get("plan_binding")
        or existing.get("object_id") != receipt.get("object_id")
    ):
        raise HardFailure(
            "prior coding implementation receipt does not bind the archived generation"
        )
    old_has_lifecycle = "coding_lifecycle" in existing
    new_has_lifecycle = "coding_lifecycle" in receipt
    if not old_has_lifecycle and not new_has_lifecycle:
        # Compatibility is deliberately preservation-only. It cannot replace
        # a projection, cross into a lifecycle generation, or authorize a
        # lifecycle supervisor transition. The fixed projection can remain an
        # older generation, so bind it and the new result independently to the
        # canonical append-only attempt chain instead of treating the
        # projection as the immediately preceding attempt.
        try:
            attempts = history(path.parent)
        except (OSError, TypeError, ValueError, AttributeError) as exc:
            raise HardFailure(
                "record-less coding implementation lineage is invalid"
            ) from exc
        stable_fields = (
            "todo_id",
            "plan_commit",
            "solution_hash",
            "source_commit",
            "source_tree",
            "actor",
            "worktree",
            "treatment_id",
            "treatment_version",
        )

        def binds_attempt(value: dict[str, Any], attempt: dict[str, Any]) -> bool:
            plan = value.get("plan_binding")
            return (
                isinstance(plan, dict)
                and value.get("status") == "FAIL"
                and value.get("receipt_schema_id") == "receipt/tgw-development/v1"
                and value.get("treatment_id") == attempt.get("treatment_id")
                and value.get("treatment_version") == attempt.get("treatment_version")
                and value.get("object_id") == attempt.get("worktree")
                and value.get("outcome") == attempt.get("outcome")
                and value.get("established_conditions") == []
                and value.get("artifacts") == attempt.get("artifacts")
                and value.get("implementation_attempt_hash")
                == attempt.get("attempt_hash")
                and all(
                    plan.get(field) == attempt.get(field)
                    for field in (
                        "plan_commit",
                        "solution_hash",
                        "source_commit",
                        "worktree",
                    )
                )
            )

        projected = attempts[0] if attempts else None
        latest = attempts[-1] if attempts else None
        if (
            len(attempts) < 2
            or projected is None
            or latest is None
            or existing.get("implementation_attempt_hash")
            != projected.get("attempt_hash")
            or predecessor != attempts[-2].get("attempt_hash")
            or latest.get("predecessor") != predecessor
            or receipt.get("implementation_attempt_hash")
            != latest.get("attempt_hash")
            or any(
                attempts[0].get(field) in (None, "")
                for field in stable_fields
            )
            or any(
                attempt.get(field) != attempts[0].get(field)
                for attempt in attempts
                for field in stable_fields
            )
            or any(
                attempt.get("outcome") not in {OUTCOME_PARTIAL, OUTCOME_FAILED}
                for attempt in attempts
            )
            or not binds_attempt(existing, projected)
            or not binds_attempt(receipt, latest)
        ):
            raise HardFailure(
                "prior coding implementation receipt does not bind the archived generation"
            )
        return False
    if (
        old_has_lifecycle != new_has_lifecycle
        or not isinstance(old_lifecycle, dict)
        or not isinstance(new_lifecycle, dict)
        or existing.get("implementation_attempt_hash") != predecessor
        or any(
            old_lifecycle.get(field) != new_lifecycle.get(field)
            for field in stable_fence_fields
        )
    ):
        raise HardFailure(
            "prior coding implementation receipt does not bind the archived generation"
        )
    archive_root = path.parent / ".tgw-coding-history" / "implementation" / "receipts"
    archive_root.mkdir(parents=True, exist_ok=True)
    parent_descriptor = os.open(archive_root.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    archive_path = archive_root / (
        predecessor.removeprefix("sha256:") + ".json"
    )
    try:
        descriptor = os.open(
            archive_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o440,
        )
    except FileExistsError:
        if _regular_file_bytes(archive_path) != existing_bytes:
            raise HardFailure("archived coding implementation receipt conflicts")
    else:
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(existing_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        directory = os.open(archive_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return True


def _replace_prior_lifecycle_negative_receipt(
    path: Path, *, receipt: dict[str, Any]
) -> bool:
    """Allow the fixed receipt projection to follow a new lifecycle generation."""

    try:
        existing = json.loads(_regular_file_bytes(path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HardFailure("prior coding receipt is unreadable") from exc
    if not isinstance(existing, dict):
        raise HardFailure("prior coding receipt is malformed")
    old_lifecycle = existing.get("coding_lifecycle")
    new_lifecycle = receipt.get("coding_lifecycle")
    stable_fence_fields = (
        "root_id",
        "binding_hash",
        "plan_binding_hash",
        "execution_root_identity",
        "card_idempotency_key",
        "closure_hash",
    )
    if (
        existing.get("status") != "FAIL"
        or receipt.get("status") != "FAIL"
        or existing.get("outcome") != OUTCOME_FAILED
        or receipt.get("outcome") != OUTCOME_FAILED
        or existing.get("treatment_id") != receipt.get("treatment_id")
        or existing.get("plan_binding") != receipt.get("plan_binding")
        or existing.get("object_id") != receipt.get("object_id")
        or not isinstance(old_lifecycle, dict)
        or not isinstance(new_lifecycle, dict)
        or any(
            old_lifecycle.get(field) != new_lifecycle.get(field)
            for field in stable_fence_fields
        )
    ):
        raise HardFailure(
            "prior coding receipt does not bind the archived lifecycle generation"
        )
    return (
        old_lifecycle.get("resume_intent_hash")
        != new_lifecycle.get("resume_intent_hash")
        and old_lifecycle.get("job_binding_hash")
        != new_lifecycle.get("job_binding_hash")
    )


def _write_receipt(
    path: Path,
    receipt: dict[str, Any],
    *,
    predecessor: str | None = None,
) -> None:
    """Atomically persist a worktree receipt for the snapshot reader."""
    if path.exists() and receipt.get("outcome") != OUTCOME_SATISFIED:
        if receipt.get("treatment_id") == "codex-implement" and predecessor is not None:
            replace_prior = _archive_prior_implementation_receipt(
                path,
                receipt=receipt,
                predecessor=predecessor,
            )
        elif receipt.get("coding_lifecycle") is not None:
            replace_prior = _replace_prior_lifecycle_negative_receipt(
                path, receipt=receipt
            )
        else:
            return
        if not replace_prior:
            return
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=path.name + ".", dir=path.parent
    )
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            os.fchmod(stream.fileno(), 0o660)
            stream.write(json.dumps(receipt, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


class CodingWorker(QueueWorker):
    """Claim one coding-treatment queue and invoke its configured launcher."""

    direct_local_receipts = True

    def __init__(
        self,
        queue_name: str,
        config: dict[str, Any],
        *,
        launcher: Launcher | None = None,
    ) -> None:
        if queue_name not in CODING_TREATMENTS:
            raise ValueError(f"unsupported coding queue: {queue_name}")
        self._launcher = launcher or self._launch_configured_command
        self._worktree_lease_fd: int | None = None
        super().__init__(queue_name=queue_name, config=config)
        self.lease_seconds = max(
            self.lease_seconds,
            self._timeout_seconds() + 300,
        )

    def _timeout_seconds(self) -> int:
        raw_timeout = self._coding_config().get("timeout_s", 1800)
        if isinstance(raw_timeout, bool):
            raise HardFailure("coding timeout_s must be a positive integer")
        try:
            timeout = int(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise HardFailure("coding timeout_s must be a positive integer") from exc
        if timeout < 1:
            raise HardFailure("coding timeout_s must be a positive integer")
        return timeout

    def _launch_configured_command(self, treatment_id: str, payload: dict[str, Any], worktree: Path) -> dict[str, Any]:
        """Run the configured argv command for a treatment in its worktree."""
        command = self._configured_command(treatment_id)
        if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
            raise HardFailure(f"coding.commands.{treatment_id} must be a non-empty argv list")
        if not command:
            raise HardFailure(f"coding.commands.{treatment_id} is empty")
        try:
            lease_fd = self._worktree_lease_fd
            active_job = getattr(self, "_active_job", {})
            job_id = str(active_job.get("job_id") or payload.get("job_id") or "")
            coding = self._coding_config()
            manifest = Path(self._coding_config().get("runner_state_root", Path(coding.get("worktree_root", DEFAULT_WORKTREE_ROOT)) / ".tgw-runner-control")) / f"{job_id}.json"
            identity = {
                "job_id": job_id,
                "queue_name": treatment_id,
                "lease_owner": self.owner,
                "lease_token": str(active_job.get("lease_token") or payload.get("lease_token") or ""),
                "worktree": str(worktree),
            }
            launched_payload = {**payload, "job_id": job_id}
            completed = _run_bounded_process_group(
                command,
                cwd=worktree,
                timeout=self._timeout_seconds(),
                env={
                    **os.environ,
                    "TGW_CODING_JOB": json.dumps(launched_payload),
                    "TGW_CODING_WORKTREE_SRC": str(worktree / "src"),
                    **({"TGW_CODING_PRESERVATION_ARCHIVE_ROOT": str(coding["preservation_archive_root"])} if coding.get("preservation_archive_root") else {}),
                    **({"TGW_CODING_WORKTREE_LEASE_FD": str(lease_fd)} if lease_fd is not None else {}),
                },
                pass_fds=(lease_fd,) if lease_fd is not None else (),
                cancellation_check=lambda: (worker_base.state_machine.get_job(job_id) or {}).get("state") == "cancelled",
                runner_manifest=manifest,
                runner_identity=identity,
            )
        except JobCancelled:
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"coding launcher failed: {exc}") from exc
        if completed.returncode:
            raise RuntimeError(f"coding launcher exited {completed.returncode}: {completed.stderr[-500:]}")
        # Exit status only says the launcher executed.  Its structured result
        # states whether the treatment's authority condition was satisfied.
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise HardFailure("coding launcher returned no JSON outcome") from exc
        if not isinstance(result, dict):
            raise HardFailure("coding launcher outcome must be a JSON object")
        return result

    def _coding_config(self) -> dict[str, Any]:
        coding = self.config.get("coding", {})
        if not isinstance(coding, dict):
            raise HardFailure("coding configuration must be an object")
        return coding

    def _configured_command(self, treatment_id: str) -> list[str]:
        commands = self._coding_config().get("commands", {})
        command = commands.get(treatment_id) if isinstance(commands, dict) else None
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
            raise HardFailure(f"coding.commands.{treatment_id} must be a non-empty argv list")
        runner = Path(command[0]).name.lower()
        if runner in {"ssh", "sudo", "sh", "bash"}:
            raise HardFailure("coding runner must use the configured local argv protocol")
        allowed = self._coding_config().get("allowed_runners")
        if allowed is not None and (not isinstance(allowed, list) or not all(isinstance(item, str) and item for item in allowed) or command[0] not in allowed):
            raise HardFailure("coding command is not an allowed local runner")
        return self._pin_runtime_resident_runner(command)

    def _pin_runtime_resident_runner(self, command: list[str]) -> list[str]:
        """Bind a ``runtime_root/current`` runner to this worker's release."""
        coding = self._coding_config()
        raw_runtime_root = coding.get("runtime_root")
        runner = Path(command[0])
        if not isinstance(raw_runtime_root, str) or not raw_runtime_root:
            try:
                runner.relative_to(DEFAULT_RUNTIME_ROOT / "current")
            except ValueError:
                return command
            raise HardFailure("runtime-resident coding runner has no runtime_root")
        runtime_root = Path(raw_runtime_root)
        if not runtime_root.is_absolute() or ".." in runtime_root.parts:
            try:
                runner.relative_to(DEFAULT_RUNTIME_ROOT / "current")
            except ValueError:
                return command
            raise HardFailure("coding runtime_root is not an exact absolute path")
        selector = runtime_root / "current"
        try:
            relative_runner = runner.relative_to(selector)
        except ValueError:
            return command
        if not relative_runner.parts or ".." in relative_runner.parts:
            raise HardFailure("runtime-resident coding runner path is invalid")
        try:
            concrete_runtime_root = runtime_root.resolve(strict=True)
            process_release = Path.cwd().resolve(strict=True)
            releases_root = (concrete_runtime_root / "releases").resolve(strict=True)
        except OSError as exc:
            raise HardFailure("coding runtime/process release is unavailable") from exc
        if (
            concrete_runtime_root != runtime_root
            or process_release.parent != releases_root
            or _COMMIT.fullmatch(process_release.name) is None
        ):
            raise HardFailure(
                "runtime-resident coding runner is not bound to the worker release"
            )
        pinned = process_release / relative_runner
        try:
            resolved = pinned.resolve(strict=True)
        except OSError as exc:
            raise HardFailure("runtime-resident coding runner is unavailable") from exc
        if (
            resolved != pinned
            or not pinned.is_file()
            or not os.access(pinned, os.X_OK)
        ):
            raise HardFailure("runtime-resident coding runner is not an exact executable")
        return [str(pinned), *command[1:]]

    def _validated_worktree(self, payload: dict[str, Any]) -> Path:
        return validated_coding_worktree(
            payload.get("worktree"),
            payload.get("object_id"),
            self._coding_config(),
        )

    def _raise_if_cancelled(self, job: dict[str, Any]) -> None:
        if not job.get("lease_token"):
            return
        current = worker_base.state_machine.get_job(str(job.get("job_id") or "")) or {}
        if current.get("state") == "cancelled":
            raise JobCancelled(
                "durable cancellation suppresses coding receipt",
                reason="no_runner",
                reaped=True,
                runner={
                    "schema": "tgw-coding-runner/v2",
                    "kind": "no_runner",
                    "job_id": str(job.get("job_id") or ""),
                    "queue_name": self.queue_name,
                    "lease_owner": self.owner,
                    "lease_token": str(job.get("lease_token") or ""),
                },
            )

    def _persist_success_receipt(self, job: dict[str, Any], path: Path, receipt: dict[str, Any]) -> None:
        """Defer real leased receipts until durable success wins cancellation."""
        if job.get("lease_token"):
            self._pending_success_receipt = (str(job.get("job_id")), path, receipt)
        else:
            _write_receipt(path, receipt)

    def _on_direct_local_success(
        self,
        job: dict[str, Any],
        receipt: dict[str, Any] | None,
        register_undo: Callable[[Callable[[], None]], None],
    ) -> None:
        pending = getattr(self, "_pending_success_receipt", None)
        if pending is None or pending[0] != str(job.get("job_id")) or pending[2] is not receipt:
            raise RuntimeError("coding success has no exact pending receipt")
        pending_attempt = getattr(self, "_pending_success_attempt", None)
        created_attempt: Path | None = None
        receipt_path = pending[1]
        previous_receipt = receipt_path.read_bytes() if receipt_path.exists() else None

        def rollback_publication() -> None:
            errors: list[BaseException] = []
            if created_attempt is not None:
                try:
                    created_attempt.unlink()
                except FileNotFoundError:
                    pass
                except BaseException as exc:
                    errors.append(exc)
            if previous_receipt is None:
                try:
                    receipt_path.unlink()
                except FileNotFoundError:
                    pass
                except BaseException as exc:
                    errors.append(exc)
            else:
                try:
                    temporary = receipt_path.with_suffix(receipt_path.suffix + ".rollback")
                    with temporary.open("wb") as stream:
                        stream.write(previous_receipt)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, receipt_path)
                except BaseException as exc:
                    errors.append(exc)
            for directory in {receipt_path.parent, created_attempt.parent if created_attempt else None}:
                if directory is None:
                    continue
                try:
                    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                except BaseException as exc:
                    errors.append(exc)
            try:
                if created_attempt is not None and created_attempt.exists():
                    raise OSError("published attempt remains after rollback")
                if previous_receipt is None and receipt_path.exists():
                    raise OSError("published receipt remains after rollback")
                if previous_receipt is not None and receipt_path.read_bytes() != previous_receipt:
                    raise OSError("prior receipt was not restored after rollback")
            except BaseException as exc:
                errors.append(exc)
            if errors:
                raise RuntimeError("cannot prove coding publication rollback") from errors[0]

        # Install the compensator while no success evidence exists.  From this
        # point every publication or durability failure is either proved clean
        # by the compensator or fenced by close_local_success under the row lock.
        register_undo(rollback_publication)
        if pending_attempt is not None:
            attempt = make_attempt(
                pending_attempt[1],
                pending_attempt[0],
                outcome=OUTCOME_SATISFIED,
                predecessor=pending_attempt[2],
                artifacts=pending_attempt[3],
            )
            # Bind the exact target before append_attempt creates it: an fsync
            # failure after O_EXCL must still be compensatable.  The registered
            # authoritative caller in close_local_success invokes rollback once.
            digest = attempt["attempt_hash"].removeprefix("sha256:")
            created_attempt = pending_attempt[0] / ".tgw-coding-history" / "implementation" / f"{len(history(pending_attempt[0])) + 1:06d}-{digest}.json"
            actual_attempt = append_attempt(pending_attempt[0], attempt)
            if actual_attempt != created_attempt:
                raise RuntimeError("attempt publication target changed")
        _write_receipt(receipt_path, pending[2])
        self._pending_success_attempt = None
        self._pending_success_receipt = None

    @staticmethod
    def _git_identity(path: Path) -> tuple[Path | None, Path | None]:
        """Return a worktree's top level and canonical common Git directory.

        TGW worktrees are deliberately shared by ordinary accounts through the
        ``tgw-coders`` Unix group.  Git's dubious-ownership check does not treat
        shared group membership as trust, so each probe must name the already
        path-fenced worktree explicitly.  This is a per-invocation trust fact;
        it does not add a broad system/global ``safe.directory`` exception.
        """
        try:
            probe = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={path.resolve()}",
                    "rev-parse",
                    "--show-toplevel",
                    "--git-common-dir",
                ],
                cwd=path,
                check=False,
                text=True,
                capture_output=True,
            )
        except OSError:
            return None, None
        if probe.returncode != 0:
            return None, None
        values = probe.stdout.splitlines()
        if len(values) != 2:
            return None, None
        top = Path(values[0]).resolve()
        common = Path(values[1])
        if not common.is_absolute():
            common = (path / common).resolve()
        else:
            common = common.resolve()
        return top, common

    def _validated_launcher_result(
        self,
        treatment_id: str,
        launcher_result: dict[str, Any] | None,
        *,
        payload: dict[str, Any] | None = None,
        worktree: Path | None = None,
        job_id: str = "",
    ) -> tuple[str, list[str], list[Any]]:
        if not isinstance(launcher_result, dict):
            raise HardFailure("coding launcher returned no structured outcome")
        outcome = launcher_result.get("outcome")
        established = launcher_result.get("established_conditions", [])
        artifacts = launcher_result.get("artifacts", [])
        if outcome not in _VALID_OUTCOMES:
            raise HardFailure("coding launcher returned invalid outcome")
        if not isinstance(established, list) or not all(isinstance(item, str) for item in established):
            raise HardFailure("coding launcher established_conditions must be a string list")
        if not isinstance(artifacts, list):
            raise HardFailure("coding launcher artifacts must be a list")
        allowed = _MAY_ESTABLISH[treatment_id]
        if not set(established).issubset(allowed):
            raise HardFailure("coding launcher claimed conditions outside treatment authority")
        if outcome != OUTCOME_SATISFIED and established:
            raise HardFailure("unsatisfied coding outcome cannot establish conditions")
        if outcome == OUTCOME_SATISFIED and not established:
            raise HardFailure("satisfied coding outcome must establish a condition")
        if (
            treatment_id == "claude-review"
            and outcome == OUTCOME_SATISFIED
            and isinstance(payload, dict)
            and payload.get("coding_lifecycle") is not None
        ):
            from tgw.development.coding_review import validate_review_artifact
            from tgw.review_contract import ReviewRunnerError

            try:
                validate_review_artifact(
                    launcher_result,
                    payload=payload,
                    worktree=worktree or Path.cwd(),
                    expected_job_id=job_id,
                )
            except ReviewRunnerError as exc:
                raise HardFailure(str(exc)) from exc
        return outcome, established, artifacts

    @staticmethod
    def _validated_plan_binding(payload: dict[str, Any], worktree: Path) -> dict[str, Any] | None:
        """Validate optional Plan provenance against this exact local worktree."""
        raw_binding = payload.get("plan_binding")
        if raw_binding is None:
            return None
        try:
            binding = validate_plan_binding(raw_binding, todo_id=payload.get("todo_id"))
        except MalformedPlanBindingError as exc:
            raise HardFailure(str(exc)) from exc
        if binding["worktree"] != str(worktree):
            raise HardFailure("Plan binding worktree does not match coding job")
        identity = binding["worktree_identity"]
        if identity.get("worktree") not in (None, str(worktree)):
            raise HardFailure("Plan binding worktree identity does not match coding job")
        return binding

    def handle(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = dict(job.get("payload_json") or {})
        self._active_job = job
        treatment_id = str(payload.get("treatment_id") or self.queue_name)
        if treatment_id != self.queue_name:
            raise HardFailure(f"job treatment {treatment_id!r} does not match queue {self.queue_name!r}")
        if not isinstance(payload.get("graph_id"), str) or not payload["graph_id"]:
            raise HardFailure("coding job has no graph_id")
        if not isinstance(payload.get("object_generation"), str) or not payload["object_generation"]:
            raise HardFailure("coding job has no object_generation")
        worktree = self._validated_worktree(payload)
        plan_binding = self._validated_plan_binding(payload, worktree)
        lifecycle_binding = payload.get("coding_lifecycle")
        if lifecycle_binding is not None:
            if plan_binding is None:
                raise HardFailure("coding lifecycle job requires an exact Plan binding")
            try:
                from tgw.development.coding_lifecycle import (
                    LifecycleError,
                    validate_job_binding_payload,
                )

                validate_job_binding_payload(
                    lifecycle_binding,
                    plan_binding=plan_binding,
                )
                if treatment_id == "claude-review":
                    from tgw.development.coding_lifecycle import (
                        validate_candidate_job_binding,
                    )

                    candidate = source_fingerprint(worktree)
                    validate_candidate_job_binding(
                        payload.get("coding_candidate"),
                        lifecycle_binding=lifecycle_binding,
                        commit=candidate["head"],
                        tree=candidate["tree"],
                    )
            except (LifecycleError, TypeError, ValueError) as exc:
                raise HardFailure(str(exc)) from exc

        lineage_bound_treatment = (
            treatment_id == "claude-review"
            or (
                treatment_id in {"codex-implement", "controller-verify"}
                and payload.get("todo_id") is not None
            )
        )
        if lineage_bound_treatment:
            with exclusive_worktree_lease(worktree) as descriptor:
                previous = self._worktree_lease_fd
                self._worktree_lease_fd = descriptor
                try:
                    return self._handle_under_lease(job, payload, treatment_id, worktree, plan_binding)
                finally:
                    self._worktree_lease_fd = previous
        return self._handle_under_lease(job, payload, treatment_id, worktree, plan_binding)

    def _handle_under_lease(
        self,
        job: dict[str, Any],
        payload: dict[str, Any],
        treatment_id: str,
        worktree: Path,
        plan_binding: dict[str, Any] | None,
    ) -> dict[str, Any]:

        if treatment_id == "controller-verify" and plan_binding is not None:
            candidate = source_fingerprint(worktree)
            try:
                recover_implementation_receipt_projection(
                    worktree, base_commit=plan_binding["source_commit"],
                    candidate_commit=candidate["head"], candidate_tree=candidate["tree"],
                    expected={
                        "todo_id": payload.get("todo_id"),
                        "plan_commit": plan_binding["plan_commit"],
                        "solution_hash": plan_binding["solution_hash"],
                        "source_commit": plan_binding["source_commit"],
                        "source_tree": source_tree(worktree, plan_binding["source_commit"]),
                        "actor": payload.get("todo_agent"),
                        "worktree": str(worktree),
                        "treatment_id": "codex-implement",
                        "treatment_version": "1",
                    },
                )
                receipt_value = json.loads(receipt_path_for_treatment(worktree, "codex-implement").read_text(encoding="utf-8"))
                latest = validate_implementation_lineage(
                    worktree, base_commit=plan_binding["source_commit"],
                    candidate_commit=candidate["head"], candidate_tree=candidate["tree"],
                    receipt=receipt_value,
                    expected={
                        "todo_id": payload.get("todo_id"), "plan_commit": plan_binding["plan_commit"],
                        "solution_hash": plan_binding["solution_hash"], "source_commit": plan_binding["source_commit"],
                        "source_tree": source_tree(worktree, plan_binding["source_commit"]), "actor": payload.get("todo_agent"),
                        "worktree": str(worktree), "treatment_id": "codex-implement", "treatment_version": "1",
                    },
                )
                if payload.get("implementation_attempt_hash") != latest.get("attempt_hash"):
                    raise ValueError(
                        "controller implementation attempt hash is absent or stale"
                    )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise HardFailure(f"controller verification requires exact implementation lineage: {exc}") from exc

        attempt_binding = None
        predecessor = None
        remediation_parent: dict[str, str] | None = None
        if treatment_id == "codex-implement" and payload.get("todo_id") is not None:
            payload.setdefault("job_id", str(job.get("job_id") or ""))
            payload.setdefault("attempt_count", job.get("attempt_count"))
            if not isinstance(plan_binding, dict):
                raise HardFailure("Codex implementation attempt requires an exact Plan binding")
            source_commit = plan_binding["source_commit"]
            baseline_tree = subprocess.run(
                ["git", "rev-parse", f"{source_commit}^{{tree}}"],
                cwd=worktree,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            attempt_binding = {
                "job_id": str(job.get("job_id") or payload.get("job_id") or ""),
                "attempt_count": job.get("attempt_count", payload.get("attempt_count")),
                "todo_id": payload.get("todo_id"),
                "plan_commit": plan_binding["plan_commit"],
                "solution_hash": plan_binding["solution_hash"],
                "source_commit": source_commit,
                "source_tree": baseline_tree,
                "actor": payload.get("todo_agent"),
                "worktree": str(worktree),
                "treatment_id": treatment_id,
                "treatment_version": str(payload.get("treatment_version", "1")),
            }
            lineage_binding = {**attempt_binding, "job_id": None, "attempt_count": None}
            state = classify(worktree, lineage_binding)
            if state["state"] == "RESUMABLE_PARTIAL":
                if payload.get("resume_of") != state.get("resume_of") or payload.get("resume_fingerprint") != state.get("fingerprint"):
                    preservation_manifest(worktree, state, attempt_binding)
                    raise HardFailure("dirty Codex worktree is resumable only with its exact attempt hash and fingerprint")
                predecessor = state["predecessor"]
            elif state["state"] == "ABANDONED_CLEAN":
                if payload.get("resume_of") or payload.get("resume_fingerprint"):
                    raise HardFailure("clean initial Codex attempt cannot carry resume authority")
            elif state["state"] == "CLOSED_CANDIDATE":
                remediation = payload.get("task_spec", {}).get("remediation")
                candidate = state.get("source", {})
                if isinstance(remediation, dict):
                    if (
                        remediation.get("schema")
                        != "tgw-local-coding-remediation-intent/v1"
                        or remediation.get("root_id")
                        != payload.get("coding_lifecycle", {}).get("root_id")
                        or remediation.get("candidate_commit")
                        != candidate.get("head")
                        or remediation.get("candidate_tree")
                        != candidate.get("tree")
                        or not str(
                            remediation.get("failure_receipt_hash", "")
                        ).startswith("sha256:")
                    ):
                        raise HardFailure(
                            "Codex remediation intent does not bind the exact closed candidate"
                        )
                    predecessor = state["history"][-1]["attempt_hash"]
                    remediation_parent = {
                        "commit": candidate["head"],
                        "tree": candidate["tree"],
                        "failure_receipt_hash": remediation[
                            "failure_receipt_hash"
                        ],
                    }
                else:
                    self._raise_if_cancelled(job)
                    latest = state["history"][-1]
                    artifacts = [
                        *latest.get("artifacts", []),
                        {
                            "kind": "recovered_attempt",
                            "attempt_hash": latest["attempt_hash"],
                            "detail": "exact closed candidate recovered without rerunning Codex",
                        },
                    ]
                    receipt = {
                        "status": "PASS",
                        "treatment_id": treatment_id,
                        "treatment_version": str(payload.get("treatment_version", "1")),
                        "graph_id": payload.get("graph_id"),
                        "object_id": str(worktree),
                        "object_generation": payload.get("object_generation"),
                        "outcome": OUTCOME_SATISFIED,
                        "established_conditions": ["implemented"],
                        "artifacts": artifacts,
                        "receipt_schema_id": "receipt/tgw-development/v1",
                        "plan_binding": plan_binding,
                        **(
                            {"coding_lifecycle": dict(payload["coding_lifecycle"])}
                            if payload.get("coding_lifecycle") is not None
                            else {}
                        ),
                        **(
                            {"coding_candidate": dict(payload["coding_candidate"])}
                            if payload.get("coding_candidate") is not None
                            else {}
                        ),
                    }
                    self._persist_success_receipt(job, receipt_path_for_treatment(worktree, treatment_id), receipt)
                    return receipt
            else:
                preservation_manifest(worktree, state, attempt_binding)
                raise HardFailure(f"Codex implementation refuses {state['state']} worktree")

        try:
            launcher_result = self._launcher(treatment_id, payload, worktree)
            outcome, established, artifacts = self._validated_launcher_result(
                treatment_id,
                launcher_result,
                payload=payload,
                worktree=worktree,
                job_id=str(job.get("job_id") or payload.get("job_id") or ""),
            )
            if remediation_parent is not None and outcome == OUTCOME_SATISFIED:
                closed = [
                    item
                    for item in artifacts
                    if isinstance(item, dict)
                    and item.get("kind") == "closed_candidate"
                ]
                if len(closed) != 1 or closed[0].get("base_commit") != remediation_parent["commit"]:
                    raise HardFailure(
                        "Codex remediation did not close one exact successor"
                    )
                candidate = source_fingerprint(worktree)
                normalized = {
                    **closed[0],
                    "base_commit": attempt_binding["source_commit"],
                    "changed_paths": candidate_changed_paths(
                        worktree,
                        attempt_binding["source_commit"],
                        candidate["head"],
                    ),
                }
                artifacts = [
                    normalized if item is closed[0] else item
                    for item in artifacts
                ]
                artifacts.append(
                    {
                        "kind": "remediation_successor",
                        "parent_commit": remediation_parent["commit"],
                        "parent_tree": remediation_parent["tree"],
                        "failure_receipt_hash": remediation_parent[
                            "failure_receipt_hash"
                        ],
                    }
                )
            if attempt_binding is not None and outcome == OUTCOME_SATISFIED:
                candidate = source_fingerprint(worktree)
                closed = [item for item in artifacts if isinstance(item, dict) and item.get("kind") == "closed_candidate"]
                ancestor = subprocess.run(
                    [
                        "git",
                        "merge-base",
                        "--is-ancestor",
                        attempt_binding["source_commit"],
                        candidate["head"],
                    ],
                    cwd=worktree,
                    check=False,
                    capture_output=True,
                )
                if (
                    len(closed) != 1
                    or candidate["changed_paths"]
                    or candidate["head"] == attempt_binding["source_commit"]
                    or closed[0].get("commit") != candidate["head"]
                    or closed[0].get("tree") != candidate["tree"]
                    or ancestor.returncode != 0
                ):
                    raise HardFailure("satisfied Codex outcome is not the exact closed source descendant")
                try:
                    validate_closed_candidate(
                        worktree,
                        closed[0],
                        base_commit=attempt_binding["source_commit"],
                        candidate_commit=candidate["head"],
                        candidate_tree=candidate["tree"],
                    )
                except ValueError as exc:
                    raise HardFailure(str(exc)) from exc
        except JobCancelled:
            raise
        except (HardFailure, RuntimeError) as exc:
            # Persist a local mechanical failure so the next snapshot can
            # reevaluate from durable evidence.
            receipt = {
                "status": "FAIL",
                "treatment_id": treatment_id,
                "treatment_version": str(payload.get("treatment_version", "1")),
                "graph_id": payload.get("graph_id"),
                "object_id": str(worktree),
                "object_generation": payload.get("object_generation"),
                "outcome": OUTCOME_FAILED,
                "established_conditions": [],
                "artifacts": [{"kind": "mechanical_failure", "detail": str(exc)}],
                "receipt_schema_id": "receipt/tgw-development/v1",
                **({"plan_binding": plan_binding} if plan_binding else {}),
                **(
                    {"coding_lifecycle": dict(payload["coding_lifecycle"])}
                    if payload.get("coding_lifecycle") is not None
                    else {}
                ),
                **(
                    {"coding_candidate": dict(payload["coding_candidate"])}
                    if payload.get("coding_candidate") is not None
                    else {}
                ),
                **(
                    {"task_spec": dict(payload["task_spec"])}
                    if treatment_id == "claude-review"
                    and isinstance(payload.get("task_spec"), dict)
                    else {}
                ),
            }
            if attempt_binding is not None:
                attempt = make_attempt(
                    attempt_binding,
                    worktree,
                    outcome=OUTCOME_FAILED,
                    predecessor=predecessor,
                    artifacts=receipt["artifacts"],
                )
                append_attempt(worktree, attempt)
                receipt["implementation_attempt_hash"] = attempt["attempt_hash"]
            _write_receipt(
                receipt_path_for_treatment(worktree, treatment_id),
                receipt,
                predecessor=predecessor,
            )
            raise TreatmentFailure(
                f"coding treatment mechanical failure: {exc}", receipt
            ) from exc
        receipt = {
            "status": "PASS" if outcome == OUTCOME_SATISFIED else "FAIL",
            "treatment_id": treatment_id,
            "treatment_version": str(payload.get("treatment_version", "1")),
            "graph_id": payload.get("graph_id"),
            "object_id": str(worktree),
            "object_generation": payload.get("object_generation"),
            "outcome": outcome,
            "established_conditions": established,
            "artifacts": artifacts,
            "receipt_schema_id": "receipt/tgw-development/v1",
            **({"plan_binding": plan_binding} if plan_binding else {}),
            **(
                {"implementation_attempt_hash": payload.get("implementation_attempt_hash")}
                if treatment_id == "controller-verify"
                else {}
            ),
            **(
                {"coding_lifecycle": dict(payload["coding_lifecycle"])}
                if payload.get("coding_lifecycle") is not None
                else {}
            ),
            **(
                {"coding_candidate": dict(payload["coding_candidate"])}
                if payload.get("coding_candidate") is not None
                else {}
            ),
            **(
                {"task_spec": dict(payload["task_spec"])}
                if treatment_id == "claude-review"
                and isinstance(payload.get("task_spec"), dict)
                else {}
            ),
        }
        self._raise_if_cancelled(job)
        if attempt_binding is not None and outcome == OUTCOME_SATISFIED and job.get("lease_token"):
            self._pending_success_attempt = (
                worktree,
                attempt_binding,
                predecessor,
                artifacts,
            )
        elif attempt_binding is not None:
            attempt = make_attempt(
                attempt_binding,
                worktree,
                outcome=outcome,
                predecessor=predecessor,
                artifacts=artifacts,
            )
            append_attempt(worktree, attempt)
            receipt["implementation_attempt_hash"] = attempt["attempt_hash"]
        if outcome == OUTCOME_SATISFIED:
            self._persist_success_receipt(job, receipt_path_for_treatment(worktree, treatment_id), receipt)
        else:
            _write_receipt(
                receipt_path_for_treatment(worktree, treatment_id),
                receipt,
                predecessor=predecessor,
            )
        if outcome != OUTCOME_SATISFIED:
            # A negative launcher result is a terminal job outcome, not a
            # successful queue delivery with a disappointing payload.
            raise TreatmentFailure(
                f"coding treatment reported {outcome}", receipt
            )
        return receipt


def execute_treatment(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry point for one local registered treatment."""
    treatment_id = payload.get("treatment_id")
    if not isinstance(treatment_id, str) or treatment_id not in CODING_TREATMENTS:
        raise HardFailure("coding job payload has no registered treatment")
    return CodingWorker(treatment_id, config).handle(
        {
            "job_id": payload.get("job_id") or payload.get("graph_id"),
            "attempt_count": payload.get("attempt_count", 1),
            "payload_json": payload,
        }
    )


def main() -> int:
    """Run one named coding queue under the ordinary queue-worker contract."""
    import argparse

    from tgw.config import DEFAULT_CONFIG, load_coding_worker_config

    parser = argparse.ArgumentParser(prog="tgw-coding-worker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--queue", required=True, choices=sorted(CODING_TREATMENTS))
    args = parser.parse_args()
    worker = CodingWorker(args.queue, load_coding_worker_config(Path(args.config)))
    # Refuse to start a queue without its configured local runner.
    worker._configured_command(args.queue)
    worker.run()
    return 0


def _main_for_queue(queue_name: str) -> int:
    """Systemd-template entry point for one registered coding queue."""
    import argparse

    from tgw.config import DEFAULT_CONFIG, load_coding_worker_config

    parser = argparse.ArgumentParser(prog=f"tgw-{queue_name}-worker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    worker = CodingWorker(queue_name, load_coding_worker_config(Path(args.config)))
    worker._configured_command(queue_name)
    worker.run()
    return 0


def codex_implement_main() -> int:
    return _main_for_queue("codex-implement")


def claude_review_main() -> int:
    return _main_for_queue("claude-review")


def controller_verify_main() -> int:
    return _main_for_queue("controller-verify")


def hermes_stitch_main() -> int:
    return _main_for_queue("hermes-stitch")


if __name__ == "__main__":
    raise SystemExit(main())

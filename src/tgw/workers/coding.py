"""Queue worker for the executable coding-treatment lane.

Each configured instance claims exactly one treatment queue (for example,
``claude-review``).  The launcher is deliberately a narrow seam: deployments
provide an argv list for each treatment and this worker executes it in the
target worktree, then persists a receipt which the coding snapshot reads.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tgw.development.plan_binding import MalformedPlanBindingError, validate_plan_binding
from tgw.queue.worker_base import HardFailure, QueueWorker
from tgw.workflow_kernel.contracts import (
    OUTCOME_CONFLICT,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_SATISFIED,
)

CODING_TREATMENTS = frozenset({"codex-implement", "claude-review", "controller-verify", "hermes-stitch"})

DEFAULT_WORKTREE_ROOT = Path("/opt/TGW/var/worktrees")
DEFAULT_REPOSITORY_ROOT = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")

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


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    """Atomically persist a worktree receipt for the snapshot reader."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


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
            completed = subprocess.run(
                command,
                cwd=worktree,
                check=False,
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds(),
                env={
                    **os.environ,
                    "TGW_CODING_JOB": json.dumps(payload),
                    "TGW_CODING_WORKTREE_SRC": str(worktree / "src"),
                },
            )
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
        return command

    def _validated_worktree(self, payload: dict[str, Any]) -> Path:
        return validated_coding_worktree(
            payload.get("worktree"),
            payload.get("object_id"),
            self._coding_config(),
        )

    @staticmethod
    def _git_identity(path: Path) -> tuple[Path | None, Path | None]:
        """Return a worktree's top level and canonical common Git directory."""
        try:
            probe = subprocess.run(
                ["git", "rev-parse", "--show-toplevel", "--git-common-dir"],
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
        treatment_id = str(payload.get("treatment_id") or self.queue_name)
        if treatment_id != self.queue_name:
            raise HardFailure(f"job treatment {treatment_id!r} does not match queue {self.queue_name!r}")
        if not isinstance(payload.get("graph_id"), str) or not payload["graph_id"]:
            raise HardFailure("coding job has no graph_id")
        if not isinstance(payload.get("object_generation"), str) or not payload["object_generation"]:
            raise HardFailure("coding job has no object_generation")
        worktree = self._validated_worktree(payload)
        plan_binding = self._validated_plan_binding(payload, worktree)

        try:
            launcher_result = self._launcher(treatment_id, payload, worktree)
            outcome, established, artifacts = self._validated_launcher_result(
                treatment_id,
                launcher_result,
            )
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
            }
            _write_receipt(receipt_path_for_treatment(worktree, treatment_id), receipt)
            raise HardFailure(f"coding treatment mechanical failure: {exc}") from exc
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
        }
        _write_receipt(receipt_path_for_treatment(worktree, treatment_id), receipt)
        if outcome != OUTCOME_SATISFIED:
            # A negative launcher result is a terminal job outcome, not a
            # successful queue delivery with a disappointing payload.
            raise HardFailure(f"coding treatment reported {outcome}")
        return receipt


def execute_treatment(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry point for one local registered treatment."""
    treatment_id = payload.get("treatment_id")
    if not isinstance(treatment_id, str) or treatment_id not in CODING_TREATMENTS:
        raise HardFailure("coding job payload has no registered treatment")
    return CodingWorker(treatment_id, config).handle({"payload_json": payload})


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

"""Bounded broker lifecycle: provider completion precedes final receipt read."""

from __future__ import annotations

import json
import select
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class BrokerSupervisorError(RuntimeError):
    pass


def _await_ready(process: subprocess.Popen, timeout: float) -> None:
    """Require the serving broker to bind before the provider can use it."""

    output = getattr(process, "stdout", None)
    if output is None or not hasattr(output, "fileno"):
        # Test doubles exercise termination/receipt semantics without a pipe.
        return
    readable, _, _ = select.select([output], [], [], timeout)
    if not readable:
        raise BrokerSupervisorError("broker did not become ready before provider launch")
    try:
        ready = json.loads(output.readline())
    except (OSError, json.JSONDecodeError) as exc:
        raise BrokerSupervisorError("broker readiness record is invalid") from exc
    if set(ready) != {"status", "policy_hash"} or ready.get("status") != "READY":
        raise BrokerSupervisorError("broker readiness record is invalid")


def run_with_broker(
    broker_argv: Sequence[str],
    provider: Callable[[], Any],
    receipt_path: Path,
    *,
    spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
    stop_timeout: float = 10,
) -> tuple[Any, Mapping[str, Any]]:
    """Start exact broker argv, run provider, terminate, then read receipt."""
    if not broker_argv or not all(isinstance(item, str) and item for item in broker_argv):
        raise BrokerSupervisorError("broker argv is invalid")
    if receipt_path.exists() or not receipt_path.parent.is_dir():
        raise BrokerSupervisorError("broker receipt sink must be absent in a provisioned directory")
    process = spawn(list(broker_argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    provider_result = None
    provider_error = None
    try:
        _await_ready(process, stop_timeout)
        provider_result = provider()
    except BaseException as exc:
        provider_error = exc
    finally:
        process.terminate()
        try:
            process.wait(timeout=stop_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=stop_timeout)
    if not receipt_path.is_file():
        raise BrokerSupervisorError("broker terminated without a final audit receipt") from provider_error
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrokerSupervisorError("broker audit receipt is invalid JSON") from exc
    if provider_error is not None:
        raise provider_error
    return provider_result, receipt

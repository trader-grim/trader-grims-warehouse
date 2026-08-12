"""Bounded broker lifecycle: provider completion precedes final receipt read."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class BrokerSupervisorError(RuntimeError):
    pass


def run_with_broker(
    broker_argv: Sequence[str],
    provider: Callable[[], Any],
    receipt_path: Path,
    ready_path: Path,
    expected_run_id: str,
    expected_policy_hash: str,
    *,
    spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
    verify_process_socket: Callable[[subprocess.Popen, Mapping[str, Any]], bool] | None = None,
    stop_timeout: float = 10,
) -> tuple[Any, Mapping[str, Any]]:
    """Start exact broker argv, run provider, terminate, then read receipt."""
    if not broker_argv or not all(isinstance(item, str) and item for item in broker_argv):
        raise BrokerSupervisorError("broker argv is invalid")
    if receipt_path.exists() or ready_path.exists() or not receipt_path.parent.is_dir():
        raise BrokerSupervisorError("broker receipt sink must be absent in a provisioned directory")
    process = spawn(list(broker_argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    deadline = time.monotonic() + stop_timeout
    ready = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BrokerSupervisorError("broker exited before authenticated readiness")
        if ready_path.is_file():
            try:
                ready = json.loads(ready_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise BrokerSupervisorError("broker readiness is invalid JSON") from exc
            break
        time.sleep(0.01)
    if (
        not isinstance(ready, Mapping)
        or set(ready) != {"schema", "run_id", "policy_hash", "attestation_signature", "broker_identity", "broker_bind"}
        or ready["schema"] != "tgw-review-egress-ready/v1"
        or ready["run_id"] != expected_run_id
        or ready["policy_hash"] != expected_policy_hash
        or not str(ready["attestation_signature"]).startswith("ed25519:")
        or not isinstance(ready["broker_identity"], Mapping)
    ):
        process.terminate()
        raise BrokerSupervisorError("broker readiness is absent or not bound to the run policy")
    def strict_verifier(proc, value):
        identity = value["broker_identity"]
        socket = identity.get("socket") if isinstance(identity, Mapping) else None
        bind = value["broker_bind"]
        return (
            set(identity) == {"pid", "uid", "cgroup", "starttime", "exe_sha256", "socket"}
            and isinstance(socket, Mapping)
            and set(socket) == {"pid", "uid", "inode", "local_ip", "local_port", "state"}
            and identity["pid"] == getattr(proc, "pid", None) == socket["pid"]
            and identity["uid"] == socket["uid"] == 972
            and identity["cgroup"] == f"tgw-review-egress@{expected_run_id}.service"
            and isinstance(identity["starttime"], int) and identity["starttime"] > 0
            and isinstance(identity["exe_sha256"], str) and identity["exe_sha256"].startswith("sha256:") and len(identity["exe_sha256"]) == 71
            and isinstance(socket["inode"], int) and socket["inode"] > 0
            and socket["local_ip"] == bind.get("host")
            and socket["local_port"] == bind.get("port")
            and socket["state"] == "LISTEN"
        )
    verifier = verify_process_socket or strict_verifier
    if not verifier(process, ready):
        process.terminate()
        raise BrokerSupervisorError("broker readiness process or listening socket ownership mismatch")
    provider_result = None
    provider_error = None
    try:
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

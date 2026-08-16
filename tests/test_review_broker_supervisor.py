import asyncio
import json
import os
import signal
import socket
import subprocess
import time
from unittest.mock import Mock

import pytest

from tgw.review_broker_supervisor import BrokerSupervisorError, run_with_broker
from tgw.review_egress_broker import ReviewEgressPolicy, serve


class Process:
    def __init__(self, receipt, emit=True):
        self.receipt, self.emit = receipt, emit
        self.terminated = False

    def terminate(self):
        self.terminated = True
        if self.emit:
            self.receipt.write_text(json.dumps({"schema": "tgw-review-egress-receipt/v1"}))

    def wait(self, timeout):
        assert self.terminated
        return 0

    def kill(self):
        self.terminated = True


def test_provider_completion_terminates_broker_before_final_receipt_read(tmp_path):
    receipt = tmp_path / "egress.json"
    process = Process(receipt)
    provider = Mock(return_value={"verdict": "PASS"})
    result, audit = run_with_broker(["broker", "--exact"], provider, receipt, spawn=lambda *a, **k: process)
    assert result == {"verdict": "PASS"}
    assert process.terminated and audit["schema"] == "tgw-review-egress-receipt/v1"


def test_missing_receipt_fails_closed_even_after_successful_provider(tmp_path):
    receipt = tmp_path / "egress.json"
    with pytest.raises(BrokerSupervisorError, match="without a final audit"):
        run_with_broker(["broker"], lambda: "PASS", receipt, spawn=lambda *a, **k: Process(receipt, emit=False))


def test_timeout_kills_broker_and_provider_error_is_preserved_after_receipt(tmp_path):
    receipt = tmp_path / "egress.json"
    process = Process(receipt)
    process.wait = Mock(side_effect=[subprocess.TimeoutExpired("broker", 1), 0])
    with pytest.raises(RuntimeError, match="provider failed"):
        run_with_broker(["broker"], lambda: (_ for _ in ()).throw(RuntimeError("provider failed")), receipt, spawn=lambda *a, **k: process)
    process.kill


def test_real_broker_sigterm_writes_final_receipt(tmp_path):
    policy = ReviewEgressPolicy.parse({
        "run_id": "real-signal-test",
        "allowed_hosts": ["chatgpt.com"],
        "expires_unix": int(time.time()) + 60,
        "max_connections": 1,
        "max_bytes_each_direction": 65536,
        "runtime_sha256": "sha256:" + "a" * 64,
        "credential_sha256": "sha256:" + "b" * 64,
    })
    receipt = tmp_path / "receipt.json"
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    pid = os.fork()
    if pid == 0:
        try:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            asyncio.run(serve(policy, "127.0.0.1", port, receipt))
        finally:
            os._exit(0)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.01)
        else:
            raise AssertionError("real review broker did not become ready")
        os.kill(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
        assert json.loads(receipt.read_text())["run_id"] == policy.run_id
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

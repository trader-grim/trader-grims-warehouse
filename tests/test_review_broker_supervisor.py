import json
import subprocess
from unittest.mock import Mock

import pytest

from tgw.review_broker_supervisor import BrokerSupervisorError, run_with_broker


class Process:
    def __init__(self, receipt, ready, emit=True, policy_hash="sha256:policy", mutate=None):
        self.receipt, self.emit = receipt, emit
        self.terminated = False
        self.pid = 123
        self.ready, self.policy_hash, self.mutate = ready, policy_hash, mutate

    def start(self):
        value = {
                    "schema": "tgw-review-egress-ready/v1",
                    "run_id": "run-1",
                    "policy_hash": self.policy_hash,
                    "attestation_signature": "ed25519:x",
                    "broker_identity": {
                        "pid": 123, "uid": 972, "cgroup": "tgw-review-egress@run-1.service", "starttime": 42,
                        "exe_sha256": "sha256:" + "a" * 64,
                        "socket": {"pid": 123, "uid": 972, "inode": 9, "local_ip": "169.254.1.1", "local_port": 18443, "state": "LISTEN"},
                    },
                    "broker_bind": {"host": "169.254.1.1", "port": 18443},
                }
        if self.mutate:
            self.mutate(value)
        self.ready.write_text(json.dumps(value))
        return self

    def poll(self):
        return None

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
    ready = tmp_path / "ready.json"
    process = Process(receipt, ready)
    provider = Mock(return_value={"verdict": "PASS"})
    result, audit = run_with_broker(["broker", "--exact"], provider, receipt, ready_path=ready, expected_run_id="run-1", expected_policy_hash="sha256:policy", spawn=lambda *a, **k: process.start())
    assert result == {"verdict": "PASS"}
    assert process.terminated and audit["schema"] == "tgw-review-egress-receipt/v1"


def test_missing_receipt_fails_closed_even_after_successful_provider(tmp_path):
    receipt = tmp_path / "egress.json"
    ready = tmp_path / "ready.json"
    with pytest.raises(BrokerSupervisorError, match="without a final audit"):
        run_with_broker(
            ["broker"], lambda: "PASS", receipt, ready_path=ready, expected_run_id="run-1", expected_policy_hash="sha256:policy", spawn=lambda *a, **k: Process(receipt, ready, emit=False).start()
        )


def test_timeout_kills_broker_and_provider_error_is_preserved_after_receipt(tmp_path):
    receipt = tmp_path / "egress.json"
    ready = tmp_path / "ready.json"
    process = Process(receipt, ready)
    process.wait = Mock(side_effect=[subprocess.TimeoutExpired("broker", 1), 0])
    with pytest.raises(RuntimeError, match="provider failed"):
        run_with_broker(
            ["broker"],
            lambda: (_ for _ in ()).throw(RuntimeError("provider failed")),
            receipt,
            ready_path=ready,
            expected_run_id="run-1",
            expected_policy_hash="sha256:policy",
            spawn=lambda *a, **k: process.start(),
        )
    process.kill


def test_wrong_policy_readiness_never_starts_provider(tmp_path):
    receipt, ready = tmp_path / "egress.json", tmp_path / "ready.json"
    process = Process(receipt, ready, policy_hash="sha256:wrong")
    provider = Mock()
    with pytest.raises(BrokerSupervisorError, match="not bound"):
        run_with_broker(["broker"], provider, receipt, ready_path=ready, expected_run_id="run-1", expected_policy_hash="sha256:policy", spawn=lambda *a, **k: process.start(), stop_timeout=0.1)
    provider.assert_not_called()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v["broker_identity"].update(pid=312),
        lambda v: v["broker_identity"].update(uid=1972),
        lambda v: v["broker_identity"].update(starttime=0),
        lambda v: v["broker_identity"]["socket"].update(pid=312),
        lambda v: v["broker_identity"]["socket"].update(uid=1972),
        lambda v: v["broker_identity"]["socket"].update(inode=0),
        lambda v: v["broker_identity"]["socket"].update(local_ip="169.254.1.10"),
    ],
)
def test_collision_stale_and_swapped_identity_records_fail_closed(tmp_path, mutate):
    receipt, ready = tmp_path / "egress.json", tmp_path / "ready.json"
    process = Process(receipt, ready, mutate=mutate)
    provider = Mock()
    with pytest.raises(BrokerSupervisorError, match="ownership mismatch"):
        run_with_broker(["broker"], provider, receipt, ready, "run-1", "sha256:policy", spawn=lambda *a, **k: process.start(), stop_timeout=0.1)
    provider.assert_not_called()

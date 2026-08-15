import json
import subprocess
from unittest.mock import Mock

import pytest

from tgw.review_broker_supervisor import BrokerSupervisorError, run_with_broker


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

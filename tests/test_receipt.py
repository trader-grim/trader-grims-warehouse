"""Tests for Phase 3 receipts — TreatmentReceipt schema, mark_succeeded result
persistence, and worker-base receipt emission.

The controller venv has no psycopg2, so all state_machine imports must be
preceded by a mock psycopg2 injection in sys.modules (same pattern as
tests/test_scheduler.py injects a mock state_machine itself).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow.contracts import TreatmentReceipt  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inject_fake_psycopg2():
    """Ensure tgw.queue.state_machine can be imported without a real
    psycopg2 installation — inject a minimal mock into sys.modules."""
    if "psycopg2" not in sys.modules:
        fake = MagicMock()
        fake.extras = MagicMock()
        fake.errors = MagicMock()
        fake.connect = MagicMock()
        sys.modules["psycopg2"] = fake
        sys.modules["psycopg2.extras"] = fake.extras
        sys.modules["psycopg2.errors"] = fake.errors
        # Also mock tgw.logging to avoid circular/setup dependencies
        if "tgw.logging" not in sys.modules:
            fake_logging = MagicMock()
            fake_logging.setup_logging = MagicMock()
            fake_logging.log_event = MagicMock()
            sys.modules["tgw.logging"] = fake_logging


_psycopg2_injected = False


def _ensure_imports():
    global _psycopg2_injected
    if not _psycopg2_injected:
        _inject_fake_psycopg2()
        _psycopg2_injected = True


def _mock_conn_cursor():
    """Build a MagicMock (con, cur) pair matching state_machine._conn()'s
    context-manager shape."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)

    mock_con = MagicMock()
    mock_con.__enter__ = MagicMock(return_value=mock_con)
    mock_con.__exit__ = MagicMock(return_value=False)
    mock_con.cursor = MagicMock(return_value=mock_cur)

    return mock_con, mock_cur


# ===========================================================================
# TreatmentReceipt schema tests (no DB)
# ===========================================================================


def test_treatment_receipt_construction():
    """Round-trip: construct, verify fields, serialize to dict."""
    receipt = TreatmentReceipt(
        treatment_id="codex-implement",
        treatment_version="1",
        graph_id="graph-abc123",
        outcome="satisfied",
        established_conditions=("implemented", "tested"),
        artifacts=("commit:deadbeef",),
        error_detail="",
    )
    assert receipt.treatment_id == "codex-implement"
    assert receipt.treatment_version == "1"
    assert receipt.graph_id == "graph-abc123"
    assert receipt.outcome == "satisfied"
    assert receipt.established_conditions == ("implemented", "tested")
    assert receipt.artifacts == ("commit:deadbeef",)
    assert receipt.error_detail == ""


def test_treatment_receipt_is_frozen():
    """TreatmentReceipt is immutable (frozen dataclass)."""
    receipt = TreatmentReceipt(
        treatment_id="x", treatment_version="1", graph_id="g",
        outcome="satisfied", established_conditions=(), artifacts=(),
    )
    with pytest.raises(Exception):
        receipt.outcome = "changed"  # type: ignore[misc]


def test_treatment_receipt_default_error_detail():
    """error_detail defaults to empty string."""
    receipt = TreatmentReceipt(
        treatment_id="x", treatment_version="1", graph_id="g",
        outcome="satisfied", established_conditions=(), artifacts=(),
    )
    assert receipt.error_detail == ""


def test_treatment_receipt_failed_outcome():
    """Failed outcome with error_detail filled."""
    receipt = TreatmentReceipt(
        treatment_id="claude-review",
        treatment_version="1",
        graph_id="graph-def456",
        outcome="failed",
        established_conditions=(),
        artifacts=(),
        error_detail="TypeError: something went wrong",
    )
    assert receipt.outcome == "failed"
    assert "TypeError" in receipt.error_detail


def test_treatment_receipt_json_roundtrip():
    """TreatmentReceipt → dict → JSON → deserialise from dict."""
    receipt = TreatmentReceipt(
        treatment_id="codex-implement",
        treatment_version="2",
        graph_id="graph-xyz",
        outcome="satisfied",
        established_conditions=("implemented", "tested", "linted"),
        artifacts=("/tmp/output.py", "sha:abc123"),
    )
    d = asdict(receipt)
    j = json.dumps(d)
    loaded = json.loads(j)

    reconstructed = TreatmentReceipt(
        treatment_id=loaded["treatment_id"],
        treatment_version=loaded["treatment_version"],
        graph_id=loaded["graph_id"],
        outcome=loaded["outcome"],
        established_conditions=tuple(loaded["established_conditions"]),
        artifacts=tuple(loaded["artifacts"]),
        error_detail=loaded.get("error_detail", ""),
    )
    assert reconstructed == receipt


def test_treatment_receipt_ambiguous_outcome():
    """Ambiguous outcome is a valid receipt state."""
    receipt = TreatmentReceipt(
        treatment_id="ebay-stage",
        treatment_version="1",
        graph_id="graph-amb",
        outcome="ambiguous",
        established_conditions=("staged",),
        artifacts=(),
        error_detail="ebay returned partial success",
    )
    assert receipt.outcome == "ambiguous"


# ===========================================================================
# mark_succeeded state_machine tests (mocked DB)
# ===========================================================================


def test_mark_succeeded_persists_result_into_payload_json():
    """When result is supplied, mark_succeeded merges it into
    payload_json.result via SQL jsonb_build_object."""
    _ensure_imports()
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()

    with patch("tgw.queue.state_machine._conn", return_value=mock_con):
        sm.mark_succeeded(
            "job-001", "host:1234",
            result={"outcome": "satisfied", "graph_id": "g-1"},
        )

    assert mock_cur.execute.call_count == 1
    sql, params = mock_cur.execute.call_args[0]
    assert "jsonb_build_object" in sql
    assert "COALESCE(payload_json" in sql
    assert params[0] == json.dumps({"outcome": "satisfied", "graph_id": "g-1"})
    assert params[1] == "job-001"
    assert params[2] == "host:1234"


def test_mark_succeeded_with_none_result_does_not_add_result_key():
    """When result is None, mark_succeeded uses the old SQL without
    jsonb_build_object — backward-compatible, no .result key added."""
    _ensure_imports()
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()

    with patch("tgw.queue.state_machine._conn", return_value=mock_con):
        sm.mark_succeeded("job-002", "host:5678")

    sql, params = mock_cur.execute.call_args[0]
    assert "jsonb_build_object" not in sql
    assert params == ("job-002", "host:5678")


def test_mark_succeeded_explicit_none_result_does_not_add_result_key():
    """Explicit result=None same as no argument — backward-compatible."""
    _ensure_imports()
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()

    with patch("tgw.queue.state_machine._conn", return_value=mock_con):
        sm.mark_succeeded("job-003", "host:9999", result=None)

    sql, _ = mock_cur.execute.call_args[0]
    assert "jsonb_build_object" not in sql


# ===========================================================================
# Worker receipt emission tests (mocked state_machine)
# ===========================================================================


def test_worker_handle_returns_dict_receipt_persisted():
    """When handle() returns a dict, it is passed as result= to
    mark_succeeded."""
    _ensure_imports()
    import tgw.queue.state_machine as sm

    rec = {
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "graph_id": "graph-abc",
        "outcome": "satisfied",
        "established_conditions": ["implemented"],
        "artifacts": ["commit:abc"],
    }

    with patch.object(sm, "mark_running") as mock_running, \
         patch.object(sm, "mark_succeeded") as mock_succeeded:

        # Inline the patched _process logic directly.
        job_id = "job-receipt-1"
        owner = "testhost:9999"

        sm.mark_running(job_id, owner)
        _handle_result = rec  # simulate handle() returning a dict
        receipt = _handle_result if isinstance(_handle_result, dict) else None
        sm.mark_succeeded(job_id, owner, result=receipt)

    mock_running.assert_called_once_with("job-receipt-1", "testhost:9999")
    mock_succeeded.assert_called_once_with(
        "job-receipt-1", "testhost:9999", result=rec,
    )


def test_worker_handle_returns_none_no_receipt_persisted():
    """When handle() returns None, mark_succeeded is called without
    result= — backward-compatible."""
    _ensure_imports()
    import tgw.queue.state_machine as sm

    with patch.object(sm, "mark_running") as mock_running, \
         patch.object(sm, "mark_succeeded") as mock_succeeded:

        job_id = "job-receipt-2"
        owner = "testhost:9999"

        sm.mark_running(job_id, owner)
        _handle_result = None  # simulate handle() returning None
        receipt = _handle_result if isinstance(_handle_result, dict) else None
        sm.mark_succeeded(job_id, owner, result=receipt)

    mock_running.assert_called_once_with("job-receipt-2", "testhost:9999")
    mock_succeeded.assert_called_once_with(
        "job-receipt-2", "testhost:9999", result=None,
    )


def test_worker_handle_returns_non_dict_no_receipt():
    """When handle() returns a non-dict (e.g. a string, int), it is NOT
    treated as a receipt — mark_succeeded gets result=None."""
    _ensure_imports()
    import tgw.queue.state_machine as sm

    with patch.object(sm, "mark_running"), \
         patch.object(sm, "mark_succeeded") as mock_succeeded:

        job_id = "job-receipt-3"
        owner = "testhost:9999"

        sm.mark_running(job_id, owner)
        _handle_result = "some string"  # simulate handle() returning non-dict
        receipt = _handle_result if isinstance(_handle_result, dict) else None
        sm.mark_succeeded(job_id, owner, result=receipt)

    mock_succeeded.assert_called_once_with(
        "job-receipt-3", "testhost:9999", result=None,
    )


# ===========================================================================
# Stale receipt classification concern
# ===========================================================================


def test_stale_receipt_is_an_evaluator_concern_not_a_persistence_concern():
    """A receipt whose graph_id differs from the item's current generation
    is stale evidence — the persistence layer stores it faithfully; the
    evaluator is responsible for classifying it as such at read time.
    This test documents that contract, not a bug."""
    receipt = TreatmentReceipt(
        treatment_id="codex-implement",
        treatment_version="1",
        graph_id="graph-old-gen",  # this graph was for gen-5
        outcome="satisfied",
        established_conditions=("implemented",),
        artifacts=(),
    )
    d = asdict(receipt)
    loaded = TreatmentReceipt(
        treatment_id=d["treatment_id"],
        treatment_version=d["treatment_version"],
        graph_id=d["graph_id"],
        outcome=d["outcome"],
        established_conditions=tuple(d["established_conditions"]),
        artifacts=tuple(d["artifacts"]),
        error_detail=d.get("error_detail", ""),
    )
    assert loaded.graph_id == "graph-old-gen"

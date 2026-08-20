from datetime import datetime, timezone

import pytest

from tgw.lifecycle_snapshot import LifecycleSnapshotError, compile_lifecycle_snapshot


NOW = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)


def _row(*, outcome=None, expires_at="2026-08-20T21:00:00Z"):
    return {
        "request_id": "request-one", "effect_hash": "sha256:" + "1" * 64,
        "effect_generation": "sha256:" + "2" * 64, "decision_kind": "approve",
        "receipt_id": None, "completed_at": None, "outcome": outcome, "expires_at": expires_at,
        "effect_parameters": {
            "lifecycle": {
                "lifecycle_hash": "sha256:" + "3" * 64,
                "launch_cards": [{
                    "unit": "W18", "role": "implementer", "idempotency_key": "sha256:" + "4" * 64,
                    "state": "PREPARED", "lease": {"id": "lease-one", "expires_at": "2026-08-20T20:30:00Z"},
                }],
            },
        },
    }


def test_snapshot_captures_live_request_role_and_continuation():
    snapshot = compile_lifecycle_snapshot(
        generation="sha256:" + "a" * 64, rows=[_row()], surfaces=[], observed_at=NOW,
    )
    assert snapshot["schema"] == "tgw-w18-lifecycle-snapshot/v1"
    assert snapshot["collections"]["live_requests"][0]["request_id"] == "request-one"
    assert snapshot["collections"]["role_leases"][0]["lease_id"] == "lease-one"
    assert snapshot["collections"]["continuations"][0]["unit"] == "W18"
    assert snapshot["snapshot_hash"].startswith("sha256:")


def test_snapshot_omits_terminal_request_and_rejects_invalid_expiry():
    terminal = compile_lifecycle_snapshot(
        generation="sha256:" + "a" * 64, rows=[_row(outcome="succeeded")], surfaces=[], observed_at=NOW,
    )
    assert terminal["collections"] == {
        "live_requests": [], "role_leases": [], "rendered_surfaces": [], "continuations": [],
    }
    with pytest.raises(LifecycleSnapshotError, match="authority lifecycle row"):
        compile_lifecycle_snapshot(
            generation="sha256:" + "a" * 64, rows=[_row(expires_at="invalid")], surfaces=[], observed_at=NOW,
        )

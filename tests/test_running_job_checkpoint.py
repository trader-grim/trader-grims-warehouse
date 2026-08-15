from unittest.mock import MagicMock, patch

import pytest

from tgw.queue import state_machine


def _connection(*rows):
    cursor = MagicMock()
    cursor.fetchone.side_effect = rows
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
    return connection, cursor


def test_checkpoint_running_job_is_exact_lease_fenced_and_persists_once():
    checkpoint = {"schema_id": "test/v1", "offer": {"offerId": "O-1"}}
    connection, cursor = _connection(
        {"payload_json": {"sku": "S-1"}}, {"checkpoint": checkpoint},
    )
    with patch("tgw.queue.state_machine._conn", return_value=connection):
        assert state_machine.checkpoint_running_job(
            "job-1", "owner-1", "11111111-1111-1111-1111-111111111111",
            checkpoint,
        ) == checkpoint
    assert cursor.execute.call_count == 2
    select_sql, select_params = cursor.execute.call_args_list[0].args
    assert "lease_token = %s::uuid" in select_sql
    assert "lease_expires_at > NOW()" in select_sql
    assert select_params[-1] == "11111111-1111-1111-1111-111111111111"
    assert "jsonb_set" in cursor.execute.call_args_list[1].args[0]


def test_checkpoint_running_job_exact_replay_does_not_rewrite():
    checkpoint = {"schema_id": "test/v1", "value": 1}
    connection, cursor = _connection(
        {"payload_json": {"observation_checkpoint": checkpoint}},
    )
    with patch("tgw.queue.state_machine._conn", return_value=connection):
        assert state_machine.checkpoint_running_job(
            "job-1", "owner-1", "11111111-1111-1111-1111-111111111111",
            checkpoint,
        ) == checkpoint
    assert cursor.execute.call_count == 1


def test_checkpoint_running_job_rejects_mismatch():
    connection, _ = _connection(
        {"payload_json": {"observation_checkpoint": {"value": 1}}},
    )
    with patch("tgw.queue.state_machine._conn", return_value=connection), \
         pytest.raises(ValueError, match="conflicts"):
        state_machine.checkpoint_running_job(
            "job-1", "owner-1", "11111111-1111-1111-1111-111111111111",
            {"value": 2},
        )


def test_checkpoint_exact_replay_distinguishes_boolean_from_integer():
    connection, _ = _connection(
        {"payload_json": {"observation_checkpoint": {"value": True}}},
    )
    with patch("tgw.queue.state_machine._conn", return_value=connection), \
         pytest.raises(ValueError, match="conflicts"):
        state_machine.checkpoint_running_job(
            "job-1", "owner-1", "11111111-1111-1111-1111-111111111111",
            {"value": 1},
        )


def test_checkpoint_distinguishes_missing_from_durable_null():
    connection, _ = _connection(
        {"payload_json": {"observation_checkpoint": None}},
    )
    with patch("tgw.queue.state_machine._conn", return_value=connection), \
         pytest.raises(ValueError, match="conflicts"):
        state_machine.checkpoint_running_job(
            "job-1", "owner-1", "11111111-1111-1111-1111-111111111111",
            {"value": 1},
        )


def test_checkpoint_running_job_rejects_lost_lease():
    connection, _ = _connection(None)
    with patch("tgw.queue.state_machine._conn", return_value=connection), \
         pytest.raises(RuntimeError, match="lost running lease"):
        state_machine.checkpoint_running_job(
            "job-1", "owner-1", "22222222-2222-2222-2222-222222222222",
            {"value": 1},
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "bad"}])
def test_checkpoint_running_job_rejects_non_json_or_nonfinite(value):
    with pytest.raises((TypeError, ValueError)):
        state_machine.checkpoint_running_job(
            "job-1", "owner-1", "11111111-1111-1111-1111-111111111111",
            {"value": value},
        )


def test_checkpoint_running_job_rejects_oversized_json():
    with pytest.raises(ValueError, match="maximum encoded size"):
        state_machine.checkpoint_running_job(
            "job-1", "owner-1", "11111111-1111-1111-1111-111111111111",
            {"value": "x" * (257 * 1024)},
        )

import hashlib

import pytest

from tgw.fleet_activation import FleetActivationError, apply_fleet_configuration, rollback_fleet_configuration


def _hash(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def test_configuration_and_materialization_are_receipt_backed_and_rollbackable(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"old-one")
    second.write_bytes(b"old-two")
    applied, rolled_back = [], []
    receipt = apply_fleet_configuration(
        {first: {"expected_sha256": _hash(b"old-one"), "desired": b"new-one"}, second: {"expected_sha256": _hash(b"old-two"), "desired": b"new-two"}},
        materialize=lambda: applied.append(True) or {"schema": "materialization", "rollback_journal": []},
        rollback_materialization=lambda value: rolled_back.append(value),
    )
    assert receipt["status"] == "CONFIGURED_MATERIALIZED_NOT_SERVICE_ACTIVATED"
    assert first.read_bytes() == b"new-one" and second.read_bytes() == b"new-two"
    rollback_fleet_configuration(receipt, rollback_materialization=lambda value: rolled_back.append(value))
    assert first.read_bytes() == b"old-one" and second.read_bytes() == b"old-two"
    assert applied and rolled_back


def test_changed_preimage_fails_closed_without_writes(tmp_path):
    config = tmp_path / "config"
    config.write_bytes(b"actual")
    with pytest.raises(FleetActivationError, match="preimage changed"):
        apply_fleet_configuration({config: {"expected_sha256": _hash(b"forged"), "desired": b"new"}}, materialize=lambda: {}, rollback_materialization=lambda _: None)
    assert config.read_bytes() == b"actual"


def test_materialization_failure_restores_configurations(tmp_path):
    config = tmp_path / "config"
    config.write_bytes(b"old")
    with pytest.raises(RuntimeError, match="stop"):
        apply_fleet_configuration({config: {"expected_sha256": _hash(b"old"), "desired": b"new"}}, materialize=lambda: (_ for _ in ()).throw(RuntimeError("stop")), rollback_materialization=lambda _: None)
    assert config.read_bytes() == b"old"

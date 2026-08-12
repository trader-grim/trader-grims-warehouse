from unittest.mock import Mock

import pytest

from tgw.deployment_runtime import DeploymentMounts, compose_deployment_controller
from tgw.effect_handlers import AuthorityEffectController


def _compose(tmp_path, **changes):
    root = tmp_path / "releases"
    root.mkdir(exist_ok=True)
    artifact = tmp_path / "candidate.tar.gz"
    artifact.write_bytes(b"archive")
    values = {
        "mounts": DeploymentMounts("tgw-prod", "production-releases", root, "candidate:1", artifact),
        "expected_host": "tgw-prod",
        "consume_authority": Mock(),
        "backup": Mock(),
        "health": Mock(),
        "require_authority_schema": Mock(),
        "flake_push": Mock(),
        "flake_switch_record": Mock(),
        "dependency_resubmit": Mock(),
    }
    values.update(changes)
    return compose_deployment_controller(**values), values


def test_composition_binds_symbolic_mounts_and_checks_schema(tmp_path):
    controller, values = _compose(tmp_path)
    assert isinstance(controller, AuthorityEffectController)
    values["require_authority_schema"].assert_called_once_with()


def test_wrong_host_fails_before_schema_or_provider_access(tmp_path):
    check = Mock()
    with pytest.raises(ValueError, match="registered production host"):
        _compose(tmp_path, expected_host="other", require_authority_schema=check)
    check.assert_not_called()


def test_missing_artifact_or_schema_fails_composition(tmp_path):
    missing = DeploymentMounts("tgw-prod", "production-releases", tmp_path, "candidate:1", tmp_path / "missing")
    with pytest.raises(ValueError, match="artifact is unavailable"):
        _compose(tmp_path, mounts=missing)
    with pytest.raises(RuntimeError, match="schema absent"):
        _compose(tmp_path, require_authority_schema=Mock(side_effect=RuntimeError("schema absent")))

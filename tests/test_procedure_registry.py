from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tgw.procedure_registry import (
    ProcedureRegistryError,
    load_procedure_registry,
    procedures_revision,
    resolve_procedure,
    validate_procedure_registry,
)

REGISTRY = Path(__file__).parents[1] / "config/environment/procedures.json"


def _registry():
    return load_procedure_registry(REGISTRY)


def test_repository_procedure_registry_is_revision_bound_and_non_executing():
    registry = _registry()
    assert registry["revision"] == procedures_revision(registry["procedures"])
    switch = resolve_procedure(registry, "nixos-prod-switch/v1")
    assert switch["host_role"] == "production"
    assert switch["direct_invocation_allowed"] is False
    assert switch["authority_gate"] == "explicit-deployment-approval"
    assert switch["rollback_procedure"] == "nixos-prod-rollback/v1"
    install = registry["procedures"]["app-release-install/v1"]
    assert install["status"] == "active"
    assert install["argv"][0] == "/opt/TGW/installer/current/bin/tgw-release-install"
    assert install["argv"][2] == "/opt/TGW"
    assert install["rollback_procedure"] == "app-release-rollback/v1"
    assert resolve_procedure(registry, "app-release-install/v1")["status"] == "active"
    for flag, value in (
        ("--admission-public-key", ":admission_public_key"),
        ("--environment-public-key", ":environment_public_key"),
        ("--current-plan-commit", ":current_plan_commit"),
        ("--current-solution-hash", ":current_solution_hash"),
    ):
        index = install["argv"].index(flag)
        assert install["argv"][index + 1] == value


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("revision",), "sha256:" + "0" * 64, "revision"),
        (("procedures", "nixos-prod-switch/v1", "direct_invocation_allowed"), True, "forbid"),
        (("procedures", "app-release-install/v1", "status"), "held", "status and execution policy"),
        (("procedures", "nixos-prod-switch/v1", "authority_gate"), "plan-text", "approval"),
        (("procedures", "nixos-prod-switch/v1", "argv"), ["sh", "-c", "unsafe"], "fixed argv"),
        (("procedures", "nixos-prod-switch/v1", "rollback_procedure"), "missing/v1", "unknown rollback"),
    ],
)
def test_registry_fails_closed_on_authority_or_execution_drift(path, value, match):
    registry = deepcopy(_registry())
    target = registry
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    if path != ("revision",):
        registry["revision"] = procedures_revision(registry["procedures"])
    with pytest.raises(ProcedureRegistryError, match=match):
        validate_procedure_registry(registry)


def test_unknown_procedure_identity_fails_closed():
    with pytest.raises(ProcedureRegistryError, match="unknown procedure"):
        resolve_procedure(_registry(), "nixos-prod-improvise/v1")

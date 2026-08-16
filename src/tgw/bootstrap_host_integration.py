"""Fail-closed host mounting for pinned bootstrap deployment contracts.

This module is deliberately an adapter boundary, not a deployment
implementation.  A host supplies immutable S/D/X pins and a typed provider;
the provider receives only a verified contract reference/hash and must report
the observed closure transitions, health proof receipts, and rollback receipt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from tgw.bootstrap_deployment_contract import (
    BootstrapDeploymentContractError,
    PinnedBootstrapDeploymentContractResolver,
    VerifiedBootstrapDeploymentContract,
)
from tgw.candidate_receipt_sink import (
    PinnedGitReceiptSink,
    load_pinned_candidate_evidence_descriptor,
    load_receipt_sink_descriptor,
)

PINNED_BOOTSTRAP_HOST_INTEGRATION_SCHEMA = "tgw-pinned-bootstrap-host-integration/v1"
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}$")


class BootstrapHostIntegrationError(ValueError):
    """A host integration cannot safely mount or prove a bootstrap action."""


class TypedBootstrapDeploymentProvider(Protocol):
    """A host-specific adapter with no ambient target or path input."""

    def observe(self, binding: Mapping[str, str]) -> Mapping[str, Any]: ...
    def install(self, binding: Mapping[str, str]) -> Mapping[str, Any]: ...
    def rollback(self, binding: Mapping[str, str]) -> Mapping[str, Any]: ...


def _absolute_path(value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise BootstrapHostIntegrationError(f"{label} is invalid")
    path = Path(value)
    if not path.is_absolute():
        raise BootstrapHostIntegrationError(f"{label} must be absolute")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise BootstrapHostIntegrationError(f"{label} is unavailable") from exc


def _integration_pins(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "candidate_repository",
        "plan_repository",
        "plan_approved_ref",
        "candidate_evidence_descriptor_config",
        "execution_evidence_sink_config",
        "bootstrap_contract_sink_config",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise BootstrapHostIntegrationError("pinned bootstrap host integration configuration is invalid")
    if value.get("schema") != PINNED_BOOTSTRAP_HOST_INTEGRATION_SCHEMA:
        raise BootstrapHostIntegrationError("pinned bootstrap host integration schema is invalid")
    approved_ref = value.get("plan_approved_ref")
    if not isinstance(approved_ref, str) or not approved_ref:
        raise BootstrapHostIntegrationError("pinned bootstrap approved Plan reference is invalid")
    return {
        "candidate_repository": _absolute_path(value.get("candidate_repository"), label="bootstrap candidate repository"),
        "plan_repository": _absolute_path(value.get("plan_repository"), label="bootstrap Plan repository"),
        "plan_approved_ref": approved_ref,
        "candidate_evidence_descriptor_config": _absolute_path(
            value.get("candidate_evidence_descriptor_config"), label="bootstrap candidate evidence descriptor config",
        ),
        "execution_evidence_sink_config": _absolute_path(
            value.get("execution_evidence_sink_config"), label="bootstrap execution evidence sink config",
        ),
        "bootstrap_contract_sink_config": _absolute_path(
            value.get("bootstrap_contract_sink_config"), label="bootstrap contract sink config",
        ),
    }


def _binding(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"bootstrap_contract_ref", "bootstrap_contract_hash"}:
        raise BootstrapHostIntegrationError("bootstrap provider binding is invalid")
    ref, contract_hash = value.get("bootstrap_contract_ref"), value.get("bootstrap_contract_hash")
    if not isinstance(ref, str) or not isinstance(contract_hash, str):
        raise BootstrapHostIntegrationError("bootstrap provider binding is invalid")
    return {"bootstrap_contract_ref": ref, "bootstrap_contract_hash": contract_hash}


def _observation(value: Any, *, label: str, generation: str, closure: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"generation", "closure"}:
        raise BootstrapHostIntegrationError(f"{label} observation is invalid")
    if value.get("generation") != generation or value.get("closure") != closure:
        raise BootstrapHostIntegrationError(f"{label} observation does not match pinned bootstrap contract")
    return {"generation": generation, "closure": closure}


def _evidence(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BootstrapHostIntegrationError(f"{label} evidence is invalid")
    if not all(isinstance(item, str) and _IDENTITY.fullmatch(item) for item in value):
        raise BootstrapHostIntegrationError(f"{label} evidence is invalid")
    return sorted(set(value))


def _health(value: Any, *, required_probes: tuple[str, ...]) -> list[str]:
    if not isinstance(value, Mapping) or set(value) != {"probes"} or not isinstance(value.get("probes"), list):
        raise BootstrapHostIntegrationError("bootstrap health result is invalid")
    receipts: list[str] = []
    probes: list[str] = []
    for item in value["probes"]:
        if not isinstance(item, Mapping) or set(item) != {"probe", "receipt"}:
            raise BootstrapHostIntegrationError("bootstrap health result is invalid")
        probe, receipt = item.get("probe"), item.get("receipt")
        if not isinstance(probe, str) or not isinstance(receipt, str) or not _IDENTITY.fullmatch(receipt):
            raise BootstrapHostIntegrationError("bootstrap health result is invalid")
        probes.append(probe)
        receipts.append(receipt)
    if probes != list(required_probes) or len(set(receipts)) != len(receipts):
        raise BootstrapHostIntegrationError("bootstrap health probes do not match pinned contract")
    return receipts


@dataclass(frozen=True)
class MountedBootstrapHostIntegration:
    """The resolver and closed install/rollback callbacks given to W09."""

    resolver: PinnedBootstrapDeploymentContractResolver
    provider: TypedBootstrapDeploymentProvider

    def _verified(self, binding: Mapping[str, str]) -> tuple[dict[str, str], VerifiedBootstrapDeploymentContract]:
        normalized = _binding(binding)
        try:
            verified = self.resolver.resolve(
                normalized["bootstrap_contract_ref"], normalized["bootstrap_contract_hash"],
            )
        except BootstrapDeploymentContractError as exc:
            raise BootstrapHostIntegrationError("bootstrap provider contract verification failed") from exc
        return normalized, verified

    def install(self, binding: Mapping[str, str]) -> Mapping[str, Any]:
        normalized, verified = self._verified(binding)
        _observation(
            self.provider.observe(dict(normalized)),
            label="bootstrap prior",
            generation=verified.expected_prior_generation,
            closure=verified.expected_prior_closure,
        )
        result = self.provider.install(dict(normalized))
        if not isinstance(result, Mapping) or set(result) != {"observed", "health", "evidence"}:
            raise BootstrapHostIntegrationError("bootstrap install result is invalid")
        _observation(
            result["observed"],
            label="bootstrap successor",
            generation=verified.intended_next_generation,
            closure=verified.intended_next_closure,
        )
        # A provider's completion payload is not itself sufficient evidence
        # that the target changed.  Re-observe through the typed host adapter
        # before admitting health evidence to the authority receipt.
        _observation(
            self.provider.observe(dict(normalized)),
            label="bootstrap verified successor",
            generation=verified.intended_next_generation,
            closure=verified.intended_next_closure,
        )
        health_receipts = _health(result["health"], required_probes=verified.required_health_probes)
        return {"evidence": sorted(set(_evidence(result["evidence"], label="bootstrap install") + health_receipts))}

    def rollback(self, binding: Mapping[str, str]) -> Mapping[str, Any]:
        normalized, verified = self._verified(binding)
        _observation(
            self.provider.observe(dict(normalized)),
            label="bootstrap pre-rollback",
            generation=verified.intended_next_generation,
            closure=verified.intended_next_closure,
        )
        result = self.provider.rollback(dict(normalized))
        if not isinstance(result, Mapping) or set(result) != {"observed", "rollback_receipt", "evidence"}:
            raise BootstrapHostIntegrationError("bootstrap rollback result is invalid")
        _observation(
            result["observed"],
            label="bootstrap restored prior",
            generation=verified.expected_prior_generation,
            closure=verified.expected_prior_closure,
        )
        _observation(
            self.provider.observe(dict(normalized)),
            label="bootstrap verified restored prior",
            generation=verified.expected_prior_generation,
            closure=verified.expected_prior_closure,
        )
        receipt = result.get("rollback_receipt")
        if not isinstance(receipt, str) or not _IDENTITY.fullmatch(receipt):
            raise BootstrapHostIntegrationError("bootstrap rollback receipt is invalid")
        return {"receipt": receipt, "evidence": _evidence(result["evidence"], label="bootstrap rollback")}


def mount_pinned_bootstrap_host_integration(
    config: Mapping[str, Any],
    *,
    provider: TypedBootstrapDeploymentProvider | None,
) -> MountedBootstrapHostIntegration:
    """Mount bootstrap only with externally configured pins and a typed adapter."""
    if provider is None or not all(callable(getattr(provider, name, None)) for name in ("observe", "install", "rollback")):
        raise BootstrapHostIntegrationError("typed bootstrap deployment provider is not mounted")
    pins = _integration_pins(config)
    try:
        descriptor = load_pinned_candidate_evidence_descriptor(
            pins["candidate_evidence_descriptor_config"],
            candidate_repository=pins["candidate_repository"],
        )
        execution_sink = PinnedGitReceiptSink(
            load_receipt_sink_descriptor(
                pins["execution_evidence_sink_config"], candidate_repository=pins["candidate_repository"],
            ),
            candidate_repository=pins["candidate_repository"],
        )
        contract_sink = PinnedGitReceiptSink(
            load_receipt_sink_descriptor(
                pins["bootstrap_contract_sink_config"], candidate_repository=pins["candidate_repository"],
            ),
            candidate_repository=pins["candidate_repository"],
        )
        resolver = PinnedBootstrapDeploymentContractResolver(
            pins["candidate_repository"],
            plan_repository=pins["plan_repository"],
            plan_approved_ref=pins["plan_approved_ref"],
            candidate_evidence_descriptor=descriptor,
            execution_evidence_sink=execution_sink,
            bootstrap_contract_sink=contract_sink,
        )
    except (BootstrapDeploymentContractError, OSError, ValueError) as exc:
        raise BootstrapHostIntegrationError("pinned bootstrap host integration is unavailable") from exc
    return MountedBootstrapHostIntegration(resolver=resolver, provider=provider)

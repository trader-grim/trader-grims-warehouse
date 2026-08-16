"""Fail-closed host mounting for pinned bootstrap deployment contracts.

This module is deliberately an adapter boundary, not a deployment
implementation.  A host supplies immutable S/D/X pins and a typed provider;
the provider receives only a verified contract reference/hash and must report
the observed closure transitions, health proof receipts, and rollback receipt.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

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
BOOTSTRAP_PROVIDER_BINDING_SCHEMA = "tgw-bootstrap-provider-binding/v1"
BOOTSTRAP_PROVIDER_RESPONSE_SCHEMA = "tgw-bootstrap-provider-response/v1"
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}$")
_CREDENTIAL_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class BootstrapHostIntegrationError(ValueError):
    """A host integration cannot safely mount or prove a bootstrap action."""


class TypedBootstrapDeploymentProvider(Protocol):
    """A host-specific adapter with no ambient target or path input."""

    def observe(self, binding: Mapping[str, str]) -> Mapping[str, Any]: ...
    def install(self, binding: Mapping[str, str]) -> Mapping[str, Any]: ...
    def rollback(self, binding: Mapping[str, str]) -> Mapping[str, Any]: ...


class _NoRedirect(HTTPRedirectHandler):
    """A deployment provider response must come from its configured endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(frozen=True)
class _ConfiguredBootstrapHttpProvider:
    """The one host-owned remote bootstrap provider client.

    The remote service, not tgw-http, owns the privileged target procedure.
    This client can send only a verified contract reference and hash to three
    fixed endpoints; it has no command, target, generation, or path surface.
    """

    endpoint: str
    credential_env: str
    provider_id: str
    provider_identity: str
    trust_anchor_sha256: str
    timeout_seconds: int

    def _call(self, operation: str, binding: Mapping[str, str]) -> Mapping[str, Any]:
        normalized = _binding(binding)
        secret = os.environ.get(self.credential_env)
        if not secret:
            raise BootstrapHostIntegrationError("bootstrap provider credential is unavailable")
        request = Request(
            f"{self.endpoint}/v1/bootstrap/{operation}",
            data=json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout_seconds) as response:  # nosec: fixed host provider binding
                if response.status != 200:
                    raise BootstrapHostIntegrationError("bootstrap provider returned a non-success status")
                raw = response.read(1024 * 1024 + 1)
        except (HTTPError, URLError, OSError) as exc:
            raise BootstrapHostIntegrationError("bootstrap provider is unavailable") from exc
        if len(raw) > 1024 * 1024:
            raise BootstrapHostIntegrationError("bootstrap provider response is too large")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise BootstrapHostIntegrationError("bootstrap provider response is invalid") from exc
        if not isinstance(payload, Mapping) or set(payload) != {
            "schema", "provider_id", "provider_identity", "trust_anchor_sha256", "result",
        }:
            raise BootstrapHostIntegrationError("bootstrap provider response is invalid")
        if (
            payload.get("schema") != BOOTSTRAP_PROVIDER_RESPONSE_SCHEMA
            or payload.get("provider_id") != self.provider_id
            or payload.get("provider_identity") != self.provider_identity
            or payload.get("trust_anchor_sha256") != self.trust_anchor_sha256
            or not isinstance(payload.get("result"), Mapping)
        ):
            raise BootstrapHostIntegrationError("bootstrap provider identity binding is invalid")
        return dict(payload["result"])

    def observe(self, binding: Mapping[str, str]) -> Mapping[str, Any]:
        return self._call("observe", binding)

    def install(self, binding: Mapping[str, str]) -> Mapping[str, Any]:
        return self._call("install", binding)

    def rollback(self, binding: Mapping[str, str]) -> Mapping[str, Any]:
        return self._call("rollback", binding)


def configured_bootstrap_deployment_provider(config: Mapping[str, Any]) -> TypedBootstrapDeploymentProvider | None:
    """Resolve the one allowlisted, host-owned bootstrap provider binding.

    A pin set without this independent binding remains deliberately unmounted.
    There is no Python import, shell command, target, or procedure selector in
    the configuration contract.
    """
    value = config.get("bootstrap_provider_binding")
    if value is None:
        return None
    required = {
        "schema", "provider_id", "endpoint", "credential_env", "timeout_seconds",
        "provider_identity", "trust_anchor_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise BootstrapHostIntegrationError("bootstrap provider binding is invalid")
    if value.get("schema") != BOOTSTRAP_PROVIDER_BINDING_SCHEMA:
        raise BootstrapHostIntegrationError("bootstrap provider binding schema is invalid")
    # This map is intentionally closed.  Adding a provider is a source review,
    # not an operator-configurable import or command choice.
    if value.get("provider_id") != "tgw-bootstrap-deployment-provider@1":
        raise BootstrapHostIntegrationError("bootstrap provider identity is not allowlisted")
    endpoint = value.get("endpoint")
    parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
    local_http = parsed and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if not parsed or (parsed.scheme != "https" and not local_http) or not parsed.netloc or parsed.query or parsed.fragment:
        raise BootstrapHostIntegrationError("bootstrap provider endpoint is invalid")
    credential_env = value.get("credential_env")
    provider_identity = value.get("provider_identity")
    trust_anchor = value.get("trust_anchor_sha256")
    timeout = value.get("timeout_seconds")
    if (
        not isinstance(credential_env, str) or _CREDENTIAL_ENV.fullmatch(credential_env) is None
        or not isinstance(provider_identity, str) or _IDENTITY.fullmatch(provider_identity) is None
        or not isinstance(trust_anchor, str) or _SHA256.fullmatch(trust_anchor) is None
        or isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 60
    ):
        raise BootstrapHostIntegrationError("bootstrap provider binding is invalid")
    return _ConfiguredBootstrapHttpProvider(
        endpoint=endpoint.rstrip("/"), credential_env=credential_env,
        provider_id="tgw-bootstrap-deployment-provider@1", provider_identity=provider_identity,
        trust_anchor_sha256=trust_anchor, timeout_seconds=timeout,
    )


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

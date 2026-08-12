"""Typed effect registry and one-shot authority/controller boundary.

Handlers receive validated objects, never commands.  Concrete release, flake,
and queue providers are injected by identity so this registry cannot become a
generic shell or ambient host/path selector.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from tgw.plan_authority import EffectKind, TypedEffect

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}$")
_NIX_STORE_PATH = re.compile(r"^/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+$")

_REVIEW_EVAL_UNITS = (
    "tgw-review-egress@.service",
    "tgw-review-egress-attest@.service",
    "tgw-review-egress-namespace@.service",
)


class EffectHandlerError(RuntimeError):
    pass


class RetryableEffect(EffectHandlerError):
    pass


class AmbiguousEffect(EffectHandlerError):
    pass


class EffectOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    RETRY = "retry"
    AMBIGUOUS = "ambiguous"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _required(parameters: Mapping[str, Any], keys: set[str]) -> dict[str, str]:
    if set(parameters) != keys:
        raise ValueError(f"effect parameters must be exactly {sorted(keys)}")
    result = dict(parameters)
    if any(not isinstance(value, str) or not value or not _IDENTITY.fullmatch(value) for value in result.values()):
        raise ValueError("effect parameter identity is invalid")
    return result


def _required_strings(parameters: Mapping[str, Any], keys: set[str]) -> dict[str, str]:
    if set(parameters) != keys:
        raise ValueError(f"effect parameters must be exactly {sorted(keys)}")
    result = dict(parameters)
    if any(not isinstance(value, str) or not value for value in result.values()):
        raise ValueError("effect parameters must be non-empty strings")
    return result


@dataclass(frozen=True)
class EffectExecutionReceipt:
    schema: str
    request_id: str
    authority_receipt_id: str
    effect_hash: str
    effect_kind: str
    generation: str
    handler_id: str
    outcome: EffectOutcome
    evidence: tuple[str, ...]
    rollback_receipt: str | None = None
    detail: str = ""

    @property
    def receipt_hash(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical(self.__dict__)).hexdigest()


def _authority_canary(parameters: Mapping[str, str]) -> Mapping[str, Any]:
    """Return deterministic evidence only; perform no provider or filesystem I/O."""
    evidence = {
        "schema": "tgw-authority-canary-evidence/v1",
        "canary_id": parameters["canary_id"],
        "generation": parameters["generation"],
        "purpose": parameters["purpose"],
    }
    return {"evidence": ["authority-canary:sha256:" + hashlib.sha256(_canonical(evidence)).hexdigest()]}


class TypedEffectHandlerRegistry:
    """Closed registry of Plan-declared typed effect classes."""

    def __init__(
        self,
        *,
        release_install: Callable[[Mapping[str, str]], Mapping[str, Any]],
        release_rollback: Callable[[Mapping[str, str]], Mapping[str, Any]],
        flake_push: Callable[[Mapping[str, str]], Mapping[str, Any]],
        flake_switch_record: Callable[[Mapping[str, str]], Mapping[str, Any]],
        dependency_resubmit: Callable[[Mapping[str, str]], Mapping[str, Any]],
        bootstrap_install: Callable[[Mapping[str, str]], Mapping[str, Any]] | None = None,
        bootstrap_rollback: Callable[[Mapping[str, str]], Mapping[str, Any]] | None = None,
        nixos_reviewed_evaluation: Callable[[Mapping[str, str]], Mapping[str, Any]] | None = None,
    ) -> None:
        self._providers = {
            EffectKind.CODING_RELEASE: ("immutable-release-installer@1", release_install, release_rollback),
            EffectKind.BOUNDED_FLAKE_PUSH: ("bounded-flake-push@1", flake_push, None),
            EffectKind.FLAKE_SWITCH_RECORD_ONLY: ("flake-switch-record-only@1", flake_switch_record, None),
            EffectKind.DEPENDENCY_RESUBMIT: ("dependency-resubmit@1", dependency_resubmit, None),
            EffectKind.AUTHORITY_CANARY: ("authority-canary-receipt-only@1", _authority_canary, None),
            EffectKind.APPROVAL_PLATFORM_BOOTSTRAP_DEPLOYMENT: (
                "nixos-reviewed-generation-switch@1",
                bootstrap_install or self._unavailable_bootstrap,
                bootstrap_rollback or self._unavailable_bootstrap,
            ),
            EffectKind.NIXOS_REVIEWED_EVALUATION: (
                "nixos-reviewed-evaluation@1",
                self._reviewed_evaluation_provider(nixos_reviewed_evaluation or self._unavailable_evaluation),
                None,
            ),
        }

    @staticmethod
    def _unavailable_bootstrap(parameters: Mapping[str, str]) -> Mapping[str, Any]:
        raise EffectHandlerError("bootstrap deployment provider is not mounted")

    @staticmethod
    def _unavailable_evaluation(parameters: Mapping[str, str]) -> Mapping[str, Any]:
        raise EffectHandlerError("reviewed Nix evaluation provider is not mounted")

    @staticmethod
    def _reviewed_evaluation_provider(provider: Callable[[Mapping[str, str]], Mapping[str, Any]]) -> Callable[[Mapping[str, str]], Mapping[str, Any]]:
        """Validate immutable provider output before it becomes authority evidence."""
        def invoke(parameters: Mapping[str, str]) -> Mapping[str, Any]:
            result = provider(parameters)
            exact = {
                "schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
                "outcome": "verified",
                "source_commit": parameters["source_commit"],
                "source_tree": parameters["source_tree"],
                "source_archive_sha256": parameters["source_archive_sha256"],
                "flake_lock_sha256": parameters["flake_lock_sha256"],
                "module_sha256": parameters["module_sha256"],
                "provider_sha256": parameters["provider_sha256"],
                "scratch_id": parameters["scratch_id"],
                "cleanup": "removed",
                "activate": False,
                "profile_write": False,
                "home_db_write": False,
                "system": "x86_64-linux",
                "evaluation_target": "review-egress-systemd-units",
            }
            if not isinstance(result, Mapping) or any(result.get(key) != value for key, value in exact.items()):
                raise EffectHandlerError("reviewed Nix evaluation receipt identity or safety invariant mismatch")
            expected_executables = {
                "git": "/run/current-system/sw/bin/git",
                "nix": "/run/current-system/sw/bin/nix",
                "systemd_analyze": "/run/current-system/sw/bin/systemd-analyze",
            }
            if result.get("executables") != expected_executables:
                raise EffectHandlerError("reviewed Nix evaluation executable provenance mismatch")
            digest_keys = (
                "evaluated_closure_sha256", "eval_log_sha256", "build_log_sha256",
                "systemd_verify_output_sha256", "receipt_sha256",
            )
            if any(not isinstance(result.get(key), str) or not _SHA256.fullmatch(result[key]) for key in digest_keys):
                raise EffectHandlerError("reviewed Nix evaluation receipt digest is invalid")
            if not isinstance(result.get("evaluated_config_drv"), str) or not _NIX_STORE_PATH.fullmatch(result["evaluated_config_drv"]):
                raise EffectHandlerError("reviewed Nix evaluation derivation identity is invalid")
            if result.get("systemd_verify_exit") != 0:
                raise EffectHandlerError("generated systemd units did not verify")
            if not isinstance(result.get("nix_version"), str) or not result["nix_version"]:
                raise EffectHandlerError("Nix version evidence is absent")
            try:
                systemd_version = int(result.get("systemd_version"))
            except (TypeError, ValueError) as exc:
                raise EffectHandlerError("systemd version evidence is invalid") from exc
            if systemd_version < int(parameters["minimum_systemd_version"]):
                raise EffectHandlerError("systemd verifier is older than the admitted minimum")
            units = result.get("unit_sha256")
            if not isinstance(units, Mapping) or set(units) != set(_REVIEW_EVAL_UNITS) or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in units.values()):
                raise EffectHandlerError("reviewed Nix evaluation unit hashes are incomplete")
            evidence = result.get("evidence")
            if not isinstance(evidence, (list, tuple)) or not evidence or any(not isinstance(item, str) or not _IDENTITY.fullmatch(item) for item in evidence):
                raise EffectHandlerError("reviewed Nix evaluation immutable evidence is absent")
            receipt_payload = {key: value for key, value in result.items() if key not in {"receipt_sha256", "evidence"}}
            receipt_hash = "sha256:" + hashlib.sha256(_canonical(receipt_payload)).hexdigest()
            if result["receipt_sha256"] != receipt_hash or tuple(evidence) != ("nixos-evaluation:" + receipt_hash,):
                raise EffectHandlerError("reviewed Nix evaluation receipt self-hash mismatch")
            return result
        return invoke

    def prepare(self, effect: TypedEffect) -> tuple[str, dict[str, str], Callable[..., Mapping[str, Any]], Callable[..., Mapping[str, Any]] | None]:
        if effect.kind is EffectKind.CODING_RELEASE:
            parameters = _required(
                effect.parameters,
                {
                    "candidate_commit",
                    "candidate_tree",
                    "archive_sha256",
                    "artifact_ref",
                    "root_id",
                    "expected_current",
                    "operation_id",
                    "review_receipt",
                    "controller_receipt",
                },
            )
            if not _SHA1.fullmatch(parameters["candidate_commit"]) or not _SHA1.fullmatch(parameters["candidate_tree"]) or not _SHA256.fullmatch(parameters["archive_sha256"]):
                raise ValueError("coding release hashes are invalid")
        elif effect.kind is EffectKind.BOUNDED_FLAKE_PUSH:
            parameters = _required(effect.parameters, {"repository_id", "host_role", "commit", "remote_ref"})
            if (
                parameters["repository_id"] != "tgw-flake"
                or parameters["host_role"] not in {"production", "controller"}
                or parameters["remote_ref"] != "origin/master"
                or not _SHA1.fullmatch(parameters["commit"])
            ):
                raise ValueError("flake push target is outside the registered bound")
        elif effect.kind is EffectKind.FLAKE_SWITCH_RECORD_ONLY:
            parameters = _required(effect.parameters, {"host_role", "commit", "execution_receipt"})
            if parameters["host_role"] != "production" or not _SHA1.fullmatch(parameters["commit"]):
                raise ValueError("flake switch record target is outside the registered bound")
        elif effect.kind is EffectKind.DEPENDENCY_RESUBMIT:
            parameters = _required(effect.parameters, {"dependency_id", "queue_id", "failed_generation"})
            if parameters["queue_id"] not in {"coding", "review", "controller", "release"}:
                raise ValueError("dependency queue is not registered")
        elif effect.kind is EffectKind.AUTHORITY_CANARY:
            parameters = _required(effect.parameters, {"canary_id", "purpose"})
            if parameters["purpose"] != "verify-plan-authority-roundtrip":
                raise ValueError("authority canary purpose is outside the harmless registered bound")
        elif effect.kind is EffectKind.APPROVAL_PLATFORM_BOOTSTRAP_DEPLOYMENT:
            parameters = _required_strings(effect.parameters, {
                "target_host", "flake_repository_id", "flake_commit", "flake_tree",
                "expected_current_system", "successor_system", "credential_ref", "credential_sha256",
                "broker_source_sha256", "namespace_source_sha256", "nix_module_sha256",
                "egress_contract_sha256", "install_contract_sha256", "review_receipt",
                "controller_receipt", "network_attestation_receipt", "probe_receipt", "operation_id",
            })
            if parameters["target_host"] != "tgw-prod" or parameters["flake_repository_id"] != "tgw-flake":
                raise ValueError("bootstrap deployment target is outside the registered production bound")
            if not _SHA1.fullmatch(parameters["flake_commit"]) or not _SHA1.fullmatch(parameters["flake_tree"]):
                raise ValueError("bootstrap reviewed flake identity is invalid")
            digest_fields = {key for key in parameters if key.endswith("_sha256")}
            if any(not _SHA256.fullmatch(parameters[key]) for key in digest_fields):
                raise ValueError("bootstrap artifact or credential digest is invalid")
            for key in ("expected_current_system", "successor_system"):
                if not parameters[key].startswith("/nix/store/") or "nixos-system-tgw-prod-" not in parameters[key]:
                    raise ValueError("bootstrap system closure is not an exact tgw-prod Nix store identity")
            if parameters["expected_current_system"] == parameters["successor_system"]:
                raise ValueError("bootstrap successor must differ from expected current generation")
            if not parameters["credential_ref"].startswith("credential:tgw-review:"):
                raise ValueError("bootstrap credential must use the dedicated symbolic review identity")
            identity_fields = {
                "target_host", "flake_repository_id", "credential_ref", "review_receipt",
                "controller_receipt", "network_attestation_receipt", "probe_receipt", "operation_id",
            }
            if any(not _IDENTITY.fullmatch(parameters[key]) for key in identity_fields):
                raise ValueError("bootstrap symbolic identity is invalid")
        else:
            parameters = _required_strings(effect.parameters, {
                "target_host", "flake_repository_id", "artifact_ref", "source_commit",
                "source_tree", "source_archive_sha256", "flake_lock_sha256", "module_path",
                "module_sha256", "provider_sha256", "scratch_id", "system", "evaluation_target", "unit_set",
                "output_schema", "nix_network_policy", "minimum_systemd_version",
                "max_duration_seconds", "max_output_bytes", "activate", "profile_write",
                "home_db_write", "operation_id",
            })
            fixed = {
                "target_host": "tgw-prod", "flake_repository_id": "tgw-flake",
                "module_path": "nix/review-egress.nix", "system": "x86_64-linux",
                "evaluation_target": "review-egress-systemd-units",
                "unit_set": ",".join(_REVIEW_EVAL_UNITS),
                "output_schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
                "nix_network_policy": "offline-no-substituters",
                "activate": "false", "profile_write": "false", "home_db_write": "false",
            }
            if any(parameters[key] != value for key, value in fixed.items()):
                raise ValueError("reviewed Nix evaluation target or safety invariant is outside the registered bound")
            if not _SHA1.fullmatch(parameters["source_commit"]) or not _SHA1.fullmatch(parameters["source_tree"]):
                raise ValueError("reviewed Nix source identity is invalid")
            for key in ("source_archive_sha256", "flake_lock_sha256", "module_sha256", "provider_sha256"):
                if not _SHA256.fullmatch(parameters[key]):
                    raise ValueError("reviewed Nix source digest is invalid")
            if parameters["artifact_ref"] != f"artifact:sha256:{parameters['source_archive_sha256'].removeprefix('sha256:')}":
                raise ValueError("reviewed Nix artifact reference is not content-addressed")
            if not parameters["scratch_id"].startswith("nixos-review:") or not _IDENTITY.fullmatch(parameters["scratch_id"]):
                raise ValueError("reviewed Nix scratch identity is invalid")
            if not _IDENTITY.fullmatch(parameters["operation_id"]):
                raise ValueError("reviewed Nix operation identity is invalid")
            try:
                minimum_systemd = int(parameters["minimum_systemd_version"])
                max_duration = int(parameters["max_duration_seconds"])
                max_output = int(parameters["max_output_bytes"])
            except ValueError as exc:
                raise ValueError("reviewed Nix bounds must be decimal integers") from exc
            if minimum_systemd < 257 or not 1 <= max_duration <= 900 or not 1024 <= max_output <= 16 * 1024 * 1024:
                raise ValueError("reviewed Nix resource or verifier bound is outside the registered range")
        handler_id, handler, rollback = self._providers[effect.kind]
        parameters["generation"] = effect.generation
        return handler_id, parameters, handler, rollback


class AuthorityEffectController:
    """Atomically redeems authority before invoking one registered provider."""

    def __init__(self, registry: TypedEffectHandlerRegistry, consume_authority: Callable[..., Mapping[str, Any]]):
        self.registry = registry
        self.consume_authority = consume_authority

    def execute(self, *, request_id: str, effect: TypedEffect) -> EffectExecutionReceipt:
        handler_id, parameters, handler, rollback = self.registry.prepare(effect)
        authority = self.consume_authority(request_id, effect_hash=effect.effect_hash, generation=effect.generation)
        receipt_id = str(authority["receipt_id"])
        try:
            result = handler(parameters)
            evidence = tuple(sorted(str(item) for item in result.get("evidence", ())))
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.SUCCEEDED, evidence)
        except RetryableEffect as exc:
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.RETRY, (), detail=str(exc))
        except AmbiguousEffect as exc:
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.AMBIGUOUS, (), detail=str(exc))
        except Exception as exc:
            if rollback is not None:
                try:
                    rolled_back = rollback(parameters)
                    rollback_receipt = str(rolled_back["receipt"])
                    return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.ROLLED_BACK, (), rollback_receipt=rollback_receipt, detail=str(exc))
                except Exception as rollback_exc:
                    return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.FAILED, (), detail=f"effect={exc}; rollback={rollback_exc}")
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.FAILED, (), detail=str(exc))

    @staticmethod
    def _receipt(
        request_id: str, authority_receipt_id: str, effect: TypedEffect, handler_id: str, outcome: EffectOutcome, evidence: tuple[str, ...], *, rollback_receipt: str | None = None, detail: str = ""
    ) -> EffectExecutionReceipt:
        return EffectExecutionReceipt(
            "tgw-effect-execution-receipt/v1", request_id, authority_receipt_id, effect.effect_hash, effect.kind.value, effect.generation, handler_id, outcome, evidence, rollback_receipt, detail
        )

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

from tgw.bootstrap_authority import BootstrapConsumptionAmbiguous
from tgw.nix_observer_render_evaluation import validate_request as validate_render_request
from tgw.nixos_observer_render_evaluation import (
    EFFECT_KIND as OBSERVER_RENDER_EFFECT_KIND,
)
from tgw.nixos_observer_render_evaluation import (
    CompositionHold,
    RemoteAttemptAmbiguous,
    RemoteRenderFailure,
    TerminalPersistenceError,
    validate_handler_success,
)
from tgw.nixos_reviewed_evaluation import _validate_remote_effect
from tgw.plan_authority import EffectKind, TypedEffect
from tgw.platform_bootstrap import BootstrapStateAmbiguous, validate_platform_bootstrap_effect

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
    def __init__(self, message: str, *, evidence: tuple[str, ...] = ()):
        super().__init__(message)
        self.evidence = evidence


class RetryableEffect(EffectHandlerError):
    pass


class AmbiguousEffect(EffectHandlerError):
    pass


class HeldEffect(EffectHandlerError):
    pass


class EffectOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    RETRY = "retry"
    HOLD = "hold"
    AMBIGUOUS = "ambiguous"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _observer_memory_evidence(
    *, reason: str, generation: str, request: Mapping[str, Any], composition_sha256: str, observed: Any
) -> tuple[str, ...]:
    observation = {
        "schema": "tgw-nixos-observer-render-handler-observation/v1",
        "reason": reason,
        "generation": generation,
        "request_sha256": request.get("request_sha256"),
        "composition_sha256": composition_sha256,
        "observed_sha256": "sha256:" + hashlib.sha256(_canonical(observed)).hexdigest(),
    }
    return ("nixos-observer-render-handler-memory:sha256:" + hashlib.sha256(_canonical(observation)).hexdigest(),)


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
        bootstrap_validate: Callable[[Mapping[str, Any]], None] | None = None,
        nixos_reviewed_evaluation: Callable[[Mapping[str, str]], Mapping[str, Any]] | None = None,
        nixos_observer_render_evaluation: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self._providers = {
            EffectKind.CODING_RELEASE: ("immutable-release-installer@1", release_install, release_rollback),
            EffectKind.BOUNDED_FLAKE_PUSH: ("bounded-flake-push@1", flake_push, None),
            EffectKind.FLAKE_SWITCH_RECORD_ONLY: ("flake-switch-record-only@1", flake_switch_record, None),
            EffectKind.DEPENDENCY_RESUBMIT: ("dependency-resubmit@1", dependency_resubmit, None),
            EffectKind.AUTHORITY_CANARY: ("authority-canary-receipt-only@1", _authority_canary, None),
            EffectKind.APPROVAL_PLATFORM_BOOTSTRAP_DEPLOYMENT: (
                "a3-platform-bootstrap-install@1",
                bootstrap_install or self._unavailable_bootstrap,
                bootstrap_rollback or self._unavailable_bootstrap,
            ),
            EffectKind.NIXOS_REVIEWED_EVALUATION: (
                "nixos-reviewed-evaluation@1",
                self._reviewed_evaluation_provider(nixos_reviewed_evaluation or self._unavailable_evaluation),
                None,
            ),
            EffectKind.NIXOS_OBSERVER_RENDER_EVALUATION: (
                "nixos-observer-render-evaluation@1",
                self._observer_render_provider(nixos_observer_render_evaluation or self._unavailable_render_evaluation),
                None,
            ),
        }
        self._bootstrap_validate = bootstrap_validate

    @staticmethod
    def _unavailable_bootstrap(parameters: Mapping[str, str]) -> Mapping[str, Any]:
        raise EffectHandlerError("bootstrap deployment provider is not mounted")

    @staticmethod
    def _unavailable_evaluation(parameters: Mapping[str, str]) -> Mapping[str, Any]:
        raise EffectHandlerError("reviewed Nix evaluation provider is not mounted")

    @staticmethod
    def _unavailable_render_evaluation(parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        raise EffectHandlerError("observer render evaluation provider is not mounted")

    @staticmethod
    def _observer_render_provider(provider: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
        """Accept only evidence rebound to the mounted immutable composition."""

        def invoke(parameters: Mapping[str, Any]) -> Mapping[str, Any]:
            generation = parameters["generation"]
            request = {key: value for key, value in parameters.items() if key != "generation"}
            composition = getattr(provider, "composition", None)
            if composition is None:
                raise HeldEffect("observer render provider has no immutable composition binding")
            try:
                result = provider({"kind": OBSERVER_RENDER_EFFECT_KIND, "generation": generation, "parameters": request})
            except CompositionHold as exc:
                raise HeldEffect(str(exc), evidence=("nixos-observer-render-composition:" + composition.receipt_sha256,)) from exc
            except (RemoteAttemptAmbiguous, TerminalPersistenceError) as exc:
                try:
                    evidence = exc.authority_evidence()
                except Exception:
                    evidence = _observer_memory_evidence(
                        reason="invalid-controller-ambiguity-evidence",
                        generation=generation,
                        request=request,
                        composition_sha256=composition.receipt_sha256,
                        observed={"type": type(exc).__name__, "detail": str(exc)},
                    )
                raise AmbiguousEffect(str(exc), evidence=evidence) from exc
            except RemoteRenderFailure as exc:
                if exc.terminal.get("outcome") == "AMBIGUOUS":
                    outcome = AmbiguousEffect
                else:
                    outcome = EffectHandlerError
                refs = (exc.attempt_ref, exc.transport_ref, exc.terminal_ref)
                evidence = tuple(
                    f"nixos-observer-render-{label}:{ref['sha256']}"
                    for label, ref in zip(("attempt", "transport", "terminal"), refs, strict=True)
                )
                raise outcome(str(exc), evidence=evidence) from exc
            try:
                validated = validate_handler_success(result, request=request, composition=composition)
            except Exception as exc:
                raise AmbiguousEffect(
                    "post-launch observer render evidence failed exact validation",
                    evidence=_observer_memory_evidence(
                        reason="post-launch-handler-validation-failed",
                        generation=generation,
                        request=request,
                        composition_sha256=composition.receipt_sha256,
                        observed=result,
                    ),
                ) from exc
            return {
                "evidence": [
                    "nixos-observer-render:" + validated["receipt_sha256"],
                    "nixos-observer-render-attempt:" + validated["attempt_ref"]["sha256"],
                    "nixos-observer-render-transport:" + validated["transport_ref"]["sha256"],
                    "nixos-observer-render-terminal:" + validated["terminal_ref"]["sha256"],
                    "nixos-observer-render-replay:" + validated["replay_ref"]["sha256"],
                ],
                "terminal": validated["terminal"],
            }

        return invoke

    @staticmethod
    def _reviewed_evaluation_provider(provider: Callable[[Mapping[str, str]], Mapping[str, Any]]) -> Callable[[Mapping[str, str]], Mapping[str, Any]]:
        """Validate immutable provider output before it becomes authority evidence."""

        def invoke(parameters: Mapping[str, str]) -> Mapping[str, Any]:
            generation = parameters["generation"]
            bound = {key: value for key, value in parameters.items() if key != "generation"}
            result = provider({"kind": "nixos-reviewed-evaluation", "generation": generation, "parameters": bound})
            exact = {
                "schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
                "outcome": "verified",
                "source_commit": bound["source_commit"],
                "source_tree": bound["source_tree"],
                "source_archive_sha256": parameters["source_archive_sha256"],
                "flake_lock_sha256": parameters["flake_lock_sha256"],
                "module_sha256": parameters["module_sha256"],
                "provider_sha256": parameters["provider_sha256"],
                "ssh_sha256": parameters["ssh_sha256"],
                "known_hosts_sha256": parameters["known_hosts_sha256"],
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
            scratch_root = result.get("scratch_root")
            valid_scratch_root = (
                isinstance(scratch_root, Mapping)
                and set(scratch_root) == {"path", "created_by_attempt", "final_state"}
                and scratch_root.get("path") == "/var/tmp/tgw-reviewed-evaluation"
                and isinstance(scratch_root.get("created_by_attempt"), bool)
            )
            if not valid_scratch_root:
                raise EffectHandlerError("reviewed Nix evaluation scratch-root receipt is invalid")
            expected_final = "removed" if scratch_root["created_by_attempt"] else "retained-existing"
            if scratch_root.get("final_state") != expected_final:
                raise EffectHandlerError("reviewed Nix evaluation scratch-root rollback is invalid")
            expected_executables = {
                "git": "/run/current-system/sw/bin/git",
                "nix": "/run/current-system/sw/bin/nix",
                "nix_store": "/run/current-system/sw/bin/nix-store",
                "systemd_analyze": "/run/current-system/sw/bin/systemd-analyze",
            }
            if result.get("executables") != expected_executables:
                raise EffectHandlerError("reviewed Nix evaluation executable provenance mismatch")
            expected_digests = {name: parameters[name + "_sha256"] for name in ("remote_python", "git", "nix", "nix_store", "systemd_analyze")}
            if result.get("executable_sha256") != expected_digests:
                raise EffectHandlerError("reviewed Nix evaluation executable digest mismatch")
            digest_keys = (
                "closure_manifest_sha256",
                "eval_log_sha256",
                "build_log_sha256",
                "systemd_verify_output_sha256",
                "verifier_metadata_sha256",
                "receipt_sha256",
            )
            if any(not isinstance(result.get(key), str) or not _SHA256.fullmatch(result[key]) for key in digest_keys):
                raise EffectHandlerError("reviewed Nix evaluation receipt digest is invalid")
            closure_count = result.get("closure_path_count")
            if not isinstance(closure_count, int) or not 1 <= closure_count <= 10_000:
                raise EffectHandlerError("reviewed Nix evaluation closure count is invalid")
            manifest = result.get("closure_manifest")
            if not isinstance(manifest, list) or len(manifest) != closure_count:
                raise EffectHandlerError("reviewed Nix evaluation closure manifest is absent")
            store_path = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+")
            paths = []
            for item in manifest:
                valid_entry = (
                    isinstance(item, Mapping)
                    and set(item) == {"path", "nar_sha256"}
                    and isinstance(item["path"], str)
                    and store_path.fullmatch(item["path"])
                    and isinstance(item["nar_sha256"], str)
                    and _SHA256.fullmatch(item["nar_sha256"])
                )
                if not valid_entry:
                    raise EffectHandlerError("reviewed Nix evaluation closure entry is invalid")
                paths.append(item["path"])
            manifest_hash = "sha256:" + hashlib.sha256(_canonical(manifest)).hexdigest()
            if paths != sorted(set(paths)) or result["closure_manifest_sha256"] != manifest_hash or result.get("closure_manifest_ref") != "inline:" + manifest_hash:
                raise EffectHandlerError("reviewed Nix evaluation closure manifest binding mismatch")
            if not isinstance(result.get("evaluated_config_drv"), str) or not _NIX_STORE_PATH.fullmatch(result["evaluated_config_drv"]):
                raise EffectHandlerError("reviewed Nix evaluation derivation identity is invalid")
            if result.get("systemd_verify_exit") != 0:
                raise EffectHandlerError("generated systemd units did not verify")
            if result.get("verifier_metadata") != {
                "schema": "tgw-review-egress-systemd-units/v1",
                "system": "x86_64-linux",
                "units": list(_REVIEW_EVAL_UNITS),
                "activation": False,
            }:
                raise EffectHandlerError("generated unit verifier metadata is invalid")
            try:
                expected_inputs = json.loads(parameters["input_closure_manifest_json"])
            except json.JSONDecodeError as exc:
                raise EffectHandlerError("reviewed Nix input closure parameter is invalid") from exc
            if (
                result.get("input_closure_manifest") != expected_inputs
                or result.get("input_closure_manifest_sha256") != parameters["input_closure_manifest_sha256"]
                or result.get("input_closure_path_count") != int(parameters["input_closure_path_count"])
            ):
                raise EffectHandlerError("reviewed Nix input closure evidence binding mismatch")
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

    def prepare(self, effect: TypedEffect) -> tuple[str, dict[str, Any], Callable[..., Mapping[str, Any]], Callable[..., Mapping[str, Any]] | None]:
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
            validate_platform_bootstrap_effect(effect.parameters)
            parameters = dict(effect.parameters)
        elif effect.kind is EffectKind.NIXOS_REVIEWED_EVALUATION:
            parameters = _required_strings(
                effect.parameters,
                {
                    "target_host",
                    "flake_repository_id",
                    "artifact_ref",
                    "source_commit",
                    "source_tree",
                    "source_archive_sha256",
                    "flake_lock_sha256",
                    "archive_root",
                    "module_path",
                    "module_sha256",
                    "provider_sha256",
                    "ssh_sha256",
                    "known_hosts_sha256",
                    "remote_python_sha256",
                    "git_sha256",
                    "nix_sha256",
                    "nix_store_sha256",
                    "systemd_analyze_sha256",
                    "scratch_id",
                    "system",
                    "evaluation_target",
                    "unit_set",
                    "output_schema",
                    "nix_network_policy",
                    "input_closure_manifest_json",
                    "input_closure_manifest_sha256",
                    "input_closure_path_count",
                    "minimum_systemd_version",
                    "max_duration_seconds",
                    "max_output_bytes",
                    "max_archive_bytes",
                    "max_unpacked_bytes",
                    "max_files",
                    "activate",
                    "profile_write",
                    "home_db_write",
                    "operation_id",
                },
            )
            _, parameters = _validate_remote_effect({"kind": "nixos-reviewed-evaluation", "generation": effect.generation, "parameters": parameters})
            fixed = {
                "target_host": "tgw-prod",
                "flake_repository_id": "tgw-flake",
                "archive_root": "trader-grims-warehouse",
                "module_path": "nix/review-egress.nix",
                "system": "x86_64-linux",
                "evaluation_target": "review-egress-systemd-units",
                "unit_set": ",".join(_REVIEW_EVAL_UNITS),
                "output_schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
                "nix_network_policy": "offline-no-substituters",
                "activate": "false",
                "profile_write": "false",
                "home_db_write": "false",
            }
            if any(parameters[key] != value for key, value in fixed.items()):
                raise ValueError("reviewed Nix evaluation target or safety invariant is outside the registered bound")
            if not _SHA1.fullmatch(parameters["source_commit"]) or not _SHA1.fullmatch(parameters["source_tree"]):
                raise ValueError("reviewed Nix source identity is invalid")
            digest_keys = (
                "source_archive_sha256",
                "flake_lock_sha256",
                "module_sha256",
                "provider_sha256",
                "ssh_sha256",
                "known_hosts_sha256",
                "remote_python_sha256",
                "git_sha256",
                "nix_sha256",
                "nix_store_sha256",
                "systemd_analyze_sha256",
            )
            for key in digest_keys:
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
                max_archive = int(parameters["max_archive_bytes"])
                max_unpacked = int(parameters["max_unpacked_bytes"])
                max_files = int(parameters["max_files"])
            except ValueError as exc:
                raise ValueError("reviewed Nix bounds must be decimal integers") from exc
            invalid_bounds = (
                minimum_systemd < 257
                or not 1 <= max_duration <= 900
                or not 1024 <= max_output <= 16 * 1024 * 1024
                or not 1024 <= max_archive <= 128 * 1024 * 1024
                or not max_archive <= max_unpacked <= 512 * 1024 * 1024
                or not 1 <= max_files <= 100_000
            )
            if invalid_bounds:
                raise ValueError("reviewed Nix resource or verifier bound is outside the registered range")
        elif effect.kind is EffectKind.NIXOS_OBSERVER_RENDER_EVALUATION:
            parameters = validate_render_request(effect.parameters)
            if any(parameters[key] is not False for key in ("activate", "profile_write", "home_db_write")):
                raise ValueError("observer render contains a forbidden activation or write effect")
        else:  # pragma: no cover - EffectKind is closed above
            raise ValueError("effect kind has no registered parameter validator")
        handler_id, handler, rollback = self._providers[effect.kind]
        parameters["generation"] = effect.generation
        if effect.kind is EffectKind.APPROVAL_PLATFORM_BOOTSTRAP_DEPLOYMENT:
            if self._bootstrap_validate is None:
                raise ValueError("platform-bootstrap pre-authority validator is unavailable")
            self._bootstrap_validate(parameters)
        return handler_id, parameters, handler, rollback


class AuthorityEffectController:
    """Atomically redeems authority before invoking one registered provider."""

    def __init__(self, registry: TypedEffectHandlerRegistry, consume_authority: Callable[..., Mapping[str, Any]]):
        self.registry = registry
        self.consume_authority = consume_authority

    def execute(self, *, request_id: str, effect: TypedEffect) -> EffectExecutionReceipt:
        handler_id, parameters, handler, rollback = self.registry.prepare(effect)
        try:
            authority = self.consume_authority(request_id, effect_hash=effect.effect_hash, generation=effect.generation)
        except BootstrapConsumptionAmbiguous as exc:
            authority_observation = exc.evidence[0]
            return self._receipt(
                request_id,
                authority_observation,
                effect,
                handler_id,
                EffectOutcome.AMBIGUOUS,
                exc.evidence,
                detail=str(exc),
            )
        receipt_id = str(authority["receipt_id"])
        try:
            result = handler(parameters)
            evidence = tuple(sorted(str(item) for item in result.get("evidence", ())))
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.SUCCEEDED, evidence)
        except RetryableEffect as exc:
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.RETRY, exc.evidence, detail=str(exc))
        except HeldEffect as exc:
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.HOLD, exc.evidence, detail=str(exc))
        except AmbiguousEffect as exc:
            evidence = exc.evidence
            if not evidence:
                observation = {
                    "schema": "tgw-effect-ambiguity-observation/v1",
                    "request_id": request_id,
                    "authority_receipt_id": receipt_id,
                    "effect_hash": effect.effect_hash,
                    "generation": effect.generation,
                    "handler_id": handler_id,
                    "detail": str(exc),
                }
                evidence = ("effect-ambiguity-memory:sha256:" + hashlib.sha256(_canonical(observation)).hexdigest(),)
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.AMBIGUOUS, evidence, detail=str(exc))
        except BootstrapStateAmbiguous as exc:
            if not exc.rollback_required:
                return self._receipt(
                    request_id, receipt_id, effect, handler_id, EffectOutcome.AMBIGUOUS, exc.evidence, detail=str(exc)
                )
            if rollback is not None:
                try:
                    rolled_back = rollback(parameters)
                    rollback_receipt = str(rolled_back["receipt"])
                    return self._receipt(
                        request_id,
                        receipt_id,
                        effect,
                        handler_id,
                        EffectOutcome.ROLLED_BACK,
                        exc.evidence,
                        rollback_receipt=rollback_receipt,
                        detail=str(exc),
                    )
                except BootstrapStateAmbiguous as rollback_exc:
                    evidence = tuple(sorted(set(exc.evidence + rollback_exc.evidence)))
                    return self._receipt(
                        request_id,
                        receipt_id,
                        effect,
                        handler_id,
                        EffectOutcome.AMBIGUOUS,
                        evidence,
                        detail=f"effect={exc}; rollback={rollback_exc}",
                    )
                except Exception as rollback_exc:
                    return self._receipt(
                        request_id,
                        receipt_id,
                        effect,
                        handler_id,
                        EffectOutcome.FAILED,
                        exc.evidence,
                        detail=f"effect={exc}; rollback={rollback_exc}",
                    )
            return self._receipt(
                request_id, receipt_id, effect, handler_id, EffectOutcome.AMBIGUOUS, exc.evidence, detail=str(exc)
            )
        except Exception as exc:
            if rollback is not None:
                try:
                    rolled_back = rollback(parameters)
                    rollback_receipt = str(rolled_back["receipt"])
                    return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.ROLLED_BACK, (), rollback_receipt=rollback_receipt, detail=str(exc))
                except Exception as rollback_exc:
                    if isinstance(rollback_exc, BootstrapStateAmbiguous):
                        return self._receipt(
                            request_id,
                            receipt_id,
                            effect,
                            handler_id,
                            EffectOutcome.AMBIGUOUS,
                            rollback_exc.evidence,
                            detail=f"effect={exc}; rollback={rollback_exc}",
                        )
                    return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.FAILED, (), detail=f"effect={exc}; rollback={rollback_exc}")
            evidence = exc.evidence if isinstance(exc, EffectHandlerError) else ()
            return self._receipt(request_id, receipt_id, effect, handler_id, EffectOutcome.FAILED, evidence, detail=str(exc))

    @staticmethod
    def _receipt(
        request_id: str, authority_receipt_id: str, effect: TypedEffect, handler_id: str, outcome: EffectOutcome, evidence: tuple[str, ...], *, rollback_receipt: str | None = None, detail: str = ""
    ) -> EffectExecutionReceipt:
        return EffectExecutionReceipt(
            "tgw-effect-execution-receipt/v1", request_id, authority_receipt_id, effect.effect_hash, effect.kind.value, effect.generation, handler_id, outcome, evidence, rollback_receipt, detail
        )

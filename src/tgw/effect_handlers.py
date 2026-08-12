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


class TypedEffectHandlerRegistry:
    """Closed registry of the four Plan-declared effect classes."""

    def __init__(
        self,
        *,
        release_install: Callable[[Mapping[str, str]], Mapping[str, Any]],
        release_rollback: Callable[[Mapping[str, str]], Mapping[str, Any]],
        flake_push: Callable[[Mapping[str, str]], Mapping[str, Any]],
        flake_switch_record: Callable[[Mapping[str, str]], Mapping[str, Any]],
        dependency_resubmit: Callable[[Mapping[str, str]], Mapping[str, Any]],
    ) -> None:
        self._providers = {
            EffectKind.CODING_RELEASE: ("immutable-release-installer@1", release_install, release_rollback),
            EffectKind.BOUNDED_FLAKE_PUSH: ("bounded-flake-push@1", flake_push, None),
            EffectKind.FLAKE_SWITCH_RECORD_ONLY: ("flake-switch-record-only@1", flake_switch_record, None),
            EffectKind.DEPENDENCY_RESUBMIT: ("dependency-resubmit@1", dependency_resubmit, None),
        }

    def prepare(self, effect: TypedEffect) -> tuple[str, dict[str, str], Callable[..., Mapping[str, Any]], Callable[..., Mapping[str, Any]] | None]:
        if effect.kind is EffectKind.CODING_RELEASE:
            parameters = _required(effect.parameters, {"candidate_commit", "candidate_tree", "archive_sha256", "artifact_ref", "expected_current", "operation_id"})
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
        else:
            parameters = _required(effect.parameters, {"dependency_id", "queue_id", "failed_generation"})
            if parameters["queue_id"] not in {"coding", "review", "controller", "release"}:
                raise ValueError("dependency queue is not registered")
        handler_id, handler, rollback = self._providers[effect.kind]
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

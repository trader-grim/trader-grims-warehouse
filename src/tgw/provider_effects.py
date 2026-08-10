"""Durable exactly-bound reservations for externally visible provider effects.

This does not promise exactly-once delivery (an HTTP response can be lost).
Instead, a crash after dispatch becomes durable ambiguity and can only advance
through explicit read-only reconciliation; it is never blindly dispatched.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

import psycopg2.extras

from tgw.queue import state_machine


class ProviderEffectConflict(RuntimeError):
    pass


class ProviderEffectReconciliationRequired(RuntimeError):
    def __init__(self, record: "ProviderEffect") -> None:
        super().__init__(f"provider effect {record.effect_id} requires reconciliation")
        self.record = record


@dataclass(frozen=True)
class ProviderEffect:
    effect_id: str
    provider: str
    operation: str
    entity_type: str
    entity_id: str
    object_generation: str
    graph_id: str
    treatment_id: str
    treatment_version: str
    condition_hash: str
    request: dict[str, Any]
    authority: dict[str, Any]
    state: str
    result: dict[str, Any] | None = None
    error_detail: str = ""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def effect_identity(**binding: Any) -> str:
    return hashlib.sha256(_canonical(binding)).hexdigest()


def _authority_json(authority: Any) -> dict[str, Any]:
    value = asdict(authority)
    for key in ("issued_at", "expires_at", "superseded_at"):
        if value.get(key) is not None:
            value[key] = value[key].isoformat()
    return value


def _historical_effect_matches(
    record: ProviderEffect, *, authority_id: str, authority_scope: str,
    authority_binding: Mapping[str, str], effect_binding: Mapping[str, Any],
) -> bool:
    """Check an effect against immutable dispatch-time authority evidence."""
    stored = record.authority
    if stored.get("authority_id") != authority_id:
        return False
    if authority_scope not in stored.get("scopes", ()):
        return False
    if any(stored.get(key) != value for key, value in authority_binding.items()):
        return False
    return record.effect_id == effect_identity(
        **dict(effect_binding), authority=stored,
    )


def reserve_and_begin_authorized_effect(
    *, authority_id: str, authority_scope: str,
    authority_binding: Mapping[str, str], provider: str, operation: str,
    entity_type: str, entity_id: str, object_generation: str, graph_id: str,
    treatment_id: str, treatment_version: str, condition_hash: str,
    request: Mapping[str, Any],
) -> ProviderEffect:
    """Lock authority, validate, reserve, and mark dispatched atomically."""
    from tgw.workflow.operator_authority import get_authority, validate_authority

    with state_machine._conn() as con:
        authority = get_authority(authority_id, connection=con, for_update=True)
        effect_binding = {
            "provider": provider, "operation": operation, "entity_type": entity_type,
            "entity_id": entity_id, "object_generation": object_generation,
            "graph_id": graph_id, "treatment_id": treatment_id,
            "treatment_version": treatment_version, "condition_hash": condition_hash,
            "request": dict(request),
        }
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{provider}:{operation}:{entity_type}:{entity_id}",),
            )
            cur.execute(
                """SELECT * FROM provider_effects WHERE provider=%s AND operation=%s
                     AND entity_type=%s AND entity_id=%s AND state IN
                     ('reserved','dispatched','ambiguous','reconciliation_required')""",
                (provider, operation, entity_type, entity_id),
            )
            row = cur.fetchone()
            if row is not None:
                record = _record(row)
                if not _historical_effect_matches(
                    record, authority_id=authority_id,
                    authority_scope=authority_scope,
                    authority_binding=authority_binding,
                    effect_binding=effect_binding,
                ):
                    raise ProviderEffectConflict("provider effect entity has unresolved work")
                if record.state != "reserved":
                    raise ProviderEffectReconciliationRequired(record)
                validated, detail = validate_authority(
                    authority_id, scope=authority_scope,
                    lookup=lambda _: authority, **dict(authority_binding),
                )
                if validated is None:
                    raise ProviderEffectConflict(f"operator authority invalid: {detail}")
                cur.execute(
                    """UPDATE provider_effects SET state='dispatched',
                         dispatched_at=NOW(), updated_at=NOW()
                         WHERE effect_id=%s AND state='reserved' RETURNING *""",
                    (record.effect_id,),
                )
                return _record(cur.fetchone())
            cur.execute(
                """SELECT * FROM provider_effects WHERE provider=%s AND operation=%s
                     AND entity_type=%s AND entity_id=%s AND object_generation=%s""",
                (provider, operation, entity_type, entity_id, object_generation),
            )
            row = cur.fetchone()
            if row is not None:
                record = _record(row)
                if not _historical_effect_matches(
                    record, authority_id=authority_id,
                    authority_scope=authority_scope,
                    authority_binding=authority_binding,
                    effect_binding=effect_binding,
                ):
                    raise ProviderEffectConflict("provider effect binding mismatch")
                if record.state in {"succeeded", "rejected"}:
                    return record
                raise ProviderEffectReconciliationRequired(record)
            validated, detail = validate_authority(
                authority_id, scope=authority_scope,
                lookup=lambda _: authority, **dict(authority_binding),
            )
            if validated is None:
                raise ProviderEffectConflict(f"operator authority invalid: {detail}")
            authority_json = _authority_json(validated)
            effect_id = effect_identity(**effect_binding, authority=authority_json)
            cur.execute(
                """INSERT INTO provider_effects
                  (effect_id,provider,operation,entity_type,entity_id,object_generation,
                   graph_id,treatment_id,treatment_version,condition_hash,request_json,
                   authority_json,state,dispatched_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
                          'dispatched',NOW()) RETURNING *""",
                (effect_id, provider, operation, entity_type, entity_id,
                 object_generation, graph_id, treatment_id, treatment_version,
                 condition_hash, json.dumps(dict(request)), json.dumps(authority_json)),
            )
            return _record(cur.fetchone())


def validate_succeeded_authorized_effect(
    *, effect_id: str, authority_id: str, authority_scope: str,
    authority_binding: Mapping[str, str], expected_binding: Mapping[str, Any],
) -> ProviderEffect:
    """Validate immutable ledger/effect identity for post-effect repair replay."""
    from tgw.workflow.operator_authority import get_authority

    with state_machine._conn() as con:
        authority = get_authority(authority_id, connection=con, for_update=True)
        if authority is None:
            raise ProviderEffectConflict("operator authority absent")
        for key, value in authority_binding.items():
            if getattr(authority, key) != value:
                raise ProviderEffectConflict(f"operator authority {key} mismatch")
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM provider_effects WHERE effect_id=%s FOR SHARE",
                        (effect_id,))
            row = cur.fetchone()
            if row is None:
                raise ProviderEffectConflict("provider effect absent")
            record = _record(row)
            # Expiry/supersession after the irreversible effect is historical
            # state. Validate identity against the exact dispatch-time snapshot,
            # while the current row supplies immutable ledger corroboration.
            if not _historical_effect_matches(
                record, authority_id=authority_id,
                authority_scope=authority_scope,
                authority_binding=authority_binding,
                effect_binding=expected_binding,
            ):
                raise ProviderEffectConflict("provider effect identity mismatch")
            if record.state != "succeeded" or not record.result:
                raise ProviderEffectReconciliationRequired(record)
            return record


def lookup_authoritative_stage_receipt(
    *, sku: str, provider_effect_id: str, stage_content_identity: str,
    offer_id: str, expected_provider_identity: str,
) -> dict[str, str]:
    """Return canonical stage evidence only when the exact ledger effect succeeded."""
    with state_machine._conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM provider_effects WHERE effect_id=%s",
                (provider_effect_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise ProviderEffectConflict("stage provider effect absent")
    record = _record(row)
    exact_binding = {
        "provider": record.provider, "operation": record.operation,
        "entity_type": record.entity_type, "entity_id": record.entity_id,
        "object_generation": record.object_generation, "graph_id": record.graph_id,
        "treatment_id": record.treatment_id,
        "treatment_version": record.treatment_version,
        "condition_hash": record.condition_hash, "request": record.request,
        "authority": record.authority,
    }
    if effect_identity(**exact_binding) != provider_effect_id:
        raise ProviderEffectConflict("stage provider effect identity mismatch")
    if (record.provider != "ebay" or record.operation != "stage-draft"
            or record.entity_type != "item" or record.entity_id != sku
            or record.state != "succeeded" or not record.result):
        raise ProviderEffectConflict("stage provider effect is not exact succeeded evidence")
    if record.authority.get("provider_identity") != expected_provider_identity:
        raise ProviderEffectConflict("stage provider identity does not match authority")
    if (record.request.get("content_identity") != stage_content_identity
            or record.result.get("offer_id") != offer_id):
        raise ProviderEffectConflict("canonical stage evidence does not match provider receipt")
    return {
        "receipt_id": record.effect_id,
        "content_identity": stage_content_identity,
        "offer_id": offer_id,
    }


def lookup_succeeded_provider_effect(
    *, provider_effect_id: str, sku: str, provider_identity: str,
    expected_offer_id: str,
    operations: tuple[str, ...] = ("stage-draft", "publish-offer"),
) -> tuple[ProviderEffect, str]:
    """Resolve one exact successful source effect for targeted reconciliation."""
    with state_machine._conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM provider_effects WHERE effect_id=%s",
                        (provider_effect_id,))
            row = cur.fetchone()
    if row is None:
        raise ProviderEffectConflict("source provider effect absent")
    record = _record(row)
    binding = {
        "provider": record.provider, "operation": record.operation,
        "entity_type": record.entity_type, "entity_id": record.entity_id,
        "object_generation": record.object_generation, "graph_id": record.graph_id,
        "treatment_id": record.treatment_id,
        "treatment_version": record.treatment_version,
        "condition_hash": record.condition_hash, "request": record.request,
        "authority": record.authority,
    }
    if effect_identity(**binding) != provider_effect_id:
        raise ProviderEffectConflict("source provider effect identity mismatch")
    if (record.provider != "ebay" or record.operation not in operations
            or record.entity_type != "item" or record.entity_id != sku
            or record.state != "succeeded"
            or record.authority.get("provider_identity") != provider_identity):
        raise ProviderEffectConflict("source provider effect binding mismatch")
    if record.operation == "stage-draft":
        bound_offer_id = (record.result or {}).get("offer_id")
    else:
        bound_offer_id = record.request.get("offer_id")
        corroborated = ((record.result or {}).get("offer_id")
                        or (record.result or {}).get("offerId"))
        if corroborated and corroborated != bound_offer_id:
            raise ProviderEffectConflict("publish result contradicts requested offer")
    if (not isinstance(bound_offer_id, str) or not bound_offer_id.strip()
            or bound_offer_id != expected_offer_id):
        raise ProviderEffectConflict("source provider effect offer mismatch")
    return record, bound_offer_id


def _record(row: Mapping[str, Any]) -> ProviderEffect:
    return ProviderEffect(
        effect_id=str(row["effect_id"]), provider=str(row["provider"]),
        operation=str(row["operation"]), entity_type=str(row["entity_type"]),
        entity_id=str(row["entity_id"]),
        object_generation=str(row["object_generation"]), graph_id=str(row["graph_id"]),
        treatment_id=str(row["treatment_id"]),
        treatment_version=str(row["treatment_version"]),
        condition_hash=str(row["condition_hash"]), request=dict(row["request_json"]),
        authority=dict(row["authority_json"]), state=str(row["state"]),
        result=dict(row["result_json"]) if row.get("result_json") else None,
        error_detail=str(row.get("error_detail") or ""),
    )


def reserve_provider_effect(*, provider: str, operation: str, entity_type: str,
                            entity_id: str, object_generation: str, graph_id: str,
                            treatment_id: str, treatment_version: str,
                            condition_hash: str, request: Mapping[str, Any],
                            authority: Mapping[str, Any]) -> ProviderEffect:
    binding = {
        "provider": provider, "operation": operation, "entity_type": entity_type,
        "entity_id": entity_id, "object_generation": object_generation,
        "graph_id": graph_id, "treatment_id": treatment_id,
        "treatment_version": treatment_version, "condition_hash": condition_hash,
        "request": dict(request), "authority": dict(authority),
    }
    effect_id = effect_identity(**binding)
    # Serialize the whole logical external-effect seam, not merely one object
    # generation. A changed item must not bypass an older ambiguous dispatch.
    scope = f"{provider}:{operation}:{entity_type}:{entity_id}"
    with state_machine._conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (scope,))
            cur.execute(
                """SELECT * FROM provider_effects
                    WHERE provider=%s AND operation=%s AND entity_type=%s
                      AND entity_id=%s AND state IN
                        ('reserved','dispatched','ambiguous','reconciliation_required')""",
                (provider, operation, entity_type, entity_id),
            )
            unresolved = cur.fetchone()
            if unresolved is not None:
                record = _record(unresolved)
                if record.effect_id != effect_id:
                    raise ProviderEffectConflict(
                        "provider effect entity has an unresolved reservation"
                    )
                return record
            cur.execute(
                """SELECT * FROM provider_effects
                    WHERE provider=%s AND operation=%s AND entity_type=%s
                      AND entity_id=%s AND object_generation=%s""",
                (provider, operation, entity_type, entity_id, object_generation),
            )
            existing = cur.fetchone()
            if existing is not None:
                record = _record(existing)
                if record.effect_id != effect_id:
                    raise ProviderEffectConflict(
                        "provider effect scope already has a different exact binding"
                    )
                return record
            cur.execute(
                """INSERT INTO provider_effects
                    (effect_id, provider, operation, entity_type, entity_id,
                     object_generation, graph_id, treatment_id, treatment_version,
                     condition_hash, request_json, authority_json, state)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,'reserved')
                    RETURNING *""",
                (effect_id, provider, operation, entity_type, entity_id,
                 object_generation, graph_id, treatment_id, treatment_version,
                 condition_hash, json.dumps(dict(request)), json.dumps(dict(authority))),
            )
            return _record(cur.fetchone())


def begin_provider_dispatch(effect_id: str) -> ProviderEffect:
    with state_machine._conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """UPDATE provider_effects SET state='dispatched', dispatched_at=NOW(),
                       updated_at=NOW() WHERE effect_id=%s AND state='reserved'
                    RETURNING *""", (effect_id,),
            )
            row = cur.fetchone()
            if row is not None:
                return _record(row)
            cur.execute("SELECT * FROM provider_effects WHERE effect_id=%s", (effect_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(effect_id)
            record = _record(row)
            if record.state == "succeeded":
                return record
            raise ProviderEffectReconciliationRequired(record)


def finish_provider_effect(effect_id: str, *, state: str,
                           result: Mapping[str, Any] | None = None,
                           error_detail: str = "") -> ProviderEffect:
    if state not in {"succeeded", "rejected", "ambiguous", "reconciliation_required"}:
        raise ValueError(f"invalid provider effect outcome {state!r}")
    with state_machine._conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """UPDATE provider_effects SET state=%s, result_json=%s::jsonb,
                       error_detail=%s, finished_at=NOW(), updated_at=NOW()
                    WHERE effect_id=%s AND state='dispatched' RETURNING *""",
                (state, json.dumps(dict(result)) if result is not None else None,
                 error_detail[:2000], effect_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ProviderEffectConflict(
                    f"effect {effect_id} is not in dispatched state"
                )
            return _record(row)


def reconcile_provider_effect(
    effect_id: str, observe: Callable[[ProviderEffect], Mapping[str, Any]],
) -> ProviderEffect:
    """Apply a read-only provider observation to an ambiguous effect."""
    with state_machine._conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM provider_effects WHERE effect_id=%s", (effect_id,))
            row = cur.fetchone()
    if row is None:
        raise KeyError(effect_id)
    current = _record(row)
    if current.state == "succeeded":
        return current
    if current.state not in {"dispatched", "ambiguous", "reconciliation_required"}:
        raise ProviderEffectConflict(f"effect {effect_id} is not reconcilable")
    observation = dict(observe(current))
    outcome = observation.get("outcome")
    if outcome == "succeeded":
        # Reconciliation is an explicit transition from uncertainty.
        with state_machine._conn() as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """UPDATE provider_effects SET state='succeeded', result_json=%s::jsonb,
                           error_detail='', finished_at=NOW(), updated_at=NOW()
                        WHERE effect_id=%s AND state IN
                          ('dispatched','ambiguous','reconciliation_required') RETURNING *""",
                    (json.dumps(observation), effect_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ProviderEffectConflict("effect changed during reconciliation")
                return _record(row)
    return finish_reconciliation_required(effect_id, observation)


def finish_reconciliation_required(
    effect_id: str, observation: Mapping[str, Any],
) -> ProviderEffect:
    with state_machine._conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """UPDATE provider_effects SET state='reconciliation_required',
                       result_json=%s::jsonb, error_detail=%s, updated_at=NOW()
                    WHERE effect_id=%s AND state IN
                      ('dispatched','ambiguous','reconciliation_required') RETURNING *""",
                (json.dumps(dict(observation)), str(observation.get("detail") or "")[:2000],
                 effect_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ProviderEffectConflict("effect changed during reconciliation")
            return _record(row)

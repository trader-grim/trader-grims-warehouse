"""Immutable, read-only provider observations.

Observations are evidence about provider state.  They are deliberately not
provider effects, do not carry operator authority, and cannot dispatch work.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

import psycopg2.extras

from tgw.queue import state_machine

OBSERVATION_SCHEMA = "provider-observation/v1"
LEGACY_STAGE_RECEIPT_SCHEMA = "legacy-stage-corroboration/v1"
LEGACY_STAGE_OBSERVATION = "legacy-stage-corroboration"
OUTCOMES = frozenset({"corroborated", "contradicted", "indeterminate"})


class ProviderObservationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderObservation:
    observation_id: str
    schema_id: str
    observation_type: str
    provider: str
    provider_identity: str
    sku: str
    offer_id: str
    object_generation: str
    graph_id: str
    condition_hash: str
    content_identity: str
    outcome: str
    evidence: dict[str, Any]
    observed_at: str


@dataclass(frozen=True)
class LegacyStageCorroborationReceipt:
    receipt_schema_id: str
    observation_id: str
    provider: str
    provider_identity: str
    sku: str
    offer_id: str
    object_generation: str
    graph_id: str
    condition_hash: str
    content_identity: str
    outcome: str
    evidence: dict[str, Any]
    observed_at: str


def _json_native(value: Any, path: str = "value") -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _json_native(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} contains a non-string key")
            _json_native(child, f"{path}.{key}")
        return
    raise TypeError(f"{path} contains non-JSON value {type(value).__name__}")


def _canonical(value: Any) -> bytes:
    _json_native(value)
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _canonical_observed_at(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _require_text("observed_at", value)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(
        timespec="microseconds",
    ).replace("+00:00", "Z")


def observation_identity(**binding: Any) -> str:
    """Return the deterministic identity of one exact provider observation."""
    return hashlib.sha256(_canonical(binding)).hexdigest()


def build_provider_observation(
    *, provider: str, provider_identity: str, sku: str, offer_id: str,
    object_generation: str, graph_id: str, condition_hash: str,
    content_identity: str, outcome: str, evidence: Mapping[str, Any],
    observed_at: str,
    observation_type: str = LEGACY_STAGE_OBSERVATION,
) -> ProviderObservation:
    values = {
        name: _require_text(name, value)
        for name, value in {
            "provider": provider, "provider_identity": provider_identity,
            "sku": sku, "offer_id": offer_id,
            "object_generation": object_generation, "graph_id": graph_id,
            "condition_hash": condition_hash,
            "content_identity": content_identity,
            "observation_type": observation_type,
        }.items()
    }
    values["observed_at"] = _canonical_observed_at(observed_at)
    if outcome not in OUTCOMES:
        raise ValueError(f"unsupported provider observation outcome {outcome!r}")
    evidence_value = dict(evidence)
    binding = {
        "schema_id": OBSERVATION_SCHEMA, **values, "outcome": outcome,
        "evidence": evidence_value,
    }
    observation_id = observation_identity(**binding)
    return ProviderObservation(observation_id=observation_id, **binding)


def record_provider_observation(
    observation: ProviderObservation, *, connection: Any | None = None,
) -> ProviderObservation:
    """Insert an immutable observation or return its exact durable replay."""
    expected = asdict(observation)
    if observation != build_provider_observation(
        provider=observation.provider,
        provider_identity=observation.provider_identity,
        sku=observation.sku, offer_id=observation.offer_id,
        object_generation=observation.object_generation,
        graph_id=observation.graph_id, condition_hash=observation.condition_hash,
        content_identity=observation.content_identity, outcome=observation.outcome,
        evidence=observation.evidence, observed_at=observation.observed_at,
        observation_type=observation.observation_type,
    ):
        raise ProviderObservationConflict("observation identity does not match binding")

    def persist(con: Any) -> ProviderObservation:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO provider_observations
                   (observation_id,schema_id,observation_type,provider,
                    provider_identity,sku,offer_id,object_generation,graph_id,
                    condition_hash,content_identity,outcome,evidence_json,observed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (observation_id) DO NOTHING""",
                (
                    observation.observation_id, observation.schema_id,
                    observation.observation_type, observation.provider,
                    observation.provider_identity, observation.sku,
                    observation.offer_id, observation.object_generation,
                    observation.graph_id, observation.condition_hash,
                    observation.content_identity, observation.outcome,
                    json.dumps(observation.evidence, allow_nan=False),
                    observation.observed_at,
                ),
            )
            cur.execute(
                "SELECT * FROM provider_observations WHERE observation_id=%s",
                (observation.observation_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("provider observation insert was not durable")
        durable = _record(row)
        if _canonical(asdict(durable)) != _canonical(expected):
            raise ProviderObservationConflict(
                "durable provider observation binding mismatch"
            )
        return durable

    if connection is not None:
        return persist(connection)
    with state_machine._conn() as con:
        return persist(con)


def resolve_legacy_stage_corroboration(
    observation: ProviderObservation, *, sku: str, offer_id: str,
    provider: str, provider_identity: str, object_generation: str,
    graph_id: str, condition_hash: str, content_identity: str,
) -> LegacyStageCorroborationReceipt:
    """Purely resolve exact legacy staged-offer evidence into a typed receipt."""
    if provider != "ebay" or observation.provider != "ebay":
        raise ProviderObservationConflict("legacy stage provider must be ebay")
    if observation.outcome != "corroborated":
        raise ProviderObservationConflict("legacy stage observation is not corroborated")
    expected = {
        "sku": sku, "offer_id": offer_id, "provider": provider,
        "provider_identity": provider_identity,
        "object_generation": object_generation, "graph_id": graph_id,
        "condition_hash": condition_hash, "content_identity": content_identity,
    }
    if observation.schema_id != OBSERVATION_SCHEMA:
        raise ProviderObservationConflict("provider observation schema mismatch")
    if observation.observation_type != LEGACY_STAGE_OBSERVATION:
        raise ProviderObservationConflict("provider observation type mismatch")
    if any(getattr(observation, key) != value for key, value in expected.items()):
        raise ProviderObservationConflict("legacy stage corroboration binding mismatch")
    rebuilt = build_provider_observation(
        provider=observation.provider,
        provider_identity=observation.provider_identity,
        sku=observation.sku, offer_id=observation.offer_id,
        object_generation=observation.object_generation,
        graph_id=observation.graph_id, condition_hash=observation.condition_hash,
        content_identity=observation.content_identity,
        outcome=observation.outcome, evidence=observation.evidence,
        observed_at=observation.observed_at,
        observation_type=observation.observation_type,
    )
    if rebuilt.observation_id != observation.observation_id:
        raise ProviderObservationConflict("provider observation identity mismatch")
    return LegacyStageCorroborationReceipt(
        receipt_schema_id=LEGACY_STAGE_RECEIPT_SCHEMA,
        observation_id=observation.observation_id,
        provider=provider, provider_identity=provider_identity, sku=sku,
        offer_id=offer_id, object_generation=object_generation,
        graph_id=graph_id, condition_hash=condition_hash,
        content_identity=content_identity, outcome=observation.outcome,
        evidence=dict(observation.evidence), observed_at=observation.observed_at,
    )


def _record(row: Mapping[str, Any]) -> ProviderObservation:
    return ProviderObservation(
        observation_id=str(row["observation_id"]), schema_id=row["schema_id"],
        observation_type=row["observation_type"], provider=row["provider"],
        provider_identity=row["provider_identity"], sku=row["sku"],
        offer_id=row["offer_id"], object_generation=row["object_generation"],
        graph_id=row["graph_id"], condition_hash=row["condition_hash"],
        content_identity=row["content_identity"], outcome=row["outcome"],
        evidence=dict(row["evidence_json"]),
        observed_at=_canonical_observed_at(row["observed_at"]),
    )

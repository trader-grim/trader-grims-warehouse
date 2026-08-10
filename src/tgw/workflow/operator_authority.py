"""Authoritative, durable operator authorization for provider effects."""
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from tgw.queue import state_machine

_SCOPES = frozenset({"upload", "stage", "publish", "force-restage"})


def listing_content_identity(item: Mapping[str, Any]) -> str:
    import hashlib
    value = {key: item.get(key) for key in (
        "condition", "draft_listing", "ebay_category_id", "ebay_photos",
        "item_specifics", "title",
    )}
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


@dataclass(frozen=True)
class OperatorAuthority:
    authority_id: str
    operator_identity: str
    surface: str
    entity_id: str
    goal_profile_id: str
    goal_profile_version: str
    object_generation: str
    pre_authority_condition_hash: str
    content_identity: str
    provider_identity: str
    scopes: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    superseded_at: datetime | None = None
    superseded_by: str | None = None


def _connection(connection=None):
    return nullcontext(connection) if connection is not None else state_machine._conn()


def issue_authority(*, operator_identity: str, surface: str, entity_id: str,
                    goal_profile_id: str, goal_profile_version: str,
                    object_generation: str, pre_authority_condition_hash: str,
                    content_identity: str, provider_identity: str,
                    scopes: tuple[str, ...], issued_at: datetime,
                    expires_at: datetime, connection=None) -> str:
    """Trusted issuer seam. Public HTTP handlers must not call this implicitly."""
    scopes = tuple(sorted(set(scopes)))
    values = (operator_identity, surface, entity_id, goal_profile_id,
              goal_profile_version, object_generation, pre_authority_condition_hash,
              content_identity, provider_identity)
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError("authority bindings must be non-empty")
    if not scopes or not set(scopes).issubset(_SCOPES):
        raise ValueError("invalid authority scopes")
    if issued_at.tzinfo is None or expires_at.tzinfo is None or expires_at <= issued_at:
        raise ValueError("authority timestamps must be aware and increasing")
    authority_id = str(uuid.uuid4())
    with _connection(connection) as con, con.cursor() as cur:
        cur.execute("""INSERT INTO operator_authorities
          (authority_id, operator_identity, surface, entity_id, goal_profile_id,
           goal_profile_version, object_generation, pre_authority_condition_hash,
           content_identity,
           provider_identity, scopes, issued_at, expires_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
          (authority_id, *values, list(scopes), issued_at, expires_at))
    return authority_id


def get_authority(authority_id: str, *, connection=None,
                  for_update: bool = False) -> OperatorAuthority | None:
    try:
        uuid.UUID(authority_id)
    except (AttributeError, TypeError, ValueError):
        return None
    with _connection(connection) as con, con.cursor() as cur:
        suffix = " FOR UPDATE" if for_update else ""
        cur.execute(
            """SELECT authority_id, operator_identity, surface, entity_id,
                      goal_profile_id, goal_profile_version, object_generation,
                      pre_authority_condition_hash, content_identity,
                      provider_identity, scopes,
                      issued_at, expires_at, superseded_at, superseded_by
                 FROM operator_authorities WHERE authority_id=%s""" + suffix,
            (authority_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return OperatorAuthority(*row[:10], tuple(row[10]), *row[11:])


def supersede_authority(authority_id: str, *, superseded_by: str,
                        connection=None) -> bool:
    if not superseded_by.strip():
        raise ValueError("superseding authority identity required")
    with _connection(connection) as con, con.cursor() as cur:
        # Lock in a separate statement. Under READ COMMITTED, a single UPDATE
        # that waits for this row can retain a statement snapshot from before a
        # concurrent dispatcher committed its provider_effects row, allowing
        # both dispatch and supersession to win. Each statement below receives
        # a fresh snapshot after the row lock has been acquired.
        cur.execute(
            "SELECT superseded_at FROM operator_authorities "
            "WHERE authority_id=%s FOR UPDATE",
            (authority_id,),
        )
        row = cur.fetchone()
        if row is None or row[0] is not None:
            return False
        cur.execute(
            """SELECT 1 FROM provider_effects
                WHERE authority_json->>'authority_id'=%s
                  AND state IN ('dispatched','ambiguous','reconciliation_required')
                LIMIT 1""",
            (authority_id,),
        )
        if cur.fetchone() is not None:
            return False
        cur.execute(
            """UPDATE operator_authorities
                SET superseded_at=NOW(), superseded_by=%s
                WHERE authority_id=%s AND superseded_at IS NULL""",
            (superseded_by, authority_id),
        )
        return cur.rowcount == 1


def validate_authority(authority_id: str | None, *, entity_id: str,
                       goal_profile_id: str, goal_profile_version: str,
                       object_generation: str, pre_authority_condition_hash: str,
                       content_identity: str, provider_identity: str, scope: str,
                       now: datetime | None = None,
                       lookup: Callable[[str], OperatorAuthority | None] = get_authority,
                       ) -> tuple[OperatorAuthority | None, str]:
    if not authority_id:
        return None, "operator authority absent"
    authority = lookup(authority_id)
    if authority is None or authority.superseded_at is not None:
        return None, "operator authority absent or superseded"
    expected = (entity_id, goal_profile_id, goal_profile_version, object_generation,
                pre_authority_condition_hash, content_identity, provider_identity)
    actual = (authority.entity_id, authority.goal_profile_id,
              authority.goal_profile_version, authority.object_generation,
              authority.pre_authority_condition_hash, authority.content_identity,
              authority.provider_identity)
    if actual != expected or scope not in authority.scopes:
        return None, "operator authority binding mismatch"
    current = now or datetime.now(UTC)
    if current < authority.issued_at or current >= authority.expires_at:
        return None, "operator authority outside validity window"
    return authority, "operator authority exact durable binding valid"


def validate_provider_effect_authority(authority_id: str, *, scope: str,
                                       binding: Mapping[str, str], lookup=get_authority):
    """Provider-worker seam: revalidate the current effect binding at execution."""
    return validate_authority(authority_id, scope=scope, lookup=lookup, **binding)

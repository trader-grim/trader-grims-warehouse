"""Live W18 lifecycle checkpoint source for a quiet fleet transition."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from tgw.plan_authority import PostgresAuthorityStore

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TERMINAL = frozenset({"succeeded", "hold", "ambiguous", "rolled_back", "failed", "legacy-consumed"})


class LifecycleSnapshotError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _text_time(value: Any) -> str | None:
    parsed = _time(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def compile_lifecycle_snapshot(
    *, generation: str, rows: Sequence[Mapping[str, Any]], surfaces: Sequence[Mapping[str, Any]],
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Compile all still-live authority, role, surface and continuation state."""
    if not isinstance(generation, str) or _HASH.fullmatch(generation) is None:
        raise LifecycleSnapshotError("lifecycle generation is invalid")
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    live_requests: list[dict[str, Any]] = []
    role_leases: list[dict[str, Any]] = []
    continuations: list[dict[str, Any]] = []
    live_ids: set[str] = set()
    for row in sorted(rows, key=lambda item: str(item.get("request_id"))):
        request_id = row.get("request_id")
        outcome = row.get("outcome")
        active_attempt = row.get("receipt_id") is not None and row.get("completed_at") is None
        expiry = _time(row.get("expires_at"))
        if not isinstance(request_id, str) or not request_id or expiry is None:
            raise LifecycleSnapshotError("authority lifecycle row is invalid")
        if (
            outcome in _TERMINAL or (not active_attempt and expiry <= now)
        ):
            continue
        parameters = row.get("effect_parameters")
        lifecycle = parameters.get("lifecycle") if isinstance(parameters, Mapping) else None
        lifecycle_hash = lifecycle.get("lifecycle_hash") if isinstance(lifecycle, Mapping) else None
        record = {
            "request_id": request_id, "effect_hash": row.get("effect_hash"),
            "effect_generation": row.get("effect_generation"), "decision": row.get("decision_kind"),
            "receipt_id": str(row["receipt_id"]) if row.get("receipt_id") is not None else None,
            "outcome": outcome, "expires_at": _text_time(expiry),
            "lifecycle_hash": lifecycle_hash,
        }
        live_requests.append(record)
        live_ids.add(request_id)
        if not isinstance(lifecycle, Mapping):
            continue
        cards = lifecycle.get("launch_cards")
        if isinstance(cards, list):
            for card in cards:
                if not isinstance(card, Mapping) or card.get("state") != "PREPARED":
                    continue
                lease = card.get("lease")
                lease_expiry = _time(lease.get("expires_at")) if isinstance(lease, Mapping) else None
                if not isinstance(lease, Mapping) or lease_expiry is None:
                    raise LifecycleSnapshotError("prepared role lease is invalid")
                if lease_expiry <= now:
                    continue
                role_leases.append({
                    "request_id": request_id, "lease_id": lease.get("id"), "expires_at": _text_time(lease_expiry),
                    "unit": card.get("unit"), "role": card.get("role"),
                    "idempotency_key": card.get("idempotency_key"), "lifecycle_hash": lifecycle_hash,
                })
                continuations.append({
                    "request_id": request_id, "unit": card.get("unit"), "role": card.get("role"),
                    "idempotency_key": card.get("idempotency_key"), "state": card.get("state"),
                    "lifecycle_hash": lifecycle_hash,
                })
    rendered_surfaces: list[dict[str, Any]] = []
    for surface in sorted(surfaces, key=lambda item: str(item.get("surface_hash"))):
        source = surface.get("source") if isinstance(surface, Mapping) else None
        surface_expiry = _time(source.get("expiry")) if isinstance(source, Mapping) else None
        if surface.get("status") == "LIVE" and surface_expiry is None:
            raise LifecycleSnapshotError("retained live dynamic surface expiry is invalid")
        if (
            surface.get("schema") != "tgw-dynamic-surface/v1" or surface.get("status") != "LIVE"
            or not isinstance(source, Mapping) or source.get("request_id") not in live_ids
            or surface_expiry <= now
        ):
            continue
        unsigned = dict(surface)
        claimed = unsigned.pop("surface_hash", None)
        if claimed != _hash(unsigned):
            raise LifecycleSnapshotError("retained dynamic surface hash is invalid")
        rendered_surfaces.append({
            "surface_id": source.get("surface_id"), "surface_hash": claimed,
            "request_id": source.get("request_id"), "card_hash": source.get("card_hash"),
            "expiry": _text_time(surface_expiry),
        })
    collections = {
        "live_requests": live_requests, "role_leases": role_leases,
        "rendered_surfaces": rendered_surfaces, "continuations": continuations,
    }
    unsigned = {
        "schema": "tgw-w18-lifecycle-snapshot/v1", "generation": generation,
        "observed_at": now.isoformat().replace("+00:00", "Z"), "collections": collections,
    }
    return {**unsigned, "snapshot_hash": _hash(unsigned)}


class LifecycleSnapshotSource:
    """Read a bounded authority projection and retained surfaces after gating."""

    def __init__(self, binding: Mapping[str, Any], *, store: Any | None = None):
        fields = {"schema", "dsn_env", "surface_root", "max_records"}
        root = Path(binding.get("surface_root")) if isinstance(binding, Mapping) and isinstance(binding.get("surface_root"), str) else Path()
        limit = binding.get("max_records") if isinstance(binding, Mapping) else None
        env_name = binding.get("dsn_env") if isinstance(binding, Mapping) else None
        if (
            not isinstance(binding, Mapping) or set(binding) != fields
            or binding.get("schema") != "tgw-lifecycle-snapshot-source/v1"
            or not isinstance(env_name, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", env_name) is None
            or not root.is_absolute() or root == Path("/tmp") or Path("/tmp") in root.parents
            or not root.is_dir() or root.is_symlink()
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000
        ):
            raise LifecycleSnapshotError("lifecycle snapshot source binding is invalid")
        dsn = os.environ.get(env_name)
        if store is None and not dsn:
            raise LifecycleSnapshotError("lifecycle snapshot database credential is unavailable")
        self.store = store or PostgresAuthorityStore(dsn)
        self.root, self.limit = root, limit

    def snapshot(self, generation: str) -> dict[str, Any]:
        rows = list(self.store.list(self.limit + 1))
        paths = sorted(self.root.glob("*.surface.json"))
        if len(rows) > self.limit or len(paths) > self.limit:
            raise LifecycleSnapshotError("lifecycle snapshot exceeds its configured bound")
        surfaces: list[Mapping[str, Any]] = []
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise LifecycleSnapshotError("retained dynamic surface is not a regular file")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LifecycleSnapshotError("retained dynamic surface is invalid") from exc
            if not isinstance(value, Mapping):
                raise LifecycleSnapshotError("retained dynamic surface is invalid")
            surfaces.append(value)
        return compile_lifecycle_snapshot(generation=generation, rows=rows, surfaces=surfaces)

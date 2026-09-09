"""Semi-automatic executor selection for the coding lifecycle.

This is the decision half of the planned model selector (NEXT-EVOLUTION §7-8):
given a role and a map of which executors are currently usable, pick the first
one an ordered per-role policy allows, and explain the choice. The availability
map is operator-maintained JSON for now; when the live availability prober
(Todo 1916) lands it writes the same file on a schedule and nothing here
changes. That prober is ``tgw.model_availability_refresh`` (LEAF-11-8 / Todo
1956) — see ``docs/runbooks/model-availability-refresh-v1-20260909.md`` for
the daily timer and the ``freshness: frozen`` opt-out.

No silent fallback: if nothing in the policy is available the selection is
ABSTAIN with a reason, and the caller decides what to do with that.

Live health (Todo 1956 dispatch-outcome slice): on top of the file's static
``available`` flags, ``select_executor`` consults
``tgw.model_observations.recent_status`` — the append-only log of dispatch
outcomes the harness writes after every session attempt. An executor with an
active observation hold is skipped exactly like one marked unavailable, and
the hold reason is carried in ``Selection.reason``. An explicit operator pin
(``$TGW_IMPLEMENT_EXECUTOR`` / ``$TGW_REVIEW_EXECUTOR``) still wins over a
hold, with the reason noting it. This module only reads observations — the
harness is the sole writer.

Availability file (first found wins):
  1. ``$TGW_MODEL_AVAILABILITY``
  2. ``/opt/TGW/tgw-lib/config/model-availability.json``  (operator-editable)
  3. ``<repo>/config/model-availability.json``            (committed default)

Shape::

    {
      "updated": "2026-09-04",
      "executors": {
        "claude":   {"available": true,  "models": ["claude-sonnet-5", ...]},
        "opencode": {"available": true},
        "codex":    {"available": false, "reason": "20x Pro lapsed 2026-08-29"},
        "manual":   {"available": true}
      },
      "roles": {
        "implementation": {"prefer": ["opencode", "claude", "manual"]},
        "review":         {"prefer": ["claude", "opencode", "manual"]}
      }
    }

``select_executor`` only reads ``executors`` and ``roles.<role>.prefer``. Any
other keys (per-role ``model`` hints, a full research role chart, comments) are
carried for humans and the future prober and ignored here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tgw import coding_executor_catalog, model_observations

SCHEMA = "tgw-model-selection/v1"

_CONFIG_PATH = Path("/opt/TGW/tgw-lib/config/model-availability.json")
_REPO_DEFAULT = Path(__file__).resolve().parent.parent.parent / "config" / "model-availability.json"

# Names a policy/availability file may reference beyond the coding-executor
# catalogue: ``manual`` is the always-available supervised-session fallback (it
# has no binary or install, so it is not a catalogue entry).
_SELECTOR_BUILTINS: frozenset[str] = frozenset({"manual"})


def known_executors() -> frozenset[str]:
    """Every executor name the selector accepts — the W1 coding-executor
    catalogue (the single source of truth) plus the selector built-ins. There
    is no hard-coded executor list here."""
    try:
        catalog = frozenset(coding_executor_catalog.executor_names())
    except coding_executor_catalog.CatalogError:
        catalog = frozenset()
    return catalog | _SELECTOR_BUILTINS


# Back-compat module attribute (a snapshot at import); prefer known_executors().
KNOWN_EXECUTORS: frozenset[str] = known_executors()

# Used when no availability file exists at all: keep the lifecycle usable by a
# supervising session rather than dead. Every real deployment ships the
# committed default, so this is a last resort.
_BOOTSTRAP = {
    "updated": "bootstrap-default",
    "executors": {"manual": {"available": True}},
    "roles": {
        "implementation": {"prefer": ["manual"]},
        "review": {"prefer": ["manual"]},
    },
}


class ModelSelectorError(RuntimeError):
    """The availability/policy configuration cannot be used as written."""


@dataclass(frozen=True)
class Selection:
    """One executor decision, with its reason and the evidence to record."""

    role: str
    status: str  # "SELECTED" | "ABSTAIN"
    executor: str | None
    reason: str
    considered: tuple[str, ...]
    availability_updated: str

    def receipt(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "role": self.role,
            "status": self.status,
            "executor": self.executor,
            "reason": self.reason,
            "considered": list(self.considered),
            "availability_updated": self.availability_updated,
        }


def availability_path() -> Path:
    override = os.environ.get("TGW_MODEL_AVAILABILITY")
    if override:
        return Path(override)
    if _CONFIG_PATH.is_file():
        return _CONFIG_PATH
    return _REPO_DEFAULT


def load_availability(path: Path | None = None) -> dict[str, Any]:
    resolved = path or availability_path()
    if not resolved.is_file():
        return dict(_BOOTSTRAP)
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelSelectorError(f"model-availability file is unreadable: {resolved}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("executors"), dict) or not isinstance(data.get("roles"), dict):
        raise ModelSelectorError(f"model-availability file is malformed (need 'executors' and 'roles' objects): {resolved}")
    known = known_executors()
    for name in data["executors"]:
        if name not in known:
            raise ModelSelectorError(f"model-availability names an unknown executor {name!r} (known: {sorted(known)})")
    return data


def select_executor(role: str, *, availability: dict[str, Any] | None = None) -> Selection:
    """Return the first policy-preferred executor that is available for *role*.

    An explicit ``$TGW_IMPLEMENT_EXECUTOR`` / ``$TGW_REVIEW_EXECUTOR`` pin is a
    deliberate operator override and always wins; if the availability file marks
    that executor unavailable the selection still uses it but the reason says so.
    The same applies to a live dispatch-outcome hold (see ``model_observations``):
    the pin wins, and the reason notes the hold.

    Without a pin, an executor under an active observation hold is skipped just
    like one the availability file marks unavailable, and the hold reason is
    carried in ``Selection.reason``. When every preferred executor is either
    unavailable or held the selection is ABSTAIN with a reason naming both kinds.
    """
    data = availability if availability is not None else load_availability()
    executors = data["executors"]
    updated = str(data.get("updated", "unknown"))

    def _is_available(name: str) -> bool:
        entry = executors.get(name)
        return isinstance(entry, dict) and bool(entry.get("available"))

    def _held_reason(name: str) -> str:
        entry = executors.get(name)
        if not isinstance(entry, dict):
            return "not listed in the availability file"
        return str(entry.get("reason") or "marked unavailable")

    def _observation_hold(name: str) -> str | None:
        """The active dispatch-outcome hold reason for *name*, or None.

        Pure read — ``model_selector`` never writes observations — and never
        raises: an unreadable store simply means no hold (fail open).
        """
        try:
            status = model_observations.recent_status(name)
        except Exception:
            return None
        if not status:
            return None
        return str(status[1])

    known = known_executors()
    pin = os.environ.get({"implementation": "TGW_IMPLEMENT_EXECUTOR", "review": "TGW_REVIEW_EXECUTOR"}.get(role, ""))
    if pin:
        if pin not in known:
            raise ModelSelectorError(f"pinned executor {pin!r} is not a known executor")
        reason = "pinned via env" if _is_available(pin) else f"pinned via env (availability file: {_held_reason(pin)})"
        hold = _observation_hold(pin)
        if hold is not None:
            reason += f" (observation hold: {hold})"
        return Selection(role, "SELECTED", pin, reason, (pin,), updated)

    role_policy = data["roles"].get(role)
    if not isinstance(role_policy, dict) or not isinstance(role_policy.get("prefer"), list) or not role_policy["prefer"]:
        raise ModelSelectorError(f"model-availability has no 'prefer' list for role {role!r}")
    prefer = tuple(str(x) for x in role_policy["prefer"])
    for candidate in prefer:
        if candidate not in known:
            raise ModelSelectorError(f"role {role!r} policy names an unknown executor {candidate!r}")
    held_notes: list[str] = []
    unavailable_notes: list[str] = []
    for candidate in prefer:
        if not _is_available(candidate):
            unavailable_notes.append(f"{candidate} ({_held_reason(candidate)})")
            continue
        hold = _observation_hold(candidate)
        if hold is not None:
            held_notes.append(f"{candidate} (observation hold: {hold})")
            continue
        if held_notes:
            reason = (f"first available in {role} policy {list(prefer)} "
                      f"(skipped held: {'; '.join(held_notes)})")
        else:
            reason = f"first available in {role} policy {list(prefer)}"
        return Selection(role, "SELECTED", candidate, reason, prefer, updated)

    parts: list[str] = []
    if unavailable_notes:
        parts.append("unavailable: " + "; ".join(unavailable_notes))
    if held_notes:
        parts.append("held: " + "; ".join(held_notes))
    detail = "; ".join(parts) if parts else "empty policy"
    return Selection(role, "ABSTAIN", None, f"no executor in the {role} policy is available: {detail}", prefer, updated)

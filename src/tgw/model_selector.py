"""Semi-automatic executor selection for the coding lifecycle.

This is the decision half of the planned model selector (NEXT-EVOLUTION §7-8):
given a role and a map of which executors are currently usable, pick the first
one an ordered per-role policy allows, and explain the choice. The availability
map is operator-maintained JSON for now; when the live availability prober
(Todo 1916) lands it writes the same file on a schedule and nothing here
changes.

No silent fallback: if nothing in the policy is available the selection is
ABSTAIN with a reason, and the caller decides what to do with that.

Availability file (first found wins):
  1. ``$TGW_MODEL_AVAILABILITY``
  2. ``/opt/TGW/tgw-lib/config/model-availability.json``  (operator-editable)
  3. ``<repo>/config/model-availability.json``            (committed default)

Shape::

    {
      "updated": "2026-09-04",
      "executors": {
        "claude":   {"available": true,  "models": ["claude-sonnet-5", ...]},
        "opencode": {"available": false, "reason": "executor backend not wired"},
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

SCHEMA = "tgw-model-selection/v1"

_CONFIG_PATH = Path("/opt/TGW/tgw-lib/config/model-availability.json")
_REPO_DEFAULT = Path(__file__).resolve().parent.parent.parent / "config" / "model-availability.json"

# Executors the coding lifecycle knows how to run. A policy or availability file
# may not name an executor outside this set.
KNOWN_EXECUTORS: frozenset[str] = frozenset(
    {"codex", "claude", "opencode", "manual", "deepseek"}
)

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
    for name in data["executors"]:
        if name not in KNOWN_EXECUTORS:
            raise ModelSelectorError(f"model-availability names an unknown executor {name!r} (known: {sorted(KNOWN_EXECUTORS)})")
    return data


def select_executor(role: str, *, availability: dict[str, Any] | None = None) -> Selection:
    """Return the first policy-preferred executor that is available for *role*.

    An explicit ``$TGW_IMPLEMENT_EXECUTOR`` / ``$TGW_REVIEW_EXECUTOR`` pin is a
    deliberate operator override and always wins; if the availability file marks
    that executor unavailable the selection still uses it but the reason says so.
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

    pin = os.environ.get({"implementation": "TGW_IMPLEMENT_EXECUTOR", "review": "TGW_REVIEW_EXECUTOR"}.get(role, ""))
    if pin:
        if pin not in KNOWN_EXECUTORS:
            raise ModelSelectorError(f"pinned executor {pin!r} is not a known executor")
        reason = "pinned via env" if _is_available(pin) else f"pinned via env (availability file: {_held_reason(pin)})"
        return Selection(role, "SELECTED", pin, reason, (pin,), updated)

    role_policy = data["roles"].get(role)
    if not isinstance(role_policy, dict) or not isinstance(role_policy.get("prefer"), list) or not role_policy["prefer"]:
        raise ModelSelectorError(f"model-availability has no 'prefer' list for role {role!r}")
    prefer = tuple(str(x) for x in role_policy["prefer"])
    for candidate in prefer:
        if candidate not in KNOWN_EXECUTORS:
            raise ModelSelectorError(f"role {role!r} policy names an unknown executor {candidate!r}")
        if _is_available(candidate):
            reason = f"first available in {role} policy {list(prefer)}"
            return Selection(role, "SELECTED", candidate, reason, prefer, updated)

    held = "; ".join(f"{name} ({_held_reason(name)})" for name in prefer)
    return Selection(role, "ABSTAIN", None, f"no executor in the {role} policy is available: {held}", prefer, updated)

"""Dispatch-outcome observations for the model selector — LEAF-11-8 / Todo 1956.

The model selector already falls back to something that works on failure
(``tgw.development.harness_session._dispatch_chain`` reroutes around a
``SessionUnavailable``). This module is the missing second half: *"updates
itself in the background for subsequent turns"*. Every dispatch outcome IS a
probe — no prober daemon — and each one is appended here so the *next*
``select_executor`` call ranks a currently-working executor first.

Observation outcomes (``record``):

* ``available`` — a session ran on the executor and produced a report.
* ``unavailable`` — ``SessionUnavailable``: no credential / quota / auth /
  rate-limit wall. The executor is held out of selection for
  ``UNAVAILABLE_COOLDOWN_S`` (default 60 min).
* ``error`` — the session ran but failed for a non-availability reason.
  The executor is held for ``ERROR_COOLDOWN_S`` (default 15 min).

A later ``available`` observation for the same executor clears any hold
immediately. ``recent_status`` collapses the recent observations for one
executor into ``("held", reason)`` or ``None`` (no active hold).

Durability: append-only JSON-lines at ``OBSERVATIONS_PATH``
(``/opt/TGW/var/log/`` — NOT ``/tmp``, so holds survive a reboot),
overridable with ``$TGW_MODEL_OBSERVATIONS``. The file is self-bounding
(rewritten to the last ``KEEP_LINES`` once it passes ``MAX_LINES``), and both
entry points tolerate a missing/corrupt file — they start fresh / report no
hold and never raise into a dispatch.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "tgw-model-observation/v1"

#: Durable observations path (NOT /tmp — holds must survive a reboot).
#: Overridden by ``$TGW_MODEL_OBSERVATIONS`` (see ``observations_path``).
OBSERVATIONS_PATH = Path("/opt/TGW/var/log/model-observations.jsonl")

ENV_VAR = "TGW_MODEL_OBSERVATIONS"
ENABLED_ENV_VAR = "TGW_MODEL_OBSERVATIONS_ENABLED"

#: Bounding: once the file passes MAX_LINES, rewrite keeping the last
#: KEEP_LINES (= MAX_LINES / 2).
MAX_LINES = 2000
KEEP_LINES = MAX_LINES // 2

#: Cooldown windows: an ``unavailable`` observation holds the executor out of
#: selection for 60 min, an ``error`` for 15 min, unless a later ``available``
#: clears the hold first.
UNAVAILABLE_COOLDOWN_S = 60 * 60
ERROR_COOLDOWN_S = 15 * 60

VALID_OUTCOMES = frozenset({"available", "unavailable", "error"})


def observations_path() -> Path:
    """The JSON-lines file observations are appended to / read from."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    return OBSERVATIONS_PATH


def enabled() -> bool:
    """False when ``$TGW_MODEL_OBSERVATIONS_ENABLED`` is an explicit off word.

    Default ON — dispatch outcomes are recorded unless the operator (or a
    test) opts out. Any other value, including unset, means enabled.
    """
    return os.environ.get(ENABLED_ENV_VAR, "").strip().lower() not in {
        "0", "false", "off", "no", "disabled",
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return _now()
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def record(executor: str, role: str, outcome: str, *, detail: str = "") -> bool:
    """Append one dispatch-outcome observation. Never raises on I/O.

    Returns True when the observation was written, False when recording is
    disabled or the file could not be written (a dispatch must never fail
    because the observation store is unavailable). Raises ``ValueError`` for
    an unknown ``outcome`` — a programmer error the dispatch wrapper still
    swallows, but one tests should see loudly.
    """
    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"unknown model-observation outcome {outcome!r} "
            f"(expected one of {sorted(VALID_OUTCOMES)})"
        )
    if not enabled():
        return False
    line = json.dumps({
        "schema": SCHEMA,
        "observed_at": _now().isoformat(),
        "executor": str(executor),
        "role": str(role),
        "outcome": outcome,
        "detail": str(detail),
    }, sort_keys=True)
    path = observations_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        _bound(path)
    except OSError:
        return False
    return True


def _bound(path: Path) -> None:
    """Keep the observations file bounded; tolerate any I/O problem."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES:
        return
    try:
        path.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def _parse_entry(raw: str) -> dict | None:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("outcome") not in VALID_OUTCOMES:
        return None
    if not isinstance(value.get("executor"), str):
        return None
    try:
        observed = datetime.fromisoformat(str(value.get("observed_at")))
    except (ValueError, TypeError):
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    value["_observed_at"] = observed
    return value


def recent_status(executor: str, *, now: datetime | None = None) -> tuple[str, str] | None:
    """Collapse recent observations for *executor* into a hold, or None.

    Returns ``("held", reason)`` when the most recent observation for the
    executor is ``unavailable``/``error`` and still inside its cooldown
    window; ``None`` when there is no active hold — no observations at all,
    a later ``available`` that clears the hold, or a lapsed cooldown. Never
    raises: a missing/corrupt/unreadable file simply means no hold
    (fail open — garbage must never block a dispatch).
    """
    try:
        return _recent_status_inner(str(executor), _coerce_now(now))
    except Exception:
        return None


def _recent_status_inner(executor: str, moment: datetime) -> tuple[str, str] | None:
    try:
        lines = observations_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    latest: dict | None = None
    for raw in lines:
        entry = _parse_entry(raw)
        if entry is None or entry["executor"] != executor:
            continue
        latest = entry
    if latest is None or latest["outcome"] == "available":
        return None
    cooldown = UNAVAILABLE_COOLDOWN_S if latest["outcome"] == "unavailable" else ERROR_COOLDOWN_S
    age_s = max(0.0, (moment - latest["_observed_at"]).total_seconds())
    if age_s > cooldown:
        return None
    detail = str(latest.get("detail") or "")
    reason = (
        f"{latest['outcome']} {int(age_s)}s ago "
        f"(cooldown {cooldown // 60}m)"
        + (f": {detail[:300]}" if detail else "")
    )
    return ("held", reason)

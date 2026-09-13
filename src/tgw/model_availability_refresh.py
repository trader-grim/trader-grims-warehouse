"""Rewrite ``config/model-availability.json`` from the live model catalogue.

Bounded slice of the "updater/research factory" (LEAF-11-8 / Todo 1956): the
freshness half only. This module owns no policy — it reads the live, ranked,
coding-appropriate model list through ``tgw.model_currency_adapter`` and
updates two things in the existing availability file:

* ``executors.<name>.available`` — for executors that name a provider path
  (a ``models`` / ``models_free`` / ``models_paid_via_zen`` / ``models_go``
  list), true iff the live catalogue reaches that family, else false with a
  dated reason. ``models_go`` (added 2026-09-12) is the quota-priced OpenCode
  Go tier, which rides the same opencode CLI as zen and is matched the same
  open-list way (no closed allowlist -- see ``_closed_ids``).
  Executors with no such list (e.g. ``manual``, the human fallback) have
  nothing for a live catalogue to confirm and are left alone.
* ``roles.<role>.model`` hints — left exactly as written UNLESS the model id
  a slot names has dropped out of the live catalogue, in which case it is
  swapped for the best live candidate for *that slot's own* executor family
  ("minimal auto-migration" — never a catalogue-wide best pick, and never a
  re-optimisation of a still-live hint). This is deliberate: the per-role
  model assignments come from the research role chart (best / least-expensive-
  acceptable per role) and are an operator/cost-policy decision — this job
  keeps them from pointing at a dead model, it does not second-guess which
  model a role should use. The "prefer a new model that is 50% cheaper" rule
  is a later, explicit addition. For a closed-list executor (e.g. ``claude``,
  whose ``executors.claude.models`` is the exact id set the claude-code CLI
  accepts) candidates are restricted to that allowlist; a bare/prefixed id
  mismatch (``opencode/x`` hint vs ``x`` in the live list) is normalised, not
  treated as stale.

Everything else — ``roles[].prefer`` order, ``_comment`` / ``research`` /
``note`` / ``cost_policy`` / ``role_chart_source`` strings, and any executor
or role object carrying ``"freshness": "frozen"`` — is left untouched.
``tgw.model_selector`` reads only ``executors`` and ``roles.<role>.prefer``,
neither of which this job changes in shape, so it needs no changes.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tgw import model_currency_adapter, model_selector

SCHEMA = "tgw-model-availability-refresh/v1"

_RECEIPTS_PATH = Path("/opt/TGW/var/log/model-availability-refresh-receipts.jsonl")


class ModelAvailabilityRefreshError(RuntimeError):
    """The availability file cannot be refreshed as written."""


def _bare_id(model_id: str) -> str:
    """Strip any ``provider/`` prefix a live catalogue id may carry."""
    return model_id.rsplit("/", 1)[-1]


def _closed_ids(executors: dict[str, Any], executor_name: str) -> set[str] | None:
    """The exact-id allowlist for an executor (e.g. ``executors.claude.models``),
    or ``None`` if that executor has no closed list (e.g. ``opencode``, which is
    reached by provider/family match instead)."""
    entry = executors.get(executor_name)
    if not isinstance(entry, dict):
        return None
    ids = entry.get("models")
    return {str(i) for i in ids} if isinstance(ids, list) else None


def _reachable_for(executor_name: str, model: dict[str, Any], closed_ids: set[str] | None) -> bool:
    if closed_ids is not None:
        # A closed-list executor (e.g. claude-code CLI) only accepts the exact
        # ids it was given — a live model merely *named* "claude" elsewhere in
        # the catalogue does not mean this executor can reach it.
        return _bare_id(str(model.get("model_id", ""))) in closed_ids
    needle = executor_name.lower()
    return needle in str(model.get("model_id", "")).lower() or needle == str(model.get("provider", "")).lower()


def _has_provider_path(entry: dict[str, Any]) -> bool:
    return bool(
        entry.get("models")
        or entry.get("models_free")
        or entry.get("models_paid_via_zen")
        or entry.get("models_go")
    )


def _best_candidate(
    slot: str,
    live_models: list[dict[str, Any]],
    executors: dict[str, Any],
) -> str | None:
    """The best live model id that fits *slot*'s executor family — used only to
    replace a hint whose model has gone stale.

    For a closed-list executor (``executors.<name>.models``, e.g. ``claude``)
    only ids on that allowlist are eligible, and the id returned is the bare
    allowlisted form (what the harness/consumer actually expects) rather than
    whatever provider-prefixed id the live source used to report it.
    """
    executor_name = slot.split("_")[0]
    closed = _closed_ids(executors, executor_name)
    for m in live_models:
        if not _reachable_for(executor_name, m, closed):
            continue
        return _bare_id(str(m.get("model_id", ""))) if closed is not None else str(m.get("model_id", ""))
    return None


def _is_live(
    value: str, executor_name: str, live_models: list[dict[str, Any]], live_ids: set[str], executors: dict[str, Any]
) -> bool:
    closed = _closed_ids(executors, executor_name)
    if closed is not None:
        if value not in closed:
            return False
        live_bare = {_bare_id(str(m.get("model_id", ""))) for m in live_models}
        return value in live_bare
    # open-list executor (e.g. opencode): a hint may carry a ``provider/`` prefix
    # the live source omits (or vice versa) — compare on the bare id too so a
    # still-live model is not churned as "stale".
    if value in live_ids:
        return True
    live_bare = {_bare_id(str(m.get("model_id", ""))) for m in live_models}
    return _bare_id(value) in live_bare


def _update_executors(
    executors: dict[str, Any], live_models: list[dict[str, Any]], today: str, changed: list[dict[str, Any]]
) -> None:
    for name, entry in executors.items():
        if not isinstance(entry, dict) or entry.get("freshness") == "frozen" or not _has_provider_path(entry):
            continue
        closed = _closed_ids(executors, name)
        was = bool(entry.get("available"))
        reachable = any(_reachable_for(name, m, closed) for m in live_models)
        if reachable == was:
            continue
        entry["available"] = reachable
        if reachable:
            entry.pop("reason", None)
        else:
            entry["reason"] = f"no live model as of {today}"
        changed.append({
            "kind": "executor", "executor": name, "field": "available",
            "was": was, "now": reachable,
        })


def _migrate_stale_role(
    role_name: str, model_hints: dict[str, Any], live_models: list[dict[str, Any]], live_ids: set[str],
    executors: dict[str, Any], changed: list[dict[str, Any]],
) -> None:
    for slot, value in model_hints.items():
        executor_name = slot.split("_")[0]
        if isinstance(value, str):
            if _is_live(value, executor_name, live_models, live_ids, executors):
                continue
            candidate = _best_candidate(slot, live_models, executors)
            if candidate is None or candidate == value:
                continue
            model_hints[slot] = candidate
            changed.append({
                "role": role_name, "slot": slot, "was": value, "now": candidate,
                "reason": "not in live catalogue",
            })
        elif isinstance(value, list):
            new_list = list(value)
            mutated = False
            for i, item in enumerate(value):
                if not isinstance(item, str) or _is_live(item, executor_name, live_models, live_ids, executors):
                    continue
                candidate = _best_candidate(slot, live_models, executors)
                if candidate is None or candidate == item:
                    continue
                new_list[i] = candidate
                mutated = True
                changed.append({
                    "role": role_name, "slot": slot, "was": item, "now": candidate,
                    "reason": "not in live catalogue",
                })
            if mutated:
                model_hints[slot] = new_list


def _update_roles(
    roles: dict[str, Any], live_models: list[dict[str, Any]], executors: dict[str, Any], changed: list[dict[str, Any]]
) -> None:
    live_ids = {str(m.get("model_id", "")) for m in live_models}
    for role_name, role in roles.items():
        if not isinstance(role, dict) or role.get("freshness") == "frozen":
            continue
        model_hints = role.get("model")
        if not isinstance(model_hints, dict):
            continue
        # Every role — selector-consumed or not — gets the same conservative
        # treatment: migrate a slot only when the model it names is gone.
        _migrate_stale_role(role_name, model_hints, live_models, live_ids, executors, changed)


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".model-availability-refresh-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def refresh(
    path: Path | str | None = None,
    *,
    dry_run: bool = False,
    receipts_path: Path | str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Rewrite *path* (default: the selector's own resolved availability file)
    from the live model catalogue and return a receipt. See module docstring
    for exactly what gets touched.
    """
    resolved_path = Path(path) if path is not None else model_selector.availability_path()
    try:
        data = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelAvailabilityRefreshError(f"model-availability file is unreadable: {resolved_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelAvailabilityRefreshError(f"model-availability file is malformed: {resolved_path}")

    today = datetime.now(timezone.utc).date().isoformat()
    live = model_currency_adapter.live_coding_models(timeout=timeout)
    live_models = live["models"]

    changed: list[dict[str, Any]] = []
    if live_models:
        executors = data.get("executors")
        executors_dict = executors if isinstance(executors, dict) else {}
        if isinstance(executors, dict):
            _update_executors(executors, live_models, today, changed)
        roles = data.get("roles")
        if isinstance(roles, dict):
            _update_roles(roles, live_models, executors_dict, changed)

    data["updated"] = today

    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "path": str(resolved_path),
        "sources_live": live["sources_live"],
        "sources_failed": live["sources_failed"],
        "changed": changed,
        "diff": changed,
        "written": False,
    }

    if not dry_run:
        _atomic_write(resolved_path, data)
        receipt["written"] = True
        receipts_target = Path(receipts_path) if receipts_path is not None else _RECEIPTS_PATH
        receipts_target.parent.mkdir(parents=True, exist_ok=True)
        with receipts_target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, sort_keys=True) + "\n")

    return receipt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tgw-model-availability-refresh")
    parser.add_argument("--dry-run", action="store_true", help="compute the receipt/diff without writing")
    parser.add_argument("--path", default=None, help="override the availability file path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        receipt = refresh(path=args.path, dry_run=args.dry_run)
    except ModelAvailabilityRefreshError as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

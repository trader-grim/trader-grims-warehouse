"""
tgw.context — Current-item context: set/get/clear.

Replaces the legacy tgwset/tgw_sku shell functions in tgw.source.
Primary store: runtime/state/current-item.json  {sku, set_at, set_by}
Compat view:   /opt/TGW/CurrentItem  →  ItemData/<SKU>/
               /opt/TGW/CurrentItem.json  →  ItemData/<SKU>/<SKU>.json
               /opt/TGW/CurrentLocation  →  ItemCatalog/by-location/<location>
All three symlinks are maintained atomically on every set/clear so existing
MC/shell consumers keep working during the tgw.source retirement.
CurrentLocation mirrors the old tgwset() shell function's
`ln -sf $catalogpath/$(tgw_location) $tgwpath/CurrentLocation` behavior
(todo #1324 — restored after being silently dropped by PP-CONTEXT-001).
Only created when the item's JSON has a non-empty `location` field; not
an error if it doesn't (matches the old shell function's silent no-op on
an empty `tgw_location` result).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .config import context_state_path, location_dir, sku_dir, sku_json

_SKU_RE = re.compile(r"^tgw\d{15,}")

# Fixed paths for backward-compat symlinks — not config-driven by design
# so existing shell consumers don't need to know about config.
_COMPAT_CURRENT_ITEM      = Path("/opt/TGW/CurrentItem")
_COMPAT_CURRENT_ITEM_JSON = Path("/opt/TGW/CurrentItem.json")
_COMPAT_CURRENT_LOCATION  = Path("/opt/TGW/CurrentLocation")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_context(
    cfg: Dict[str, Any],
    sku: str,
    set_by: str = "cli",
) -> Dict[str, Any]:
    """Set current-item context to *sku*.  Idempotent: same SKU → success, no write."""
    if not _SKU_RE.match(sku):
        return {"ok": False, "error": f"invalid SKU format: {sku!r}"}

    if not sku_dir(cfg, sku).is_dir():
        return {"ok": False, "error": f"SKU not found: {sku!r}"}

    state_path = context_state_path(cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    existing = _read_state(state_path)
    if existing and existing.get("sku") == sku:
        return {"ok": True, "sku": sku, "set_at": existing.get("set_at"), "set_by": existing.get("set_by"), "changed": False}

    now = datetime.now(timezone.utc).isoformat()
    state: Dict[str, Any] = {"sku": sku, "set_at": now, "set_by": set_by}
    _write_state(state_path, state)
    _update_compat_symlinks(cfg, sku)

    return {"ok": True, "sku": sku, "set_at": now, "set_by": set_by, "changed": True}


def get_context(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return current-item context.  Falls back to legacy symlink if no state file."""
    state = _read_state(context_state_path(cfg))
    if state:
        return {"ok": True, "sku": state.get("sku"), "set_at": state.get("set_at"), "set_by": state.get("set_by")}

    # Legacy fallback: state file absent but symlink may have been set by old tgwset
    sku = _sku_from_symlink()
    if sku:
        return {"ok": True, "sku": sku, "set_at": None, "set_by": "legacy"}

    return {"ok": True, "sku": None, "set_at": None, "set_by": None}


def clear_context(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Clear current-item context.  Idempotent: already-clear → success."""
    changed = False

    state_path = context_state_path(cfg)
    if state_path.exists():
        state_path.unlink()
        changed = True

    for link in (_COMPAT_CURRENT_ITEM, _COMPAT_CURRENT_ITEM_JSON, _COMPAT_CURRENT_LOCATION):
        if link.is_symlink():
            link.unlink()
            changed = True

    return {"ok": True, "changed": changed}


def current_sku(cfg: Dict[str, Any]) -> Optional[str]:
    """Return the current SKU or None — used internally by commands that accept an optional SKU."""
    result = get_context(cfg)
    return result.get("sku") or None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_state(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and _SKU_RE.match(str(data.get("sku", ""))):
            return data
    except Exception:
        pass
    return None


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    """Write state atomically via temp file + rename."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _update_compat_symlinks(cfg: Dict[str, Any], sku: str) -> None:
    """Atomically update /opt/TGW/CurrentItem, CurrentItem.json, and CurrentLocation."""
    target_dir  = sku_dir(cfg, sku)
    target_json = sku_json(cfg, sku)

    _atomic_symlink(target_dir, _COMPAT_CURRENT_ITEM)
    if target_json.exists():
        _atomic_symlink(target_json, _COMPAT_CURRENT_ITEM_JSON)

    _update_current_location_symlink(cfg, target_json)


def _update_current_location_symlink(cfg: Dict[str, Any], item_json_path: Path) -> None:
    """Restore CurrentLocation → ItemCatalog/by-location/<location> (todo #1324).

    Mirrors the old tgwset() shell function's
    `ln -sf $catalogpath/$(tgw_location) $tgwpath/CurrentLocation`, where
    tgw_location read the item JSON's .location field. If the item has no
    location set, or the location's catalog directory doesn't exist (e.g.
    catalog rebuild hasn't run yet), silently skip — matches the old
    shell function's silent no-op, not an error condition.
    """
    location = _item_location(item_json_path)
    if not location:
        return

    try:
        target = location_dir(cfg, location)
    except (ValueError, KeyError):
        return

    if not target.is_dir():
        return

    _atomic_symlink(target, _COMPAT_CURRENT_LOCATION)


def _item_location(item_json_path: Path) -> Optional[str]:
    """Read the .location field out of an item JSON file, or None."""
    if not item_json_path.exists():
        return None
    try:
        data = json.loads(item_json_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    location = data.get("location")
    if location is None:
        return None
    location = str(location).strip()
    return location or None


def _atomic_symlink(target: Path, link: Path) -> None:
    """Create or replace *link* → *target* atomically (temp + os.replace)."""
    tmp = link.parent / (link.name + ".symtmp")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, link)


def _sku_from_symlink() -> Optional[str]:
    """Resolve /opt/TGW/CurrentItem symlink → SKU name, or None."""
    if _COMPAT_CURRENT_ITEM.is_symlink():
        try:
            target = _COMPAT_CURRENT_ITEM.resolve()
            if target.is_dir() and _SKU_RE.match(target.name):
                return target.name
        except OSError:
            pass
    return None

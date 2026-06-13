"""
tgw.api — CLI entry point.

This module is intentionally thin.  It parses arguments, calls the
appropriate function from tgw.items, tgw.catalog, or tgw.resolver,
and prints the result as JSON.

No business logic lives here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .alt_text import cmd_alt_text, cmd_alt_text_batch
from .catalog import (
    build_all_catalogs,
    build_full_catalog,
    build_full_catalog_csv,
    build_location_tree,
    build_search_catalog,
    build_search_catalog_csv,
    load_full_catalog,
    load_search_catalog,
)
from .config import DEFAULT_CONFIG, load_config
from .context import clear_context, get_context, set_context
from .health import check_all
from .items import (
    catlocmvall,
    get_item,
    locationupdate,
    statusupdate,
    titleupdate,
    update_item,
    update_where,
    verifiedupdate,
)
from .resolver import resolve, sku_date_str
from .scrub import data_scrub_pass1, data_scrub_size_class_backfill
from .sqlite_catalog import build_sqlite_catalog
from .thumbnail import build_thumbnail_cache

# ---------------------------------------------------------------------------
# list_items — lives here because it bridges catalog and resolver
# ---------------------------------------------------------------------------


def list_items(
    cfg: Dict[str, Any],
    search: str = "",
    location: str = "",
    status: str = "",
    limit: Optional[int] = None,
    date_from: str = "",
    date_to: str = "",
    search_field: Optional[str] = None,
    empty_field: Optional[str] = None,
) -> Dict[str, Any]:
    """List items matching filters.  Always returns {'ok': True, 'items': [...]}."""
    # Load from best available source
    if cfg["search_catalog_path"].exists():
        rows = load_search_catalog(cfg)
    elif cfg["full_catalog_path"].exists():
        rows = load_full_catalog(cfg)
    else:
        from .resolver import find_item_jsons, load_item_doc

        rows = [load_item_doc(p) for p in find_item_jsons(cfg)]

    out: List[Dict[str, Any]] = []
    for item in rows:
        if search:
            if search_field:
                val = item.get(search_field)
                if val is None or search.lower() not in str(val).lower():
                    continue
            elif search.lower() not in "\n".join(f"{k}={v}" for k, v in item.items() if isinstance(v, (str, int, float, bool)) or v is None).lower():
                continue
        if location and str(item.get("location", "")) != location:
            continue
        if status and str(item.get("#STATUS", item.get("status", ""))) != status:
            continue
        if date_from or date_to:
            sku = str(item.get("sku", ""))
            d = sku_date_str(sku)
            if d is None:
                continue
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
        if empty_field:
            val = item.get(empty_field)
            if not (val is None or (isinstance(val, str) and not val.strip())):
                continue
        out.append(item)
        if limit not in (None, 0) and len(out) >= int(limit):
            break
    return {"ok": True, "count": len(out), "items": out}


def _item_ebay_id(item: Dict[str, Any]) -> str:
    """Best eBay identifier for an item: pipeline listing_id, else legacy Item number."""
    lid = item.get("ebay_listing", {}).get("listing_id") if isinstance(item.get("ebay_listing"), dict) else None
    return str(lid or item.get("Item number") or "").strip()


def cmd_picklist(
    cfg: Dict[str, Any],
    *,
    status: str = "",
    location: str = "",
    search: str = "",
    pdf: bool = False,
    output: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Location-sorted picking list (PP-FULFILLMENT-001).

    Emits a plain-text list of items grouped and sorted by warehouse location
    (unlocated items last), each line: SKU, title, eBay id. Reads via the
    token-free list_items() helper — no eBay API call.

    With --pdf: generates a PDF (checkboxes + QR per row) at --output path or
    a temp file, then auto-sends to CUPS if config key ``print_cups_queue`` is set.
    """
    listed = list_items(cfg, search=search, location=location, status=status)
    rows: List[Dict[str, str]] = []
    for item in listed["items"]:
        rows.append(
            {
                "location": str(item.get("location", "") or "").strip(),
                "sku": str(item.get("sku", "")),
                "title": str(item.get("title", "") or "").strip(),
                "ebay_id": _item_ebay_id(item),
            }
        )
    # Sort by location (unlocated last), then SKU.
    rows.sort(key=lambda r: (r["location"] == "", r["location"], r["sku"]))

    lines: List[str] = []
    current = None
    for r in rows:
        loc = r["location"] or "(unlocated)"
        if loc != current:
            lines.append("")
            lines.append(f"== {loc} ==")
            current = loc
        eid = f"  [{r['ebay_id']}]" if r["ebay_id"] else ""
        lines.append(f"  {r['sku']}  {r['title'][:60]}{eid}")
    print("\n".join(lines).strip())

    n_locs = len({r["location"] for r in rows})
    result: Dict[str, Any] = {"ok": True, "count": len(rows), "locations": n_locs, "picklist": rows}

    if pdf:
        from .printing import _default_picklist_path, build_picklist_pdf, cups_print

        out_path = Path(output) if output else _default_picklist_path()
        try:
            build_picklist_pdf(rows, out_path)
            result["pdf"] = str(out_path)
            queue = cfg.get("print_cups_queue", "")
            if queue:
                ok = cups_print(out_path, queue)
                result["cups_sent"] = ok
                result["cups_queue"] = queue
        except ImportError as exc:
            result["pdf_error"] = f"printing deps missing: {exc}"

    return result


def cmd_print_label(
    cfg: Dict[str, Any],
    sku: str,
    *,
    output: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a 2.25"×1.25" Code128 SKU label PDF (PP-FULFILLMENT-001 Phase 1).

    Writes to --output path or /tmp/tgw-label-<sku>.pdf.
    If config key ``print_cups_queue`` is set, also sends to CUPS (stub until
    hardware arrives).
    """
    from .printing import _default_label_path, build_label_pdf, cups_print

    try:
        item = get_item(cfg, sku)
    except FileNotFoundError:
        return {"ok": False, "error": f"SKU not found: {sku}"}

    item_title = str(item.get("title") or "").strip()
    location = str(item.get("location") or "").strip()

    out_path = Path(output) if output else _default_label_path(sku)

    try:
        build_label_pdf(sku, item_title, location, out_path)
    except ImportError as exc:
        return {"ok": False, "error": f"printing deps missing: {exc}"}

    result: Dict[str, Any] = {
        "ok": True,
        "sku": sku,
        "pdf": str(out_path),
        "cups_sent": False,
    }

    queue = cfg.get("print_cups_queue", "")
    if queue:
        ok = cups_print(out_path, queue)
        result["cups_sent"] = ok
        result["cups_queue"] = queue

    return result


_ENQUEUE_QUEUES = {
    "ai_identify",
    "ebay_draft",
    "ebay_price",
    "ebay_stage",
    "ebay_upload",
    "ebay_publish",
    "ebay_sync",
    "catalog_rebuild",
    "thumbnail_gen",
}


def _expand_skus(skus: List[str]) -> List[str]:
    """Expand '-' in a SKU list by reading one SKU per line from stdin."""

    out: List[str] = []
    for s in skus:
        if s == "-":
            for line in sys.stdin:
                line = line.strip()
                if line:
                    out.append(line)
        else:
            out.append(s)
    return out


def cmd_enqueue_sku(cfg: Dict[str, Any], sku: str, queue: str) -> Dict[str, Any]:
    """
    Enqueue a pipeline action for one SKU (PP-WM-001 — the CLI sibling of the
    MCP tgw_enqueue tool; the Qtile command chord calls this).
    """
    import psycopg2.errors

    from .config import sku_json
    from .queue import state_machine

    if queue not in _ENQUEUE_QUEUES:
        return {"ok": False, "error": f"invalid queue {queue!r}; valid: {sorted(_ENQUEUE_QUEUES)}"}

    if not sku_json(cfg, sku).exists():
        return {"ok": False, "error": f"item not found: {sku}"}

    state_machine.init(cfg["postgres_dsn"])
    try:
        jid = state_machine.enqueue_job(
            queue_name=queue,
            payload={"sku": sku},
            entity_type="item",
            entity_id=sku,
            operation="run",
            dedupe_key=f"{queue}:{sku}",
            max_attempts=3,
        )
        return {"ok": True, "job_id": jid, "queue": queue, "sku": sku}
    except psycopg2.errors.UniqueViolation:
        return {"ok": True, "note": "job already queued", "queue": queue, "sku": sku}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_restart_workers(queues: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, Any]:
    """
    Restart tgw-worker@<queue>.service systemd units (PP-SHELL-001 convenience).

    With no queues, restarts every worker in the canonical ``WORKER_QUEUES``
    list.  systemd unit management needs root, so when not running as root this
    transparently uses non-interactive ``sudo -n``; if passwordless sudo is not
    available it prints the exact command for the operator instead of hanging on
    a password prompt.  Always run after editing worker source (see CLAUDE.md).
    """
    import subprocess

    from .queue import WORKER_QUEUES

    if queues:
        unknown = [q for q in queues if q not in WORKER_QUEUES]
        if unknown:
            return {"ok": False, "error": f"unknown queue(s): {unknown}; valid: {list(WORKER_QUEUES)}"}
        targets = list(queues)
    else:
        targets = list(WORKER_QUEUES)

    units = [f"tgw-worker@{q}.service" for q in targets]

    is_root = os.geteuid() == 0
    prefix: List[str] = [] if is_root else ["sudo", "-n"]
    cmd = [*prefix, "systemctl", "restart", *units]
    cmd_str = " ".join(cmd)

    if dry_run:
        print(cmd_str)
        return {"ok": True, "dry_run": True, "queues": targets, "command": cmd_str}

    # Pre-flight: if we need sudo, make sure passwordless sudo works so we don't
    # block on an interactive password prompt inside an automated session.
    if not is_root:
        probe = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
        if probe.returncode != 0:
            return {
                "ok": False,
                "queues": targets,
                "command": f"sudo systemctl restart {' '.join(units)}",
                "error": "not root and passwordless sudo unavailable — run the printed command manually as root",
            }

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return {"ok": False, "error": "systemctl not found on this host", "command": cmd_str}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "systemctl restart timed out", "command": cmd_str}

    # Report per-unit liveness regardless of the restart return code.
    status_cmd = [*prefix, "systemctl", "is-active", *units]
    sproc = subprocess.run(status_cmd, capture_output=True, text=True)
    states = sproc.stdout.split()
    restarted = [u for u, s in zip(units, states) if s == "active"]
    failed = [u for u, s in zip(units, states) if s != "active"]

    return {
        "ok": proc.returncode == 0 and not failed,
        "used_sudo": not is_root,
        "restarted": restarted,
        "failed": failed,
        "command": cmd_str,
        "stderr": proc.stderr.strip() or None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class _GroupedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Suppress the flat {get,list,...} listing; description shows the grouped view."""
    def _format_action(self, action: argparse.Action) -> str:
        if isinstance(action, argparse._SubParsersAction):
            return ""
        return super()._format_action(action)


_HELP_GROUPS: list[tuple[str, list[str]]] = [
    ("Read / Search", [
        "get", "list", "search", "resolve", "quality", "hint-trail",
        "reprice-suggest", "staged", "velocity-report", "seo-audit", "locate",
    ]),
    ("Write / Update", [
        "update", "update-where", "update-title", "update-location", "update-verified",
        "update-status", "set-shipping", "bulk", "price-freeship", "hint",
        "data-scrub", "revise", "alt-text",
    ]),
    ("Context / Intake", [
        "set-context", "get-context", "clear-context", "set-template", "create-item",
    ]),
    ("Pipeline", [
        "enqueue-sku", "requeue-identify", "resolve-legacy", "ready", "publish",
        "alt-text-batch",
    ]),
    ("eBay", [
        "ebay-pull", "ebay-sweep", "import-sold-csv", "sku-migrate",
        "setup-ebay-hooks", "build-archive-index", "history-index",
        "strikethrough-check", "store-categories", "store-category", "get-ebay-token",
    ]),
    ("Catalog / Build", [
        "build-full", "build-search", "build-locations", "build-full-csv",
        "build-search-csv", "build-sqlite", "build-thumbnails", "build-all",
        "ensure-catalog", "lookup", "build-fingerprints", "export-catalog",
        "category-groups", "catalog-verify",
    ]),
    ("Ops / Admin", [
        "health", "serve", "restart-workers", "restart-ebay-token",
        "dead-letter", "queue-history", "todo", "plan", "ai-usage", "report",
        "admin-file", "classify-suggestions", "picklist", "print-label", "mvitems",
        "suggest", "quiet-check", "perp-run", "whisper-suggest",
        "claude-help", "clip", "suggest-edit", "promo",
    ]),
]


def _make_grouped_description(sub: argparse.Action) -> str:
    # _choices_actions is a CPython implementation detail of _SubParsersAction that
    # carries help strings not exposed via the public `sub.choices` dict.  Fall back
    # to an empty dict if the attribute ever disappears — help text becomes blank but
    # nothing crashes.
    help_map: dict[str, str] = {
        a.dest: (a.help or "")
        for a in getattr(sub, "_choices_actions", [])
    }
    lines: list[str] = ["TGW inventory management — subcommands by group:\n"]
    for group_name, commands in _HELP_GROUPS:
        lines.append(f"  {group_name}:")
        for cmd in commands:
            h = help_map.get(cmd, "")
            lines.append(f"    {cmd:<22} {h}")
        lines.append("")
    lines.append("Use 'tgw COMMAND --help' for command-specific options.")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgw",
        description="TGW inventory management API",
        formatter_class=_GroupedHelpFormatter,
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config JSON (default: %(default)s)")
    sub = parser.add_subparsers(dest="op", required=True, metavar="COMMAND")

    # --- read ---
    p = sub.add_parser("get", help="get full item record by SKU")
    p.add_argument("sku")

    p = sub.add_parser("list", help="list items with optional filters")
    p.add_argument("--search", default="")
    p.add_argument("--search-field", default=None, dest="search_field", metavar="KEY", help="restrict --search to this field only (e.g. title, location, #STATUS)")
    p.add_argument("--location", default="")
    p.add_argument("--status", default="")
    p.add_argument("--date-from", default="", dest="date_from", help="YYYYMMDD lower bound on SKU timestamp")
    p.add_argument("--date-to", default="", dest="date_to", help="YYYYMMDD upper bound on SKU timestamp")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--skus-only", action="store_true", dest="skus_only", help="output one SKU per line (pipe-friendly)")

    p = sub.add_parser("search", help="search items by text (shorthand for list --search TEXT)")
    p.add_argument("text", nargs="?", default="", help="search text")
    p.add_argument("--location", default="")
    p.add_argument("--status", default="")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--skus-only", action="store_true", dest="skus_only")
    p.add_argument("--empty", default=None, dest="empty_field", metavar="FIELD", help="return only items where FIELD is missing/null/empty-string")

    p = sub.add_parser("resolve", help="resolve identifiers to a set of SKUs")
    p.add_argument("--sku", default=None)
    p.add_argument("--location", default=None)
    p.add_argument("--status", default=None)
    p.add_argument("--date-from", default=None, dest="date_from")
    p.add_argument("--date-to", default=None, dest="date_to")
    p.add_argument("--ebay-item-id", default=None, dest="ebay_item_id")
    p.add_argument("--upc", default=None)
    p.add_argument("--search", default=None)
    p.add_argument("--skus-only", action="store_true", dest="skus_only", help="output one SKU per line (pipe-friendly)")

    # --- write ---
    p = sub.add_parser("update", help="update one field on one item")
    p.add_argument("sku")
    p.add_argument("field")
    p.add_argument("value")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("update-where", help="bulk-update items matching selectors")
    p.add_argument("field")
    p.add_argument("value")
    p.add_argument("--location", default=None)
    p.add_argument("--status", default=None)
    p.add_argument("--date-from", default=None, dest="date_from")
    p.add_argument("--date-to", default=None, dest="date_to")
    p.add_argument("--search", default=None)
    p.add_argument("--check-only", action="store_true")

    # --- tgw.source replacements (canonical hyphenated names; concatenated forms are aliases) ---
    for _name in ("update-title", "titleupdate"):
        p = sub.add_parser(_name, help="update title field on one item" + (" (deprecated alias)" if _name == "titleupdate" else ""))
        p.add_argument("sku")
        p.add_argument("value")
        p.add_argument("--check-only", action="store_true")

    for _name in ("update-location", "locationupdate"):
        p = sub.add_parser(_name, help="update location and rebuild tree link" + (" (deprecated alias)" if _name == "locationupdate" else ""))
        p.add_argument("sku")
        p.add_argument("location")
        p.add_argument("--check-only", action="store_true")

    for _name in ("update-verified", "verifiedupdate"):
        p = sub.add_parser(_name, help="update VERIFIED field" + (" (deprecated alias)" if _name == "verifiedupdate" else ""))
        p.add_argument("sku")
        p.add_argument("value")
        p.add_argument("--check-only", action="store_true")

    # update-status VALUE SKU... — value-first is intentional: "set all these items to this status"
    for _name in ("update-status", "statusupdate"):
        p = sub.add_parser(_name, help="update #STATUS field on one or more items" + (" (deprecated alias)" if _name == "statusupdate" else ""))
        p.add_argument("value", help='new status value (e.g. "In Stock", "Sold")')
        p.add_argument("skus", nargs="+", help="one or more SKUs to update")
        p.add_argument("--check-only", action="store_true", help="validate without writing")

    for _name in ("set-shipping", "setshipping"):
        p = sub.add_parser(_name, help="set per-item shipping_profile override (PP-HINT-001)" + (" (deprecated alias)" if _name == "setshipping" else ""))
        p.add_argument("sku")
        p.add_argument("value", help="profile name (mapped via fulfillment_policy_by_profile) or a raw fulfillment policy id")
        p.add_argument("--check-only", action="store_true", help="validate without writing")

    p = sub.add_parser("picklist", help="location-sorted picking list (PP-FULFILLMENT-001)")
    p.add_argument("--status", default="", help="filter by status")
    p.add_argument("--location", default="", help="filter by location")
    p.add_argument("--search", default="", help="text filter across fields")
    p.add_argument("--pdf", action="store_true", help="generate PDF with checkboxes and QR codes (PP-ADD-009)")
    p.add_argument("--output", default=None, metavar="PATH", help="output PDF path (default: /tmp/tgw-picklist-<ts>.pdf)")

    p = sub.add_parser("print-label", help="generate Code128 SKU label PDF (PP-FULFILLMENT-001 Phase 1)")
    p.add_argument("sku", help="item SKU")
    p.add_argument("--output", default=None, metavar="PATH", help="output PDF path (default: /tmp/tgw-label-<sku>.pdf)")

    p = sub.add_parser("enqueue-sku", help="enqueue a pipeline action for one or more SKUs (PP-WM-001)")
    p.add_argument("queue", help="target queue (ai_identify, ebay_draft, ebay_price, ...)")
    p.add_argument("skus", nargs="+", help="one or more SKUs, or - to read from stdin")

    p = sub.add_parser("quiet-check", help="when the pipeline is idle, surface pending suggestions/TODOs (PP-CAPTURE-001)")
    p.add_argument("--notify", action="store_true", help="also send a desktop/webhook notification when idle")
    p.add_argument("--kdc", action="store_true", help="push summary to phone via KDE Connect when idle (uses kdeconnect_device_id from config)")

    p = sub.add_parser("perp-run", help="load a Perplexity research brief prompt to clipboard (PP-PERP-AUTO-001)")
    p.add_argument("brief_id", nargs="?", help="brief id or substring (e.g. PERPLEXITY-001); omit to list")
    p.add_argument("--list", dest="list_briefs", action="store_true", help="list available briefs")

    for _name in ("whisper-suggest", "whispertosuggest"):
        p = sub.add_parser(_name, help="transcribe a WAV via whisper-cli and file it as a suggestion (PP-WHISPER-001)" + (" (deprecated alias)" if _name == "whispertosuggest" else ""))
        p.add_argument("wavfile", help="path to an audio file")
        p.add_argument("--model", default=None, help="path to ggml whisper model (default from config)")

    p = sub.add_parser("claude-help", help="launch a Claude troubleshooting session with TGW context (PP-CLAUDE-HELP-001)")
    p.add_argument("issue", nargs="?", default="", help="describe the problem (optional)")
    p.add_argument("--worker", default="", help="focus on a specific worker")
    p.add_argument("--launch", action="store_true", help="exec claude now (default: print the command)")

    p = sub.add_parser("clip", help="TGW clipboard history store/query (PP-CLIP-001)")
    p.add_argument("clip_action", choices=["list", "last-sku", "search", "wipe"])
    p.add_argument("pattern", nargs="?", default="", help="search pattern (for search)")
    p.add_argument("--limit", type=int, default=20, help="max rows (list/search)")
    p.add_argument("--sku-only", action="store_true", help="list: SKU clips only")

    p = sub.add_parser("catlocmvall", help="(deprecated) move all items from one location to another — use mvitems")
    p.add_argument("from_location")
    p.add_argument("to_location")
    p.add_argument("--check-only", action="store_true")

    # --- catalog builds ---
    p = sub.add_parser("build-full", help="build full catalog JSON from ItemData")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("build-search", help="build search catalog JSON")
    p.add_argument("--source", choices=["auto", "full_catalog", "itemdata"], default="auto")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("build-locations", help="build location symlink tree")
    p.add_argument("--source", choices=["auto", "search_catalog", "full_catalog", "itemdata"], default="auto")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("build-full-csv", help="build full catalog CSV")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("build-search-csv", help="build search catalog CSV")
    p.add_argument("--source", choices=["auto", "full_catalog", "itemdata"], default="auto")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("build-sqlite", help="build SQLite catalog from ItemData")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("build-thumbnails", help="generate per-SKU thumbnail cache (requires Pillow)")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("build-all", help="build full catalog, search catalog, location tree, and SQLite catalog")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("ensure-catalog", help="build search catalog only if missing")
    p.add_argument("--check-only", action="store_true")

    p = sub.add_parser("health", aliases=["status"], help="run platform health checks")
    p.add_argument("--no-ollama", action="store_true", help="skip Ollama check")
    p.add_argument("--no-ebay", action="store_true", help="skip eBay token check")

    sub.add_parser("help", help="show this help message and exit")

    p = sub.add_parser("lookup", help="run product enrichment lookup for one item (PP-LOOKUP-001)")
    p.add_argument("sku", help="SKU to look up")
    p.add_argument("--force", action="store_true", help="ignore cache and re-fetch even if fresh result exists")
    p.add_argument("--save", action="store_true", help="write result back to item JSON")

    p = sub.add_parser("quality", help="show listing quality score for one or more items (PP-QUALITY-001)")
    p.add_argument("skus", nargs="+", help="SKU(s) to score")
    p.add_argument("--save", action="store_true", help="write updated quality score back to draft_listing in item JSON")

    p = sub.add_parser("suggest", help="append a suggestion for the next planning session")
    p.add_argument("text", nargs="+", help="suggestion text")

    p = sub.add_parser("note", help="alias for suggest — capture a mid-session note or idea")
    p.add_argument("text", nargs="+", help="note text")

    p = sub.add_parser("btw", help="alias for suggest — quick back-channel capture")
    p.add_argument("text", nargs="+", help="note text")

    p = sub.add_parser("hint", help="set an ai_hint on an item and re-queue identification")
    p.add_argument("sku", help="SKU to hint")
    p.add_argument("text", nargs="+", help='hint text (e.g. "thimbles" or "mini liquor bottles")')
    p.add_argument("--force", action="store_true", help="re-identify even if already ai_identified")

    p = sub.add_parser("hint-trail", help="show identification history for an item")
    p.add_argument("sku", help="SKU to inspect")

    for _name in ("requeue-identify", "requeue"):
        p = sub.add_parser(_name, help="bulk-enqueue ai_identify for items matching a filter" + (" (deprecated alias)" if _name == "requeue" else ""))
        p.add_argument("--no-title", action="store_true", help="items with photos but title still equals SKU (truly unprocessed)")
        p.add_argument("--unidentified", action="store_true", help="all items where ai_identified is not True")
        p.add_argument("--hint-set", action="store_true", help="items with ai_hint set but not yet ai_identified")
        p.add_argument("--no-draft", action="store_true", help="items that are ai_identified but have no draft_listing")
        p.add_argument("--no-price", action="store_true", help="items with draft_listing but no price set")
        p.add_argument("--catalog-only", action="store_true", help="identify for catalog only — skip ebay_draft cascade")
        p.add_argument("--limit", type=int, default=100, help="max items to queue (default: 100; use 0 for unlimited)")
        p.add_argument("--run", action="store_true", help="actually queue jobs (default is dry-run)")

    p = sub.add_parser("resolve-legacy", help="mark item(s) as having legacy eBay listing cleared, enabling ebay_stage to proceed")
    p.add_argument("skus", nargs="+", help="one or more SKUs to resolve")
    p.add_argument("--no-stage", action="store_true", help="mark resolved but do not enqueue ebay_stage")

    p = sub.add_parser("staged", help="list items staged as UNPUBLISHED eBay offers, awaiting review")
    p.add_argument("--json", action="store_true", dest="as_json", help="output as JSON instead of a table")

    p = sub.add_parser("publish", help="approve and publish one or more staged items now (List-Now bypass of the ready dole-out)")
    p.add_argument("skus", nargs="+", help="one or more SKUs to publish")
    p.add_argument("--dry-run", action="store_true", help="show what would be enqueued without actually doing it")

    p = sub.add_parser("ready", help="ready-state dole-out queue: review done-state, listed at a rate limit by ebay_dole (PP-EDITOR-001)")
    p.add_argument("ready_op", nargs="?", default="list", choices=["list", "set", "unset"], help="list the ready pool (default), or set/unset ready on SKUs")
    p.add_argument("skus", nargs="*", help="SKU(s) for set/unset ('-' reads one per line from stdin)")

    p = sub.add_parser("setup-ebay-hooks", help="register eBay push notification delivery URL (run once)")
    p.add_argument("--url", required=True, help="public HTTPS URL eBay will POST to, e.g. https://hooks.example.com/webhooks/ebay/notification")
    p.add_argument("--check", action="store_true", help="print currently registered URL without making changes")

    p = sub.add_parser("serve", help="start tgw-http FastAPI service on port 7373")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=7373, help="bind port (default: 7373)")
    p.add_argument("--reload", action="store_true", help="enable auto-reload (dev only)")

    p = sub.add_parser("ebay-sweep", help="generate physical inventory checklist for ambiguous-status items")
    p.add_argument("--groups", default="A", help="comma-separated groups to include: A=active/unclear, B=out-of-stock/no-listing, C=no-status/no-listing (default: A)")
    p.add_argument("--location", default=None, help="filter to a specific shelf location")
    p.add_argument("--limit", type=int, default=0, help="max items per group (0 = unlimited)")
    p.add_argument("--output", default=None, help="write markdown checklist to this file instead of stdout")

    p = sub.add_parser("bulk", help="bulk-edit one field across matched items (PP-BULKEDIT-001); dry-run unless --apply")
    p.add_argument("--field", required=True, choices=["title", "location", "status", "ai_hint", "shipping_profile"], help="which field to set on every matched item")
    p.add_argument("--value", required=True, help="new value for the field")
    p.add_argument("skus", nargs="*", help="specific SKU(s); or use the filters below")
    p.add_argument("--location", default="", help="filter to a shelf location")
    p.add_argument("--status", default="", help="filter by #STATUS value")
    p.add_argument("--search", default="", help="free-text substring filter")
    p.add_argument("--limit", type=int, default=0, help="cap matched items (0 = all)")
    p.add_argument("--apply", action="store_true", help="actually write changes (default: dry-run preview)")

    p = sub.add_parser("reprice-suggest", help="read-only price suggestions from market data (PP-REPRICER-001); never writes to eBay")
    p.add_argument("skus", nargs="*", help="specific SKU(s); omit to use filters")
    p.add_argument("--location", default="", help="filter to a shelf location")
    p.add_argument("--status", default="", help="filter by #STATUS value")
    p.add_argument("--search", default="", help="free-text substring filter")
    p.add_argument("--limit", type=int, default=0, help="max items (0 = all matched)")
    p.add_argument("--json", dest="as_json", action="store_true", help="output full JSON instead of the table")

    p = sub.add_parser("price-freeship", help="compute free-shipping price (item_price + shipping_cost → nearest .99); use --apply to write it (PP-FREESHIP-001)")
    p.add_argument("sku", help="SKU of the item")
    p.add_argument("--shipping-cost", type=float, default=None, metavar="DOLLARS",
                   help="shipping cost to absorb (overrides item.shipping_cost and config default_shipping_cost)")
    p.add_argument("--apply", action="store_true",
                   help="write the combined price to the item and set free_shipping=true")

    p = sub.add_parser("seo-audit", help="SEO quality report for live and staged listings (PP-SEO-001)")
    p.add_argument("--limit", type=int, default=50, help="max items to show (default 50, worst first)")
    p.add_argument("--live-only", action="store_true", help="only show items with Active eBay listings")

    p = sub.add_parser("build-archive-index", help="scan ItemArchive zips → build eBay-ID lookup cache (run once)")
    p.add_argument("--archive-dir", default="/opt/TGW/data/history/ItemArchive", help="path to ItemArchive directory")
    p.add_argument("--cache", default="/opt/TGW/var/archive-ebay-index.json", help="output cache file path")

    p = sub.add_parser("history-index", help="index ItemArchive zips without eBay IDs + loose CSVs (GEMINI-007 / PP-HISTORY-001)")
    p.add_argument("--target", choices=["ItemArchive", "loose-csv", "all"], default="all",
                   help="what to index: ItemArchive (no-eBay zips), loose-csv (eBay order CSVs), or all (default)")
    p.add_argument("--dry-run", action="store_true", help="count and report without writing output files")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="stop after N new records (0 = no limit; useful for testing)")

    p = sub.add_parser("import-sold-csv", help="import eBay Seller Hub sold-orders CSV → mark items sold")
    p.add_argument("file", help="path to eBay sold-orders CSV file")
    p.add_argument("--dry-run", action="store_true", help="show what would be marked without writing")
    p.add_argument("--show-columns", action="store_true", help="print CSV column names and exit (for format inspection)")
    p.add_argument("--fuzzy", action="store_true", help="second pass: match unresolved rows by title similarity")
    p.add_argument("--fuzzy-threshold", type=float, default=0.80, metavar="N", help="Jaccard similarity threshold for title match (default 0.80)")

    p = sub.add_parser("ebay-pull", help="on-demand eBay data pull: active listings + sold orders → ItemData")
    p.add_argument("--no-active", action="store_true", help="skip active listing sync")
    p.add_argument("--no-sold", action="store_true", help="skip sold orders sync")
    p.add_argument("--dry-run", action="store_true", help="show what would change without writing")

    p = sub.add_parser("sku-migrate", help="SKU normalization (PP-ADD-005)")
    p.add_argument("--check-collisions", action="store_true", help="run collision check only — no changes")
    p.add_argument("--class", dest="classes", default="A,B,C,D,E,F", help="comma-separated class list to process (default: all)")
    p.add_argument("--dry-run", action="store_true", default=True, help="show planned renames without making changes (default)")
    p.add_argument("--run", action="store_true", help="actually execute renames (overrides --dry-run)")
    p.add_argument("--include-live-ebay", action="store_true", help="include items with live eBay listings (default: skip)")
    p.add_argument("--limit", type=int, default=0, help="max items to process (0 = unlimited)")
    p.add_argument("--manifest", default="", help="path for rollback manifest JSON (default: var/log/sku-migrate-<ts>.json)")

    p = sub.add_parser("velocity-report", help="sold velocity analytics by eBay category (PP-PRICE-004)")
    p.add_argument("--category", default=None, metavar="CAT_ID", help="show stats for one category ID only")
    p.add_argument("--refresh", action="store_true", help="recompute stats from ItemData before displaying")
    p.add_argument("--json", action="store_true", dest="json_out", help="output raw JSON instead of formatted table")
    p.add_argument("--output", "-o", default=None, help="write report to file instead of stdout")
    p.add_argument("--min-sold", type=int, default=1, metavar="N", help="hide categories with fewer than N sold items (default: 1)")

    sub.add_parser("store-categories", help="list eBay store custom categories via GetStore (PP-STORE-001)")

    p = sub.add_parser("store-category",
                       help="manage store_category_id in category-groups.json (PP-STORE-001)")
    p.add_argument("action", choices=["list", "set"],
                   help="list: show eBay store categories with IDs; "
                        "set: assign an ID to a category group")
    p.add_argument("group", nargs="?", default=None,
                   help="category group key (required for 'set')")
    p.add_argument("store_id", nargs="?", type=int, default=None,
                   help="eBay store category integer ID (required for 'set')")

    sub.add_parser("strikethrough-check", help="show strikethrough pricing config state and MSRP coverage (PP-STRIKE-001)")

    sub.add_parser("restart-ebay-token", help="clear dead-letter token jobs and enqueue a fresh token_refresh immediately")

    p = sub.add_parser("restart-workers", help="restart tgw-worker@<queue>.service systemd units (uses sudo if not root)")
    p.add_argument("queues", nargs="*", help="specific queue name(s) to restart (default: all canonical workers)")
    p.add_argument("--dry-run", action="store_true", help="print the systemctl command without running it")

    p = sub.add_parser("dead-letter", help="inspect and manage dead_letter queue jobs")
    p.add_argument("--queue", default="", metavar="QUEUE", help="filter by queue name (default: all queues)")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="max jobs to show (default 50)")
    p.add_argument("--requeue", default="", metavar="JOB_ID", help="re-enqueue a specific dead_letter job by ID (cancels the dead_letter entry)")
    p.add_argument("--requeue-transient", dest="requeue_transient", action="store_true", help="re-enqueue ALL dead_letter jobs classified [transient] (honors --queue)")
    p.add_argument("--cancel", default="", metavar="QUEUE", help="cancel all dead_letter jobs in a queue")

    p = sub.add_parser("queue-history", help="show job state-transition history for a SKU, queue, or job ID (v_job_history)")
    p.add_argument("sku", nargs="?", default="", help="SKU to look up (shows all pipeline jobs for this item)")
    p.add_argument("--queue", default="", metavar="QUEUE", help="filter by queue name")
    p.add_argument("--job-id", default="", dest="job_id", metavar="JOB_ID", help="full job UUID — show all transitions for one job")
    p.add_argument("--limit", type=int, default=100, metavar="N", help="max history rows to return (default 100)")
    p.add_argument("--json", action="store_true", dest="json_out", help="output raw JSON")

    p = sub.add_parser("build-fingerprints", help="build the visual fingerprint index over thumbnails (PP-VISION-001)")
    p.add_argument("--limit", type=int, default=None, metavar="N", help="index at most N thumbnails (for a quick partial build)")
    p.add_argument("--check-only", action="store_true", dest="check_only", help="report what would be indexed without writing")

    p = sub.add_parser("locate", help="rank catalog SKUs by visual similarity to an image (PP-VISION-001)")
    p.add_argument("image", help="path to a query image (jpg/png)")
    p.add_argument("--size-class", default=None, dest="size_class", metavar="CLASS", help="restrict to a size_class (flat/packet/small_box/…)")
    p.add_argument("--top", type=int, default=10, metavar="N", help="show top N matches")
    p.add_argument("--json", action="store_true", dest="json_out", help="output raw JSON instead of a formatted list")

    p = sub.add_parser("export-catalog", help="export SQLite catalog + thumbnails to a dir for Syncthing relay (PP-PORTABLE-CATALOG-001)")
    p.add_argument("dest", help="destination directory")
    p.add_argument("--no-thumbnails", action="store_true", dest="no_thumbnails", help="export the catalog db only (skip thumbnails)")
    p.add_argument("--limit", type=int, default=None, metavar="N", help="copy at most N thumbnails")
    p.add_argument("--check-only", action="store_true", dest="check_only", help="report what would be exported without writing")
    p.add_argument("--push", action="store_true", dest="push",
                   help="trigger Syncthing rescan of catalog_export_folder_id after export (PP-PORTABLE-CATALOG-001 P2)")

    p = sub.add_parser("get-ebay-token", help="browser OAuth re-consent flow — use when refresh token is dead (HTTP 400)")
    p.add_argument("--sandbox", action="store_true", help="use eBay sandbox instead of production")
    p.add_argument("--code", default=None, help="skip browser: supply auth code from redirect URL directly (URL-encoded OK)")

    p = sub.add_parser("report", help="generate reports from ItemData (PP-DOCFLOW-001 Phase-3 seed)")
    p.add_argument("report_type", choices=["sales"], help="sales: monthly units/revenue by category-group + dead-stock ranking")
    p.add_argument("--stale", action="store_true", help="dead-stock section only (skip monthly pivot)")
    p.add_argument("--output", default=None, metavar="DIR", help="output directory (default: vault/dev-workflow/research/)")
    p.add_argument("--no-vault", action="store_true", dest="no_vault", help="return data only, do not write files")

    p = sub.add_parser("promo", help="sale event automation — draft + scope check (PP-PROMO-001 P2)")
    p.add_argument("promo_sub", choices=["draft", "list"], help="draft: generate markdown draft from dead-stock scan; list: verify sell.marketing scope")
    p.add_argument("--discount", type=int, default=None, metavar="N", help="discount %% 5–80 (default: promo.discount_pct config or 20)")
    p.add_argument("--min-days", type=int, default=None, metavar="N", dest="min_days", help="minimum days stale (default: promo.min_days_stale or 30)")
    p.add_argument("--min-price", type=float, default=None, metavar="X", dest="min_price", help="minimum current price (default: promo.min_price or 2.00)")
    p.add_argument("--max-items", type=int, default=None, metavar="N", dest="max_items", help="maximum items in draft (default: promo.max_items or 50)")
    p.add_argument("--duration", type=int, default=None, metavar="DAYS", help="event duration in days (default: promo.duration_days or 30)")
    p.add_argument("--start-offset", type=int, default=None, metavar="DAYS", dest="start_offset", help="days from today to event start (default: promo.start_offset_days or 2)")
    p.add_argument("--output", default=None, metavar="DIR", help="output directory for draft (default: vault/inbox/)")
    p.add_argument("--no-vault", action="store_true", dest="no_vault", help="return data only, do not write draft file")

    p = sub.add_parser("category-groups", help="view/manage category group taxonomy (PP-PRICE-005)")
    p.add_argument("category_id", nargs="?", default=None, help="look up which group a specific eBay category ID belongs to")
    p.add_argument("--list", action="store_true", help="list all groups with category counts and pricing")
    p.add_argument("--reseed", action="store_true", help="re-seed pricing.typical_used from current velocity-stats.json")

    # --- current-item context (PP-CONTEXT-001) ---
    p = sub.add_parser("set-context", help="set current-item context to a SKU (replaces tgwset)")
    p.add_argument("sku", help="full SKU (tgwYYYYMMDDHHMMSSmmm)")

    p = sub.add_parser("get-context", help="show current-item context (replaces tgw_sku)")
    p.add_argument("--sku-only", action="store_true", dest="sku_only", help="print bare SKU and exit (pipe-friendly; exits 1 if not set)")

    sub.add_parser("clear-context", help="clear current-item context")

    p = sub.add_parser("set-template", help="apply category group defaults to an item (PP-INTAKE-001 Phase 1)")
    p.add_argument("group_key", nargs="?", default=None, help='category group key (e.g. "electronics", "books"); omit to use current template')
    p.add_argument("sku", nargs="?", default=None, help="SKU to update (default: CurrentItem symlink)")
    p.add_argument("--list", action="store_true", dest="list_groups", help="list all available template groups")
    p.add_argument("--camera", metavar="GROUP_KEY", dest="camera_only", default=None, help="push SETTEMPLATE: to clipboard only (KDE Connect relay) — no JSON update")
    p.add_argument("--dry-run", action="store_true", help="show what would be written without making changes")

    p = sub.add_parser(
        "create-item",
        help="pre-create SKU folder + blank JSON with template applied; push COMMAND:SKU to phone (PP-INTAKE-001 Phase 2.5)",
    )
    p.add_argument("--template", default=None, metavar="GROUP", help="category group key to pre-apply (e.g. electronics)")
    p.add_argument("--count", type=int, default=1, metavar="N", help="number of items to create (default: 1, max: 20)")
    p.add_argument("--dry-run", action="store_true", help="show what would be created without writing files")

    p = sub.add_parser("data-scrub", help="ItemData maintenance passes (dry-run by default)")
    p.add_argument("--pass", dest="scrub_pass", type=int, default=1, metavar="N", help="which scrub pass to run (1=#VERIFIED→verified; 2=size_class backfill)")
    p.add_argument("--write", action="store_true", help="apply changes (default: dry-run only)")

    p = sub.add_parser(
        "alt-text",
        help="generate alt_text + seo_caption via vision model; rename primary image to <sku>-alt.jpg and archive original to history",
    )
    p.add_argument("sku", nargs="?", default=None, help="SKU to process (omit with --batch)")
    p.add_argument("--model", default=None, help="vision model ID (default: google/gemini-2.5-flash)")
    p.add_argument("--provider", default=None, choices=["openrouter", "ollama"], help="provider (default: openrouter)")
    p.add_argument("--dry-run", action="store_true", help="show what would happen without calling the model or writing files")
    p.add_argument("--batch", action="store_true", help="run all eligible items directly with rate-limiting (OpenRouter free ~20 req/min)")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="max items to process in --batch mode (0 = all eligible)")

    p = sub.add_parser(
        "alt-text-batch",
        help="bulk-enqueue alt_text jobs for items that need processing (existing catalog)",
    )
    p.add_argument("--limit", type=int, default=500, metavar="N", help="max items to enqueue (default: 500; 0 = all eligible)")
    p.add_argument("--dry-run", action="store_true", help="count eligible items without enqueuing")
    p.add_argument("--status", default="", metavar="STATUS", help="filter to items with this #STATUS value (e.g. 'live')")

    p = sub.add_parser("todo", help="multi-agent TODO tracker (PP-TODO-001 / PP-PLANDB-001)")
    p.add_argument("agent", nargs="?", default=None, help="filter by agent: claude, admin, gemini, db (omit for all); or 'brief' to generate a task spec")
    p.add_argument("brief_id", nargs="?", default=None, help="todo id for 'tgw todo brief <id>'")
    p.add_argument("--add", metavar="TEXT", help="add a new TODO item")
    p.add_argument("--done", metavar="ID", type=int, help="mark a TODO item complete")
    p.add_argument("--priority", type=int, default=50, metavar="N", help="priority for --add (lower = higher priority; default 50)")
    p.add_argument("--source", default="session", metavar="SRC", help="source label for --add (default: session)")
    p.add_argument("--all", dest="show_all", action="store_true", help="show completed items too")
    p.add_argument("--seed", action="store_true", help="seed Work Tracks items from master plan into the tracker")
    p.add_argument("--update", nargs="+", metavar=("ID", "TEXT"), help="update body text of an item: --update ID new text here")
    p.add_argument("--delegate", nargs=2, metavar=("ID", "AGENT"), help="reassign item to a different agent: --delegate ID agent")
    p.add_argument("--set-priority", nargs=2, metavar=("ID", "N"), dest="set_priority", help="change item priority: --set-priority ID N")
    p.add_argument("--pp", default=None, metavar="PP-REF", help="PP-* plan item for --add / --set-meta (e.g. PP-PLANDB-001)")
    p.add_argument("--depends", default=None, metavar="IDS", help="comma-separated todo ids this item depends on (for --add / --set-meta)")
    p.add_argument("--anchor", default=None, metavar="HEADING", help="master-plan heading text the item links to (for --add / --set-meta)")
    p.add_argument("--set-meta", type=int, default=None, metavar="ID", dest="set_meta", help="set --pp/--depends/--anchor on an existing item")
    p.add_argument("--clip", action="store_true", help="copy brief output to clipboard (brief mode only)")
    p.add_argument("--next", action="store_true", dest="next_task", help="brief mode: generate brief for the top open task for --agent")
    p.add_argument("--agent", default=None, metavar="AGENT", dest="next_agent", help="agent name for --next (e.g. claude, gemini, admin)")

    p = sub.add_parser("plan", help="plan/taskboard operations (PP-PLANDB-001)")
    p.add_argument("plan_op", choices=["render"], help="render: regenerate plan/TGW-Taskboard.md from the todo tracker")

    p = sub.add_parser(
        "mvitems",
        help="move items to a location (expands catlocmvall; PP-SHELL-001)",
    )
    p.add_argument("to_location", help="destination location string")
    p.add_argument("skus", nargs="*", help="specific SKU(s) to move")
    p.add_argument("--from", dest="from_location", default=None, help="move all items currently at this location")
    p.add_argument("--search", default=None, help="move items matching text search")
    p.add_argument("--status", default=None, help="move items with this status value")
    p.add_argument("--check-only", action="store_true", help="dry-run: show what would move without writing")

    p = sub.add_parser("suggest-edit", help="open SUGGESTIONS.md in $EDITOR for review before PM-intake")
    p.add_argument("--pending-only", action="store_true", help="extract only unprocessed ([ ]) entries to a temp file for editing")

    p = sub.add_parser("admin-file", help="scan inbox and enqueue eligible notes for PM-intake (PP-DOCFLOW-001)")
    p.add_argument("--now", action="store_true", help="bypass submission-delay gate (process all files regardless of age)")

    p = sub.add_parser("classify-suggestions", help="batch-classify unprocessed SUGGESTIONS.md entries via LLM (PP-DOCFLOW-001 Phase 2)")
    p.add_argument("--apply", action="store_true", help="mark already-done entries [x] and create todos for new-work entries")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="only classify first N pending entries (0 = all)")

    p = sub.add_parser("ai-usage", help="AI/LLM usage report by provider/task/day (Phase 5 #2)")
    p.add_argument("--since", type=int, default=7, metavar="DAYS", help="report window in days (default: 7)")
    p.add_argument("--json", dest="as_json", action="store_true", help="output raw JSON instead of formatted table")

    p = sub.add_parser(
        "revise",
        help="compute a revision delta for a live listing and write revision_draft (PP-REVISION-001; no eBay writes)",
    )
    p.add_argument("sku", help="SKU of the item to revise")
    p.add_argument(
        "--set",
        action="append",
        metavar="FIELD=VALUE",
        dest="assignments",
        default=[],
        help="field=value pair to add to the delta (repeat for multiple fields; supports dotted paths like draft_listing.price)",
    )
    p.add_argument("--show", action="store_true", help="print human-readable diff to stdout before JSON result")
    p.add_argument("--by", default="claude", metavar="AGENT", help="who is creating this revision draft (default: claude)")

    p = sub.add_parser(
        "catalog-verify",
        help="scan ItemData for assumption violations and output a checklist (PP-VERIFY-001)",
    )
    p.add_argument("--location", default="", metavar="LOC", help="limit scan to items at this location")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="stop after N items (0 = all)")
    p.add_argument("--severity", default="warning", choices=["critical", "warning", "info"], help="minimum severity to include (default: warning)")
    p.add_argument("--output", default="", metavar="PATH", help="write markdown report to file (default: stdout)")
    p.add_argument("--json", dest="as_json", action="store_true", help="output JSON summary instead of markdown report")
    p.add_argument("--mark-verified", action="store_true", help="write catalog_verified hall pass to items that pass with no violations")
    p.add_argument("--force", action="store_true", help="with --mark-verified: write hall pass even to items with violations")
    p.add_argument("--skip-verified", action="store_true", help="skip items that already have a catalog_verified hall pass (faster re-scan)")
    p.add_argument("--fix", action="store_true", help="report auto-applicable fixes (e.g. strip stale TEMPLATE: title prefix); dry-run unless --write is given")
    p.add_argument("--write", action="store_true", help="with --fix: actually apply the fixes (default: dry-run only)")

    parser.description = _make_grouped_description(sub)
    return parser


_KNOWN_STATUS_VALUES: set[str] = {
    "",
    "In Stock",
    "Out of Stock",
    "sold",
    "Sold",
    "archived",
    "New",
    "staging",
    "live",
    "TEMPLATE",
    "No Photos",
    "no photos",
    "Draft",
    "draft",
    "Listed",
    "Ended",
    "ended",
    "Pending",
}

_SEV_ORDER: Dict[str, int] = {"critical": 0, "warning": 1, "info": 2}


def _verify_item(sku: str, item_dir: Path, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return list of violation dicts for one item (PP-VERIFY-001)."""
    import re

    viols: List[Dict[str, Any]] = []

    def v(rule: str, severity: str, detail: str) -> None:
        viols.append({"rule": rule, "sku": sku, "severity": severity, "detail": detail})

    raw_title = str(doc.get("title") or "")
    title = raw_title.strip()
    if not title:
        v("no_title", "critical", "Title is empty or missing")
    else:
        if raw_title.startswith(' '):
            v("leading_space_title", "warning", f"Title has leading whitespace: {raw_title[:50]!r}")
        if title == sku:
            v("title_is_sku", "warning", "Title equals SKU")
        if title.upper().startswith("TEMPLATE:"):
            v("stale_template_prefix", "critical", f"Title starts with TEMPLATE: {title[:50]!r}")
        if len(title) < 10:
            v("title_too_short", "warning", f"Title only {len(title)} chars: {title!r}")

    location = str(doc.get("location") or "").strip()
    if not location:
        v("no_location", "warning", "Location field empty or missing")

    photos = [f for f in item_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp") and not f.name.startswith(".")]
    if not photos:
        v("no_photo", "warning", "No photo files in item folder")

    cat_id = doc.get("ebay_category_id")
    if cat_id is not None and str(cat_id).strip():
        try:
            int(str(cat_id).strip())
        except ValueError:
            v("invalid_ebay_category", "warning", f"ebay_category_id not numeric: {cat_id!r}")

    verified = str(doc.get("verified") or "").strip()
    if verified and not re.fullmatch(r"\d{8}", verified):
        v("bad_verified_date", "info", f"verified field not YYYYMMDD: {verified!r}")

    raw_status = str(doc.get("#STATUS") or doc.get("status") or "").strip()
    if raw_status and raw_status not in _KNOWN_STATUS_VALUES:
        v("unknown_status", "info", f"Unrecognised #STATUS value: {raw_status!r}")

    # New-pipeline checks
    offer_price = None
    ebay_offer = doc.get("ebay_offer") or {}
    if ebay_offer:
        try:
            offer_price = float(ebay_offer.get("price", 0) or 0)
        except (TypeError, ValueError):
            pass
    draft = doc.get("draft_listing") or {}
    if draft:
        try:
            dp = float(draft.get("price", 0) or 0)
        except (TypeError, ValueError):
            dp = 0.0
        if dp and dp <= 0:
            v("negative_price", "warning", f"draft_listing.price is non-positive: {dp}")
    if offer_price is not None and offer_price <= 0:
        v("negative_price", "warning", f"ebay_offer.price is non-positive: {offer_price}")

    ebay_listing = doc.get("ebay_listing") or {}
    if ebay_listing.get("api") == "inventory" and not ebay_offer:
        v("inventory_api_no_offer", "warning", "ebay_listing.api=inventory but ebay_offer block is missing")

    upc = str(doc.get("upc") or "").strip()
    if upc and not doc.get("product_lookup"):
        v("barcode_lookup_fail", "info", f"upc present ({upc!r}) but no product_lookup block")

    if doc.get("offline_draft"):
        import time as _time

        jf = item_dir / f"{sku}.json"
        age_hours = (_time.time() - jf.stat().st_mtime) / 3600
        if age_hours > 2:
            v("offline_draft_stall", "warning", f"offline_draft=true, file unmodified for {age_hours:.1f}h — re-run ebay_draft")

    if draft:
        try:
            dp = float(draft.get("price", 0) or 0)
        except (TypeError, ValueError):
            dp = 0.0
        if dp == 0.0:
            v("no_price", "warning", "draft_listing exists but price is zero or missing")

    condition = str(doc.get("condition") or "").strip()
    if condition:
        from tgw.apis.ebay.conditions import _ITEM_CONDITION_PREFERRED
        if condition.lower() not in _ITEM_CONDITION_PREFERRED:
            v("wrong_condition", "warning", f"condition {condition!r} not in known set")

    # Category suggestion agreement (written by ebay_draft after getCategorySuggestions call)
    agreement = draft.get("category_agreement")
    if agreement == "mismatch":
        suggestions = draft.get("category_suggestions") or []
        top_name = suggestions[0].get("category_name", "") if suggestions else ""
        resolved = draft.get("category_name") or draft.get("category_id") or ""
        detail = (
            f"Taxonomy top suggestion {top_name!r} differs from drafted category {resolved!r}"
        )
        v("category_suggestion_mismatch", "warning", detail)

    return viols


def _strip_template_prefix(title: str) -> Optional[str]:
    """
    Return *title* with a leading ``TEMPLATE:`` marker stripped, or None when
    there is nothing to strip or the result would be empty (an empty title is a
    different problem — ``no_title`` — and must not be written over the field).
    """
    import re

    m = re.match(r"(?i)^\s*template:\s*", title)
    if not m:
        return None
    stripped = title[m.end() :].strip()
    return stripped or None


def _compute_fixes(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return the list of auto-applicable fixes for one item document.

    Conservative by design — only unambiguous, lossless corrections are
    auto-fixable (PP-VERIFY-001 Phase 3).  Each fix is
    ``{rule, field, before, after}``.
    """
    fixes: List[Dict[str, Any]] = []
    title = str(doc.get("title") or "")
    new_title = _strip_template_prefix(title)
    if new_title is not None:
        fixes.append({"rule": "stale_template_prefix", "field": "title", "before": title, "after": new_title})
    elif title.startswith(' ') and title.lstrip():
        fixes.append({"rule": "leading_space_title", "field": "title", "before": title, "after": title.lstrip()})
    return fixes


def cmd_catalog_verify(
    cfg: Dict[str, Any],
    *,
    location: str = "",
    limit: int = 0,
    output: Optional[Path] = None,
    min_severity: str = "warning",
    mark_verified: bool = False,
    force: bool = False,
    skip_verified: bool = False,
    fix: bool = False,
    write: bool = False,
) -> Dict[str, Any]:
    """Scan ItemData for assumption violations and emit a markdown checklist.

    With ``fix=True`` the scan also reports auto-applicable corrections (dry-run
    by default); pass ``write=True`` to actually apply them through the item
    update fence.  A per-SKU fix log is printed and returned under ``fixes``.
    """
    from tgw.items import atomic_write_json, update_item

    root: Path = cfg["itemdata_root"]
    min_sev = _SEV_ORDER.get(min_severity, 1)

    all_violations: List[Dict[str, Any]] = []
    fix_log: List[Dict[str, Any]] = []
    scanned = 0
    skipped_verified = 0
    marked = 0
    json_errors = 0
    fixes_applied = 0

    for item_dir in sorted(root.iterdir()):
        if not item_dir.is_dir() or not item_dir.name.startswith("tgw"):
            continue
        sku = item_dir.name
        jf = item_dir / f"{sku}.json"
        if not jf.exists():
            continue

        try:
            doc = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as exc:
            all_violations.append(
                {
                    "rule": "json_parse_error",
                    "sku": sku,
                    "severity": "critical",
                    "detail": str(exc),
                }
            )
            json_errors += 1
            scanned += 1
            if limit and scanned >= limit:
                break
            continue

        if location and str(doc.get("location", "")).strip() != location:
            continue

        if skip_verified and doc.get("catalog_verified"):
            skipped_verified += 1
            continue

        scanned += 1
        item_viols = _verify_item(sku, item_dir, doc)

        # Apply fixes FIRST (when writing) so the violation list, the report,
        # the by_rule tally, and the mark_verified gate all reflect post-fix
        # state — a fixed-on-disk violation must not also appear as an open TODO.
        if fix:
            item_fixed = False
            for f in _compute_fixes(doc):
                applied = False
                error = None
                if write:
                    res = update_item(cfg, sku, f["field"], f["after"])
                    applied = bool(res.get("ok"))
                    if applied:
                        fixes_applied += 1
                        doc[f["field"]] = f["after"]
                        item_fixed = True
                    else:
                        error = res.get("error")
                fix_log.append({"sku": sku, **f, "applied": applied, "error": error})
            if item_fixed:
                item_viols = _verify_item(sku, item_dir, doc)  # re-scan mutated doc

        for viol in item_viols:
            if _SEV_ORDER.get(viol["severity"], 99) <= min_sev:
                all_violations.append(viol)

        if mark_verified:
            has_violations = bool(item_viols)
            if force or not has_violations:
                ts = datetime.now(tz=timezone.utc).isoformat()
                doc["catalog_verified"] = {"ts": ts, "by": "catalog-verify"}
                atomic_write_json(jf, doc, pretty=cfg.get("pretty", True))
                marked += 1

        if limit and scanned >= limit:
            break

    by_rule: Dict[str, int] = {}
    for viol in all_violations:
        by_rule[viol["rule"]] = by_rule.get(viol["rule"], 0) + 1

    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    skip_note = f"  |  Skipped (verified): {skipped_verified}" if skip_verified else ""
    mark_note = f"  |  Marked verified: {marked}" if mark_verified else ""
    lines = [
        "# Catalog Verification Report",
        f"Generated: {now_str}",
        f"Scanned: {scanned} items  |  Violations: {len(all_violations)}  |  Severity filter: {min_severity}+{skip_note}{mark_note}",
        "",
    ]

    for sev_label in ("critical", "warning", "info"):
        if _SEV_ORDER[sev_label] > min_sev:
            continue
        sev_viols = [v for v in all_violations if v["severity"] == sev_label]
        if not sev_viols:
            continue
        lines.append(f"## {sev_label.upper()} ({len(sev_viols)})")
        lines.append("")
        for viol in sev_viols:
            lines.append(f"- [ ] **{viol['rule']}** — `{viol['sku']}` — {viol['detail']}")
        lines.append("")

    if fix and fix_log:
        verb = "Applied" if write else "Proposed (dry-run — pass --write to apply)"
        lines.append(f"## FIXES — {verb} ({len(fix_log)})")
        lines.append("")
        for f in fix_log:
            mark = "x" if f["applied"] else " "
            err = f"  ⚠ {f['error']}" if f.get("error") else ""
            lines.append(f"- [{mark}] **{f['rule']}** — `{f['sku']}` — {f['field']}: {f['before']!r} → {f['after']!r}{err}")
        lines.append("")

    report = "\n".join(lines)

    if output:
        output.write_text(report, encoding="utf-8")
        print(f"Report written to {output}")
    else:
        print(report)

    return {
        "ok": True,
        "scanned": scanned,
        "violations": len(all_violations),
        "by_rule": by_rule,
        "json_errors": json_errors,
        "skipped_verified": skipped_verified,
        "marked_verified": marked,
        "fixes": fix_log,
        "fixes_applied": fixes_applied,
        "fixes_proposed": len(fix_log),
    }


def cmd_hint(cfg: Dict[str, Any], sku: str, hint: str, force: bool = False) -> Dict[str, Any]:
    """Write ai_hint to an item and enqueue re-identification."""
    from tgw.config import sku_json
    from tgw.items import atomic_write_json
    from tgw.queue import state_machine

    json_path = sku_json(cfg, sku)
    if not json_path.exists():
        return {"ok": False, "error": f"item not found: {sku}"}

    item = json.loads(json_path.read_text(encoding="utf-8"))
    already = bool(item.get("ai_identified"))

    prev_hint = item.get("ai_hint") or None
    item["ai_hint"] = hint
    if force or not already:
        item["ai_reidentify"] = True

    from tgw.items import append_history_event

    append_history_event(
        item,
        {
            "event": "hint_set",
            "hint": hint,
            "prev_hint": prev_hint,
            "by": "operator",
        },
    )

    atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))

    # Enqueue ai_identify — dedupe key means a pending job won't double-enqueue
    import psycopg2.errors

    try:
        state_machine.init(cfg.get("postgres_dsn", "dbname=state_machine user=tgw"))
        jid = state_machine.enqueue_job(
            queue_name="ai_identify",
            payload={"sku": sku},
            dedupe_key=f"ai_identify:{sku}",
            max_attempts=3,
        )
        queued = True
    except psycopg2.errors.UniqueViolation:
        jid = None
        queued = False

    return {
        "ok": True,
        "sku": sku,
        "hint": hint,
        "force": force or not already,
        "queued": queued,
        "job_id": jid,
    }


def cmd_hint_trail(cfg: Dict[str, Any], sku: str) -> Dict[str, Any]:
    """Return and print the identification_history trail for an item."""
    from tgw.config import sku_json

    json_path = sku_json(cfg, sku)
    if not json_path.exists():
        return {"ok": False, "error": f"item not found: {sku}"}

    item = json.loads(json_path.read_text(encoding="utf-8"))
    history = item.get("identification_history", [])

    if not history:
        print(f"No identification history for {sku}.")
        return {"ok": True, "sku": sku, "count": 0, "history": []}

    print(f"Identification history for {sku} ({len(history)} event(s)):\n")
    for ev in history:
        ts = ev.get("ts", "?")
        etype = ev.get("event", "?")
        if etype == "ai_identify":
            rnd = ev.get("round", "?")
            ptype = ev.get("prompt_type", "?")
            hint = ev.get("hint") or "—"
            title = ev.get("title", "?")
            cat = ev.get("category", "?")
            cond = ev.get("condition", "?")
            src = ev.get("lookup_source") or ""
            src_str = f" [{src}]" if src else ""
            print(f"  {ts}  ai_identify  round {rnd} | {ptype}{src_str}")
            print(f"    hint: {hint}")
            print(f'    → "{title}" | {cat} | {cond}')
        elif etype == "hint_set":
            hint = ev.get("hint", "?")
            prev = ev.get("prev_hint") or "—"
            by = ev.get("by", "?")
            print(f'  {ts}  hint_set     "{hint}" (by {by}, prev: {prev})')
        else:
            print(f"  {ts}  {etype}  {ev}")
        print()

    return {"ok": True, "sku": sku, "count": len(history), "history": history}


def cmd_dead_letter(
    cfg: Dict[str, Any],
    *,
    queue: str = "",
    limit: int = 50,
    requeue_id: str = "",
    requeue_transient: bool = False,
    cancel_queue: str = "",
) -> Dict[str, Any]:
    """Inspect and manage dead_letter queue jobs."""
    from tgw.queue import state_machine
    from tgw.queue.worker_base import classify_dead_letter

    state_machine.init(cfg["postgres_dsn"])

    if requeue_id:
        try:
            new_id = state_machine.requeue_dead_letter_job(requeue_id)
            print(f"Re-enqueued: {requeue_id[:8]}… → new job {new_id[:8]}…")
            return {"ok": True, "action": "requeue", "old_job_id": requeue_id, "new_job_id": new_id}
        except ValueError as exc:
            print(f"Error: {exc}")
            return {"ok": False, "error": str(exc)}

    if requeue_transient:
        # Batch-requeue every dead_letter job whose error classifies as transient.
        # Scans all dead_letter jobs (limit is intentionally large here), honors --queue.
        jobs = state_machine.dead_letter_jobs(queue_name=queue, limit=10000)
        requeued: List[Dict[str, str]] = []
        skipped_permanent = 0
        for job in jobs:
            error = (job.get("error_detail") or "").replace("\n", " ")
            verdict, _ = classify_dead_letter(error)
            if verdict != "requeue":
                skipped_permanent += 1
                continue
            try:
                new_id = state_machine.requeue_dead_letter_job(job["job_id"])
                requeued.append({"old_job_id": job["job_id"], "new_job_id": new_id, "queue": job["queue_name"]})
                print(f"  requeued {str(job['job_id'])[:8]}… ({job['queue_name']}) → {new_id[:8]}…")
            except ValueError as exc:
                print(f"  skip {str(job['job_id'])[:8]}…: {exc}")
        label = f" in {queue!r}" if queue else ""
        print(f"Re-enqueued {len(requeued)} transient dead_letter job(s){label}; left {skipped_permanent} permanent job(s) untouched.")
        return {"ok": True, "action": "requeue_transient", "requeued": requeued, "requeued_count": len(requeued), "skipped_permanent": skipped_permanent}

    if cancel_queue:
        n = state_machine.clear_dead_letter(cancel_queue)
        print(f"Cancelled {n} dead_letter job(s) in queue {cancel_queue!r}.")
        return {"ok": True, "action": "cancel", "queue": cancel_queue, "cancelled": n}

    jobs = state_machine.dead_letter_jobs(queue_name=queue, limit=limit)
    if not jobs:
        label = f" in {queue!r}" if queue else ""
        print(f"No dead_letter jobs{label}.")
        return {"ok": True, "count": 0, "jobs": []}

    current_q = None
    for job in jobs:
        if job["queue_name"] != current_q:
            current_q = job["queue_name"]
            print(f"\n── {current_q} ──")
        payload = dict(job["payload_json"]) if job["payload_json"] else {}
        sku = payload.get("sku", payload.get("entity_id", "—"))
        error = (job["error_detail"] or "").replace("\n", " ")
        verdict, _ = classify_dead_letter(error)
        verdict_tag = "[transient]" if verdict == "requeue" else "[permanent]"
        jid_short = str(job["job_id"])[:8]
        finished = str(job["finished_at"])[:16] if job["finished_at"] else "?"
        print(f"  {jid_short}  sku={sku:<22} {verdict_tag}  finished={finished}")
        if error:
            print(f"           {error[:100]}")

    print(f"\n{len(jobs)} dead_letter job(s). Use --requeue JOB_ID or --cancel QUEUE to act.")
    return {"ok": True, "count": len(jobs), "jobs": [{**j, "payload_json": dict(j["payload_json"] or {}), "verdict": classify_dead_letter(j.get("error_detail") or "")[0]} for j in jobs]}


def cmd_queue_history(
    cfg: Dict[str, Any],
    *,
    sku: str = "",
    queue: str = "",
    job_id: str = "",
    limit: int = 100,
    json_out: bool = False,
) -> Dict[str, Any]:
    """Show job state-transition history from v_job_history."""
    from tgw.queue import state_machine

    state_machine.init(cfg["postgres_dsn"])
    rows = state_machine.job_history(sku=sku, queue_name=queue, job_id=job_id, limit=limit)

    if not rows:
        label = sku or job_id or queue or "(all)"
        print(f"No history found for {label}.")
        return {"ok": True, "count": 0, "rows": []}

    if json_out:
        print(json.dumps(rows, indent=2, default=str))
        return {"ok": True, "count": len(rows), "rows": rows}

    # Grouped by job_id for readable output
    current_job: Optional[str] = None
    for r in rows:
        jid = str(r["job_id"])
        if jid != current_job:
            current_job = jid
            ts = str(r["created_at"])[:16]
            print(f"\n── {r['queue_name']}  {jid[:8]}…  entity={r['entity_id']}  state={r['current_state']}  {ts}")
        arrow = f"{r['old_state'] or '—'} → {r['new_state']}"
        ts_short = str(r["created_at"])[11:19]
        msg = r.get("message") or r.get("error_detail") or ""
        msg_short = msg.replace("\n", " ")[:80]
        suffix = f"  {msg_short}" if msg_short else ""
        print(f"  {ts_short}  {arrow}{suffix}")

    print(f"\n{len(rows)} transition(s).")
    return {"ok": True, "count": len(rows), "rows": [{**r, "payload_json": dict(r["payload_json"] or {})} for r in rows]}


def cmd_build_fingerprints(cfg: Dict[str, Any], *, limit: Optional[int] = None, check_only: bool = False) -> Dict[str, Any]:
    """Build/refresh the visual fingerprint index over the thumbnail cache (PP-VISION-001)."""
    from tgw.fingerprint import build_fingerprint_index

    result = build_fingerprint_index(cfg, limit=limit, check_only=check_only)
    if result.get("ok"):
        verb = "would index" if check_only else "indexed"
        extra = f" ({result['problems']} unreadable)" if result.get("problems") else ""
        print(f"{verb} {result['count']}/{result['source_count']} thumbnails{extra} in {result['elapsed_seconds']}s → {result['path']}")
    else:
        print(f"Error: {result.get('error', result)}")
    return result


def cmd_locate(cfg: Dict[str, Any], image_path: str, *, size_class: Optional[str] = None, top: int = 10, json_out: bool = False) -> Dict[str, Any]:
    """Rank catalog SKUs by visual similarity to an image (PP-VISION-001)."""
    from tgw.fingerprint import locate_image

    result = locate_image(cfg, image_path, size_class=size_class, top=top)
    if json_out:
        print(json.dumps(result, indent=2))
        return result
    if not result.get("ok"):
        print(f"Error: {result.get('error', result)}")
        return result
    sc = f" [size_class={size_class}]" if size_class else ""
    print(f"Top {result['count']} matches for {image_path}{sc}:")
    for m in result["matches"]:
        print(f"  {m['distance']:.4f}  {m['sku']:<22} (dhash={m['dhash_distance']:.3f} hist={m['hist_distance']:.3f}{' ' + m['size_class'] if m['size_class'] else ''})")
    return result


def cmd_export_catalog(cfg: Dict[str, Any], dest: str, *, no_thumbnails: bool = False, limit: Optional[int] = None, check_only: bool = False, push: bool = False) -> Dict[str, Any]:
    """Export the SQLite catalog + thumbnails to a directory for Syncthing relay (PP-PORTABLE-CATALOG-001)."""
    from tgw.catalog_export import export_catalog

    push_folder_id = cfg.get('catalog_export_folder_id') or None if push else None
    if push and not push_folder_id:
        print("Warning: --push requested but catalog_export_folder_id is not set in config — skipping Syncthing trigger")

    result = export_catalog(cfg, dest, with_thumbnails=not no_thumbnails, limit=limit,
                            check_only=check_only, push_folder_id=push_folder_id)
    if result.get("ok"):
        verb = "would export" if check_only else "exported"
        mb = round(result["bytes_total"] / 1_048_576, 1)
        pushed = " + Syncthing push triggered" if result.get("syncthing_pushed") else ""
        err = f" (Syncthing error: {result['syncthing_error']})" if result.get("syncthing_error") else ""
        print(f"{verb} catalog + {result['thumbnails_copied']} thumbnails ({mb} MB) → {result['dest']} in {result['elapsed_seconds']}s{pushed}{err}")
    else:
        print(f"Error: {result.get('error', result)}")
    return result


def cmd_requeue(
    cfg: Dict[str, Any],
    *,
    no_title: bool = False,
    unidentified: bool = False,
    hint_set: bool = False,
    no_draft: bool = False,
    no_price: bool = False,
    catalog_only: bool = False,
    limit: int = 100,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Bulk-enqueue ai_identify (or ebay_draft/ebay_price) for items matching filters.
    Default is dry-run — pass dry_run=False to actually queue.
    At least one filter must be specified.
    """
    import psycopg2.errors

    from tgw.queue import state_machine

    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}

    if not any([no_title, unidentified, hint_set, no_draft, no_price]):
        return {"ok": False, "error": "specify at least one filter flag"}

    if not dry_run:
        state_machine.init(cfg.get("postgres_dsn", "dbname=state_machine user=tgw"))

    matched, queued, skipped_pending, skipped_no_photos = [], [], [], []
    root: Path = cfg["itemdata_root"]

    for sku_dir in root.iterdir():
        if limit and len(queued) >= limit:
            break
        j = sku_dir / f"{sku_dir.name}.json"
        if not j.exists():
            continue
        d = json.loads(j.read_text(encoding="utf-8"))
        sku = sku_dir.name
        title = str(d.get("title", "")).strip()
        ai_id = d.get("ai_identified")
        draft = d.get("draft_listing") or {}
        price = draft.get("price") or d.get("ebay_offer", {}).get("price")

        # Determine which queue this item needs
        target_queue = "ai_identify"
        payload: Dict[str, Any] = {"sku": sku}
        if catalog_only:
            payload["catalog_only"] = True

        if no_title:
            if ai_id or (title and title != sku):
                continue
        if unidentified:
            if ai_id:
                continue
        if hint_set:
            if not d.get("ai_hint") or ai_id:
                continue
        if no_draft:
            if not ai_id or draft:
                continue
            target_queue = "ebay_draft"
            payload = {"sku": sku}
        if no_price:
            if not draft or price is not None:
                continue
            target_queue = "ebay_price"
            payload = {"sku": sku}

        # ai_identify requires at least one photo
        if target_queue == "ai_identify":
            has_photos = any(p.suffix in _IMAGE_EXTS for p in sku_dir.iterdir() if p.is_file())
            if not has_photos:
                skipped_no_photos.append(sku)
                continue

        matched.append(sku)

        if not dry_run:
            dedupe_key = f"{target_queue}:{sku}"
            try:
                state_machine.enqueue_job(
                    queue_name=target_queue,
                    payload=payload,
                    dedupe_key=dedupe_key,
                    max_attempts=3,
                )
                queued.append(sku)
            except psycopg2.errors.UniqueViolation:
                skipped_pending.append(sku)

    return {
        "ok": True,
        "dry_run": dry_run,
        "catalog_only": catalog_only,
        "matched": len(matched),
        "queued": len(queued) if not dry_run else 0,
        "skipped_pending": len(skipped_pending),
        "skipped_no_photos": len(skipped_no_photos),
        "limit": limit,
        "sample": matched[:5],
    }


def cmd_resolve_legacy(cfg: Dict[str, Any], skus: List[str], enqueue_stage: bool = True) -> Dict[str, Any]:
    """
    Mark one or more items as having their legacy eBay Trading API listing
    cleared, setting legacy_listing_resolved=True so ebay_stage can proceed.
    Optionally enqueues ebay_stage for each resolved item.
    """
    import psycopg2.errors

    from tgw.config import sku_json
    from tgw.items import atomic_write_json
    from tgw.queue import state_machine

    state_machine.init(cfg.get("postgres_dsn", "dbname=state_machine user=tgw"))

    resolved, not_found, already_done, staged = [], [], [], []

    for sku in skus:
        json_path = sku_json(cfg, sku)
        if not json_path.exists():
            not_found.append(sku)
            continue

        item = json.loads(json_path.read_text(encoding="utf-8"))

        if item.get("legacy_listing_resolved"):
            already_done.append(sku)
        else:
            item["legacy_listing_resolved"] = True
            atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))
            resolved.append(sku)

        # Only queue ebay_stage if the item has already been priced —
        # otherwise the normal pipeline will handle it after ai_identify/ebay_draft/ebay_price
        draft = item.get("draft_listing", {})
        pipeline_ready = draft.get("price") is not None or item.get("ebay_offer", {}).get("price") is not None
        if enqueue_stage and pipeline_ready and not item.get("ebay_offer", {}).get("offer_id"):
            try:
                state_machine.enqueue_job(
                    queue_name="ebay_stage",
                    payload={"sku": sku},
                    dedupe_key=f"ebay_stage:{sku}",
                    max_attempts=5,
                )
                staged.append(sku)
            except psycopg2.errors.UniqueViolation:
                pass

    return {
        "ok": True,
        "resolved": resolved,
        "already_done": already_done,
        "not_found": not_found,
        "stage_queued": staged,
    }


def cmd_seo_audit(cfg: Dict[str, Any], limit: int = 50, live_only: bool = False) -> Dict[str, Any]:
    """
    SEO quality report for live and staged listings.

    Surfaces items with weak titles, missing brand/model, low quality scores,
    category mismatches, and thin descriptions — sorted worst-first.
    Impression data requires sell.analytics.readonly (not yet applied).
    """
    root: Path = cfg["itemdata_root"]
    items = []

    for child in sorted(root.iterdir()):
        jf = child / f"{child.name}.json"
        if not jf.exists():
            continue
        try:
            doc = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue

        listing = doc.get("ebay_listing", {})
        offer = doc.get("ebay_offer", {})
        draft = doc.get("draft_listing") or {}

        is_live = listing.get("status") == "Active"
        is_staged = offer.get("offer_id") and offer.get("status") == "UNPUBLISHED"

        if not is_live and not is_staged:
            continue
        if live_only and not is_live:
            continue

        quality = draft.get("quality") or {}
        title = draft.get("title") or doc.get("title", "")
        desc = draft.get("description") or doc.get("description", "")
        flags = draft.get("title_flags") or []

        # Compute days listed (live items only)
        days_listed = None
        pub_at = listing.get("published_at") or listing.get("synced_at")
        if is_live and pub_at:
            try:
                from datetime import datetime, timezone

                pub_dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                days_listed = (datetime.now(timezone.utc) - pub_dt).days
            except Exception:
                pass

        seo_issues = list(flags)
        cat_conf = draft.get("category_confidence")
        if cat_conf == "low":
            seo_issues.append("cat_mismatch")
        desc_words = len(desc.split()) if desc else 0
        if desc_words < 75:
            seo_issues.append(f"desc_short({desc_words}w)")
        if not draft.get("epid") and not doc.get("epid"):
            pl = doc.get("product_lookup") or {}
            from tgw.apis.lookup.base import barcode_from_item

            barcode, _ = barcode_from_item(doc)
            if barcode and not pl.get("source"):
                seo_issues.append("no_epid")

        items.append(
            {
                "sku": child.name,
                "title": title[:50],
                "status": "live" if is_live else "staged",
                "days_listed": days_listed,
                "quality": quality.get("score"),
                "price_confidence": draft.get("price_confidence"),
                "category_confidence": cat_conf,
                "desc_words": desc_words,
                "seo_issues": seo_issues,
                "listing_id": listing.get("listing_id"),
            }
        )

    items.sort(key=lambda x: (x["quality"] is None, x["quality"] or 0))
    items = items[:limit]
    return {"ok": True, "count": len(items), "items": items, "note": "Impression data requires sell.analytics.readonly scope"}


def cmd_staged(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """List items with UNPUBLISHED eBay offers awaiting operator review.

    Items already marked ready (``ebay_offer.ready_at``) have passed review and
    sit in the dole-out queue — they are counted but not listed (``tgw ready``).
    """
    root: Path = cfg["itemdata_root"]
    items = []
    ready_count = 0
    for child in sorted(root.iterdir()):
        jf = child / f"{child.name}.json"
        if not jf.exists():
            continue
        try:
            doc = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        offer = doc.get("ebay_offer", {})
        if offer.get("offer_id") and offer.get("status") == "UNPUBLISHED" and offer.get("ready_at"):
            ready_count += 1
            continue
        if offer.get("offer_id") and offer.get("status") == "UNPUBLISHED":
            draft = doc.get("draft_listing") or {}
            quality = draft.get("quality") or {}
            items.append(
                {
                    "sku": child.name,
                    "title": doc.get("title", ""),
                    "price": offer.get("price"),
                    "location": doc.get("location", ""),
                    "category": doc.get("ebay_category_name", ""),
                    "offer_id": offer.get("offer_id"),
                    "staged_at": offer.get("staged_at", ""),
                    "quality": quality.get("score"),
                    "quality_flags": quality.get("flags", []),
                    "price_confidence": draft.get("price_confidence"),
                    "category_confidence": draft.get("category_confidence"),
                    "comp_count": (offer.get("price_comps") or {}).get("count"),
                }
            )
    # Sort ascending by quality score so worst items surface first
    items.sort(key=lambda x: (x["quality"] is None, x["quality"] or 0))
    return {"ok": True, "count": len(items), "items": items, "ready_count": ready_count}


def cmd_publish(cfg: Dict[str, Any], skus: List[str], dry_run: bool = False) -> Dict[str, Any]:
    """Enqueue ebay_publish for each SKU that has an UNPUBLISHED offer."""
    import psycopg2.errors

    from tgw.queue import state_machine

    enqueued: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    for sku in skus:
        jf = cfg["itemdata_root"] / sku / f"{sku}.json"
        if not jf.exists():
            errors.append(f"{sku}: item not found")
            continue
        try:
            doc = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{sku}: bad JSON — {exc}")
            continue

        offer = doc.get("ebay_offer", {})
        if not offer.get("offer_id"):
            errors.append(f"{sku}: no offer_id — run ebay_stage first")
            continue
        if offer.get("status") != "UNPUBLISHED":
            skipped.append(f"{sku}: offer status is {offer.get('status')!r} — not UNPUBLISHED")
            continue

        if dry_run:
            enqueued.append(sku)
            continue

        try:
            state_machine.enqueue_job(
                queue_name="ebay_publish",
                payload={"sku": sku},
                dedupe_key=f"ebay_publish:{sku}",
                max_attempts=3,
            )
            enqueued.append(sku)
        except psycopg2.errors.UniqueViolation:
            skipped.append(f"{sku}: already queued")
        except Exception as exc:
            errors.append(f"{sku}: {exc}")

    return {
        "ok": not errors,
        "dry_run": dry_run,
        "enqueued": enqueued,
        "skipped": skipped,
        "errors": errors,
    }


def cmd_import_sold_csv(cfg: Dict[str, Any], csv_path: Path, dry_run: bool = False, show_columns: bool = False, fuzzy: bool = False, fuzzy_threshold: float = 0.80) -> Dict[str, Any]:
    """
    Import an eBay Seller Hub sold-orders CSV and mark matched items sold.

    Matches rows to item JSONs via Item number → ebay_listing.listing_id.
    Idempotent: items already marked sold are skipped.
    """
    import csv as _csv

    from .ebay.pull import build_listing_index, mark_item_sold

    # eBay Seller Hub column names vary slightly across exports; try each in order.
    _COL_LISTING_ID = ("Item number", "Item Number", "ItemID", "Item ID")
    _COL_SALE_DATE = ("Sale date", "Sale Date", "Purchase date", "Purchase Date", "Order date")
    _COL_SALE_PRICE = ("Sale price", "Sale Price", "Item price", "Sold for", "Sold For", "Unit price")
    _COL_BUYER = ("Buyer username", "Buyer Username", "Buyer user ID", "Buyer")
    _COL_ORDER_ID = ("Order ID", "Order number", "Sales record number", "Transaction ID")
    _COL_QUANTITY = ("Quantity", "Qty", "Item quantity")

    def _pick(row: Dict[str, str], candidates: tuple) -> str:
        for c in candidates:
            if c in row:
                return (row[c] or "").strip()
        return ""

    if not csv_path.exists():
        return {"ok": False, "error": f"file not found: {csv_path}"}

    # eBay Seller Hub exports often have one or more blank leading rows before
    # the real header. Skip any row where every field is empty, then use the
    # first non-blank row as the header.
    import io as _io

    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        raw_lines = fh.readlines()

    header_idx = None
    for i, line in enumerate(raw_lines):
        if any(c.strip().strip('"') for c in line.split(",")):
            header_idx = i
            break

    if header_idx is None:
        return {"ok": False, "error": "CSV appears empty — no header row found"}

    content = "".join(raw_lines[header_idx:])
    reader = _csv.DictReader(_io.StringIO(content))
    rows = list(reader)
    columns = reader.fieldnames or []

    if show_columns:
        return {"ok": True, "columns": list(columns), "row_count": len(rows)}

    if not rows:
        return {"ok": True, "matched": 0, "marked": 0, "skipped": 0, "unmatched": 0, "errors": 0, "dry_run": dry_run}

    # Verify we can find the listing_id column
    sample = rows[0]
    if not any(c in sample for c in _COL_LISTING_ID):
        return {
            "ok": False,
            "error": f"Cannot find Item number column. Columns found: {list(columns)}. Use --show-columns to inspect the file.",
        }

    synced_at = datetime.now(tz=timezone.utc).isoformat()
    itemdata_root = cfg["itemdata_root"]
    listing_index = build_listing_index(itemdata_root)

    stats: Dict[str, Any] = {
        "rows": len(rows),
        "matched": 0,
        "marked": 0,
        "already_sold": 0,
        "unmatched": 0,
        "errors": 0,
    }
    unmatched_ids: List[str] = []
    unmatched_rows: List[Dict[str, str]] = []

    for row in rows:
        listing_id = _pick(row, _COL_LISTING_ID)
        if not listing_id:
            continue

        json_path = listing_index.get(listing_id)
        if not json_path:
            stats["unmatched"] += 1
            unmatched_ids.append(listing_id)
            unmatched_rows.append(row)
            continue

        stats["matched"] += 1
        sale_price_raw = _pick(row, _COL_SALE_PRICE)
        try:
            sale_price = float(sale_price_raw.lstrip("$").replace(",", ""))
        except (ValueError, AttributeError):
            sale_price = sale_price_raw

        try:
            did_mark = mark_item_sold(
                json_path,
                order_id=_pick(row, _COL_ORDER_ID) or f"csv-import-{listing_id}",
                buyer=_pick(row, _COL_BUYER),
                sale_price=sale_price,
                quantity=int(_pick(row, _COL_QUANTITY) or "1"),
                sale_date=_pick(row, _COL_SALE_DATE),
                synced_at=synced_at,
                cfg=cfg,
                dry_run=dry_run,
            )
            if did_mark:
                stats["marked"] += 1
            else:
                stats["already_sold"] += 1
        except Exception as exc:
            stats["errors"] += 1
            print(f"  ERROR listing {listing_id}: {exc}")

    # --- Archive pass (uses pre-built cache; skip if cache absent) -----------
    archive_dir = cfg["itemdata_root"].parent / "history" / "ItemArchive"
    cache_path = cfg["itemdata_root"].parent.parent / "var" / "archive-ebay-index.json"
    still_unmatched_rows: List[Dict[str, str]] = []

    if unmatched_rows and cache_path.exists():
        from .ebay.pull import build_archive_index

        archive_index = build_archive_index(archive_dir, cfg["itemdata_root"], cache_path=cache_path)

        archive_matched = 0
        archive_tombstoned = 0
        for row in unmatched_rows:
            listing_id = _pick(row, _COL_LISTING_ID)
            entry = archive_index.get(listing_id) if listing_id else None
            sku_match = entry[0] if entry else None
            json_path = entry[1] if entry else None

            if json_path and not json_path.exists() and sku_match:
                if dry_run:
                    # Peek without writing — count if ZIP is present
                    zip_path = archive_dir / f"{sku_match}.zip"
                    if zip_path.exists():
                        stats["matched"] += 1
                        archive_matched += 1
                        archive_tombstoned += 1
                        stats["marked"] += 1
                    else:
                        still_unmatched_rows.append(row)
                    continue
                else:
                    from .ebay.pull import restore_archive_tombstone

                    json_path = restore_archive_tombstone(archive_dir, sku_match, itemdata_root, cfg)
                    if json_path:
                        archive_tombstoned += 1

            if not json_path or not json_path.exists():
                still_unmatched_rows.append(row)
                continue

            stats["matched"] += 1
            sale_price_raw = _pick(row, _COL_SALE_PRICE)
            try:
                sale_price = float(sale_price_raw.lstrip("$").replace(",", ""))
            except (ValueError, AttributeError):
                sale_price = sale_price_raw

            try:
                did_mark = mark_item_sold(
                    json_path,
                    order_id=_pick(row, _COL_ORDER_ID) or f"csv-archive-{listing_id}",
                    buyer=_pick(row, _COL_BUYER),
                    sale_price=sale_price,
                    quantity=int(_pick(row, _COL_QUANTITY) or "1"),
                    sale_date=_pick(row, _COL_SALE_DATE),
                    synced_at=synced_at,
                    cfg=cfg,
                    dry_run=dry_run,
                )
                if did_mark:
                    stats["marked"] += 1
                    archive_matched += 1
                else:
                    stats["already_sold"] += 1
            except Exception as exc:
                stats["errors"] += 1
                print(f"  ERROR archive match listing {listing_id}: {exc}")

        stats["archive_matched"] = archive_matched
        stats["archive_tombstoned"] = archive_tombstoned
        print(f"  Archive pass: {archive_matched} matches ({archive_tombstoned} tombstones restored)")
    elif unmatched_rows and not cache_path.exists():
        print("  Archive pass skipped — run `tgw build-archive-index` once to enable it")
        still_unmatched_rows = unmatched_rows
    else:
        still_unmatched_rows = unmatched_rows

    remaining_unmatched = still_unmatched_rows
    unmatched_ids_final = [_pick(r, _COL_LISTING_ID) for r in remaining_unmatched if _pick(r, _COL_LISTING_ID)]

    if unmatched_ids_final:
        print(f"  {len(unmatched_ids_final)} still unmatched after listing ID + archive passes:")
        for lid in unmatched_ids_final[:20]:
            print(f"    {lid}")
        if len(unmatched_ids_final) > 20:
            print(f"    ... and {len(unmatched_ids_final) - 20} more")

    # --- Fuzzy title pass (opt-in) -------------------------------------------
    if fuzzy and remaining_unmatched:
        from .ebay.pull import build_title_lookup, find_title_match

        catalog_db = cfg["sqlite_catalog_path"]
        itemdata_root = cfg["itemdata_root"]
        print(f"\n  Building title index for fuzzy pass (threshold={fuzzy_threshold})...")
        title_index, word_index = build_title_lookup(catalog_db, itemdata_root)

        fuzzy_matched = 0
        fuzzy_skipped = 0  # ambiguous / below threshold
        fuzzy_details: List[str] = []

        for row in remaining_unmatched:
            item_title = _pick(row, ("Item Title", "item_title", "Item title", "Title"))
            if not item_title:
                fuzzy_skipped += 1
                continue

            result = find_title_match(item_title, title_index, word_index, threshold=fuzzy_threshold)
            if result is None:
                fuzzy_skipped += 1
                continue

            sku, json_path, score = result
            if not json_path.exists():
                fuzzy_skipped += 1
                continue

            listing_id = _pick(row, _COL_LISTING_ID)
            sale_price_raw = _pick(row, _COL_SALE_PRICE)
            try:
                sale_price = float(sale_price_raw.lstrip("$").replace(",", ""))
            except (ValueError, AttributeError):
                sale_price = sale_price_raw

            fuzzy_details.append(f"    {sku}  score={score:.2f}  listing={listing_id}  title={item_title[:50]}")

            try:
                did_mark = mark_item_sold(
                    json_path,
                    order_id=_pick(row, _COL_ORDER_ID) or f"csv-fuzzy-{listing_id}",
                    buyer=_pick(row, _COL_BUYER),
                    sale_price=sale_price,
                    quantity=int(_pick(row, _COL_QUANTITY) or "1"),
                    sale_date=_pick(row, _COL_SALE_DATE),
                    synced_at=synced_at,
                    cfg=cfg,
                    dry_run=dry_run,
                )
                if did_mark:
                    fuzzy_matched += 1
                    stats["marked"] += 1
                else:
                    stats["already_sold"] += 1
            except Exception as exc:
                stats["errors"] += 1
                print(f"  ERROR fuzzy match {sku}: {exc}")

        stats["fuzzy_matched"] = fuzzy_matched
        stats["fuzzy_skipped"] = fuzzy_skipped
        if fuzzy_details:
            print(f"\n  Fuzzy title matches ({fuzzy_matched} marked, threshold={fuzzy_threshold}):")
            for line in fuzzy_details:
                print(line)

    return {"ok": True, "dry_run": dry_run, **stats}


def cmd_ai_usage(cfg: Dict[str, Any], since_days: int = 7) -> Dict[str, Any]:
    """Aggregate AI/LLM usage from the ai_usage table.

    Returns ``{'ok': True, 'rows': [...], 'since_days': N}``.
    Prints a formatted table to stdout when called from the CLI.
    """
    from tgw.queue import state_machine as sm
    sm.init(cfg.get('postgres_dsn', 'dbname=state_machine user=tgw'))
    try:
        rows = sm.query_ai_usage(since_days)
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}
    return {'ok': True, 'rows': rows, 'since_days': since_days}


def _print_ai_usage_table(rows: List[Dict[str, Any]], since_days: int) -> None:
    if not rows:
        print(f'No AI usage recorded in the last {since_days} day(s).')
        return

    print(f'AI usage — last {since_days} day(s)\n')
    current_day = None
    for row in rows:
        day = str(row.get('day', ''))
        if day != current_day:
            print(f'  {day}')
            current_day = day
        task     = row.get('task', '')
        provider = row.get('provider', '')
        model    = row.get('model', '')
        calls    = int(row.get('calls') or 0)
        ms       = int(row.get('total_ms') or 0)
        tokens   = row.get('total_tokens')
        errors   = int(row.get('errors') or 0)

        dur = f'{ms // 60000}m {(ms % 60000) // 1000}s' if ms >= 60000 else f'{ms // 1000}s'
        tok_str = f'{int(tokens):,}' if tokens else 'n/a'
        err_str = f'  ⚠ {errors} error(s)' if errors else ''
        model_short = model.split('/')[-1] if '/' in model else model
        print(f'    {task:<20} {provider:<12} {model_short:<32} {calls:>4} calls  {dur:>8}  {tok_str:>10} tokens{err_str}')
    print()


def cmd_velocity_report(
    cfg: Dict[str, Any],
    category: Optional[str] = None,
    refresh: bool = False,
    min_sold: int = 1,
) -> Dict[str, Any]:
    """Compute or load velocity stats and return a report dict."""
    from tgw.velocity import aggregate_velocity, load_velocity_stats, save_velocity_stats

    itemdata_root: Path = cfg["itemdata_root"]
    catalog_root: Path = cfg["catalog_root"]

    stats = None
    if not refresh:
        stats = load_velocity_stats(catalog_root)

    if stats is None:
        stats = aggregate_velocity(itemdata_root)
        save_velocity_stats(catalog_root, stats, pretty=cfg.get("pretty", True))

    cats = stats.get("categories", {})
    if category:
        cats = {k: v for k, v in cats.items() if k == category}
    if min_sold > 1:
        cats = {k: v for k, v in cats.items() if v.get("sold_count", 0) >= min_sold}

    return {
        "ok": True,
        "generated_at": stats.get("generated_at"),
        "item_count": stats.get("item_count", 0),
        "categories": cats,
    }


def cmd_ebay_sweep(cfg: Dict[str, Any], *, groups: str = "A", location: Optional[str] = None, limit: int = 0, output: Optional[Path] = None) -> Dict[str, Any]:
    """
    Scan ItemData for ambiguous-status items and generate a physical inventory checklist.

    Groups:
      A — Active eBay listing, local status not confirmed (most urgent)
      B — "out of stock" legacy items with no eBay listing (likely sold, untracked)
      C — No status and no eBay listing (completely uncategorized)

    Output is a markdown checklist (stdout or --output file) for Obsidian review.
    """

    selected = {g.strip().upper() for g in groups.split(",")}

    _CLEAR_STATUS = {"sold", "disposed", "recalled", "merged", "discard", "disposeddisposed", "vero"}

    itemdata_root = cfg["itemdata_root"]
    results: Dict[str, List[Dict[str, Any]]] = {"A": [], "B": [], "C": []}

    for json_path in itemdata_root.glob("*/*.json"):
        try:
            item = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(item, dict):
                continue
        except Exception:
            continue

        sku = json_path.parent.name
        raw_status = str(item.get("status", "")).lower().strip()
        loc = str(item.get("location", "")).strip()
        title = str(item.get("title", "")).strip()
        ebay_lst = item.get("ebay_listing") or {}
        ebay_status = str(ebay_lst.get("status", "")).lower().strip()
        listing_id = ebay_lst.get("listing_id", "")
        listing_url = ebay_lst.get("listing_url", "")
        live_price = ebay_lst.get("live_price") or item.get("ebay_offer", {}).get("price")

        if location and loc.lower() != location.lower():
            continue
        if raw_status in _CLEAR_STATUS:
            continue

        entry: Dict[str, Any] = {
            "sku": sku,
            "title": title,
            "location": loc,
            "status": raw_status or "(empty)",
            "ebay_status": ebay_status or "(none)",
            "listing_id": listing_id,
            "listing_url": listing_url,
            "price": live_price,
        }

        if "A" in selected and ebay_status == "active" and raw_status not in ("in stock",):
            results["A"].append(entry)
        elif "B" in selected and raw_status == "out of stock" and not listing_id:
            results["B"].append(entry)
        elif "C" in selected and not raw_status and not listing_id:
            results["C"].append(entry)

    # Apply per-group limit
    if limit:
        for g in results:
            results[g] = results[g][:limit]

    total = sum(len(v) for v in results.values())
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: List[str] = [
        f"# eBay Physical Inventory Sweep — {ts}",
        f"Groups: {groups}" + (f"  |  Location filter: {location}" if location else ""),
        f"Total items: {total}",
        "",
    ]

    _GROUP_DESC = {
        "A": ("Active eBay listing — local status unclear", 'Check shelf. Present → `tgw update <SKU> status "in stock"` | Missing → likely sold; check eBay order history'),
        "B": ('Legacy "out of stock" — no eBay listing', "Check shelf. Present → `tgw update <SKU> status available` | Missing → `tgw update <SKU> status sold` (or use import-sold-csv)"),
        "C": ("No status, no eBay listing — completely uncategorized", "Assess: still have it? list it? already gone?"),
    }

    for g in ("A", "B", "C"):
        items = results.get(g, [])
        if not items:
            continue
        title_str, action_str = _GROUP_DESC[g]
        lines += [
            f"## Group {g} — {title_str} ({len(items)})",
            f"*{action_str}*",
            "",
            "| Done | SKU | Status | eBay | Loc | Price | Title |",
            "|------|-----|--------|------|-----|-------|-------|",
        ]
        for it in items:
            price_str = f"${it['price']}" if it["price"] else ""
            url_str = f"[{it['listing_id']}]({it['listing_url']})" if it["listing_id"] else ""
            title_col = it["title"][:45].replace("|", "/") if it["title"] else "—"
            lines.append(f"| [ ] | {it['sku']} | {it['status']} | {url_str or it['ebay_status']} | {it['location'] or '—'} | {price_str} | {title_col} |")
        lines.append("")

    content = "\n".join(lines)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        print(f"Sweep report written to {output}  ({total} items)")
    else:
        print(content)

    counts = {g: len(v) for g, v in results.items()}
    return {"ok": True, "total": total, "groups": counts, "output": str(output) if output else None}


def cmd_price_freeship(
    cfg: Dict[str, Any],
    sku: str,
    *,
    shipping_cost: Optional[float] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    """
    Compute the free-shipping listing price for one item (PP-FREESHIP-001).

    Sums ``ebay_offer.price`` (or ``draft_listing.price``) with the item's
    shipping cost and rounds to the nearest .99.  With ``--apply``, writes
    the combined price back and sets ``free_shipping: true`` on the item.

    Shipping cost precedence: --shipping-cost arg > item.shipping_cost > config default_shipping_cost.
    """
    from tgw.config import sku_json
    from tgw.ebay.pricing import freeship_price as _freeship_price
    from tgw.items import atomic_write_json

    json_path = sku_json(cfg, sku)
    if not json_path.exists():
        return {"ok": False, "error": f"SKU not found: {sku}"}

    item = json.loads(json_path.read_text(encoding="utf-8"))

    # Resolve base price (offer price takes priority over draft price)
    base_price: Optional[float] = None
    offer = item.get("ebay_offer") or {}
    draft = item.get("draft_listing") or {}
    for src in (offer.get("price"), draft.get("price")):
        if src is not None:
            try:
                base_price = float(src)
                break
            except (TypeError, ValueError):
                pass

    if base_price is None:
        return {
            "ok": False,
            "error": f"{sku}: no price set — run ebay_price or set a price first",
        }

    # Resolve shipping cost
    if shipping_cost is not None:
        ship_cost = float(shipping_cost)
        ship_source = "arg"
    elif item.get("shipping_cost") not in (None, ""):
        try:
            ship_cost = float(item["shipping_cost"])
            ship_source = "item"
        except (TypeError, ValueError):
            ship_cost = 0.0
            ship_source = "item_invalid"
    else:
        ship_cost = float(cfg.get("default_shipping_cost", 0.0))
        ship_source = "config_default"

    combined = _freeship_price(base_price, ship_cost)

    result: Dict[str, Any] = {
        "ok": True,
        "sku": sku,
        "base_price": round(base_price, 2),
        "shipping_cost": round(ship_cost, 2),
        "shipping_cost_source": ship_source,
        "freeship_price": combined,
        "applied": False,
    }

    if apply:
        if offer.get("freeship_applied_at"):
            return {
                "ok": False,
                "error": "free_shipping already applied (freeship_applied_at is set); "
                         "remove ebay_offer.freeship_applied_at to reapply",
                "sku": sku,
            }
        offer_block = dict(offer)
        offer_block["price"] = combined
        offer_block["freeship_applied_at"] = datetime.now(timezone.utc).isoformat()
        item["ebay_offer"] = offer_block
        if draft:
            draft["price"] = combined
            item["draft_listing"] = draft
        item["free_shipping"] = True
        atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))
        try:
            import psycopg2.errors  # noqa: PLC0415

            from tgw.queue import state_machine as _sm
            _sm.init(cfg["postgres_dsn"])
            _sm.enqueue_job(
                queue_name="catalog_rebuild",
                payload={"reason": f"price_freeship:{sku}"},
                dedupe_key="catalog_rebuild:pending",
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass
        except Exception as exc:
            result["warning"] = f"catalog_rebuild enqueue failed: {exc}"
        result["applied"] = True

    return result


def cmd_reprice_suggest(
    cfg: Dict[str, Any],
    *,
    skus: Optional[List[str]] = None,
    location: str = "",
    status: str = "",
    search: str = "",
    limit: int = 0,
) -> Dict[str, Any]:
    """
    Read-only price suggestions for items (PP-REPRICER-001).

    Blends own-sales velocity + active Browse comps into a suggested price and
    a reduce/hold/raise recommendation.  NEVER writes to eBay — this is the
    dry-run foundation; the writing repricer is blocked on the
    buy.marketplace_insights scope.
    """
    from .config import sku_json
    from .ebay.market_data import reprice_suggest
    from .resolver import resolve

    if skus:
        target = sorted(set(skus))
    else:
        sel: Dict[str, Any] = {}
        if location:
            sel["location"] = location
        if status:
            sel["status"] = status
        if search:
            sel["search"] = search
        target = sorted(resolve(cfg, **sel)) if sel else []

    if not target:
        return {"ok": True, "count": 0, "items": [], "note": "no items matched (give SKU(s) or a --location/--status/--search filter)"}

    if limit > 0:  # negative would slice from the end — treat as "no cap"
        target = target[:limit]

    rows: List[Dict[str, Any]] = []
    for sku in target:
        jf = sku_json(cfg, sku)
        if not jf.exists():
            rows.append({"ok": False, "sku": sku, "error": "item not found"})
            continue
        try:
            item = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as exc:
            rows.append({"ok": False, "sku": sku, "error": str(exc)})
            continue
        item.setdefault("sku", sku)
        rows.append(reprice_suggest(cfg, item))

    return {"ok": True, "count": len(rows), "items": rows, "applied": False}


def cmd_bulk(
    cfg: Dict[str, Any],
    *,
    field: str,
    value: str,
    skus: Optional[List[str]] = None,
    location: str = "",
    status: str = "",
    search: str = "",
    limit: int = 0,
    apply: bool = False,
) -> Dict[str, Any]:
    """
    Bulk-edit one field across matched items (PP-BULKEDIT-001).

    Dry-run (preview) by default; pass apply=True to write.  Mirrors the web
    /form/bulk flow.  Enqueues a catalog rebuild after a successful apply.
    """
    from .items import bulk_edit

    selectors: Dict[str, Any] = {}
    if skus:
        selectors["skus"] = list(skus)
    if location:
        selectors["location"] = location
    if status:
        selectors["status"] = status
    if search:
        selectors["search"] = search
    if not selectors:
        return {"ok": False, "error": "no selector given — pass SKU(s) or --location/--status/--search"}

    result = bulk_edit(cfg, selectors, field, value, apply=apply, limit=limit)

    # Gate the rebuild on count (writes happened), NOT ok — a partial-success
    # run (some failed) still changed N item JSONs and must refresh the catalog.
    if apply and result.get("count"):
        try:
            from .queue import state_machine as _sm

            _sm.init(cfg["postgres_dsn"])
            _sm.enqueue_job(
                queue_name="catalog_rebuild",
                payload={"reason": "bulk_edit"},
                dedupe_key="catalog_rebuild:pending",
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except Exception:
            pass

    return result


def cmd_suggest(cfg: Dict[str, Any], text: str) -> Dict[str, Any]:
    suggestions_file = cfg["plan_vault_path"] / "suggestions" / "SUGGESTIONS.md"
    suggestions_file.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")
    line = f"- [ ] {ts} :: {text}\n"
    with suggestions_file.open("a", encoding="utf-8") as f:
        f.write(line)
    return {"ok": True, "written": line.strip(), "file": str(suggestions_file)}


def cmd_suggest_edit(cfg: Dict[str, Any], pending_only: bool = False) -> Dict[str, Any]:
    """Open SUGGESTIONS.md (or a temp file of pending entries) in $EDITOR."""
    suggestions_file = cfg["plan_vault_path"] / "suggestions" / "SUGGESTIONS.md"
    if not suggestions_file.exists():
        return {"ok": False, "error": f"suggestions file not found: {suggestions_file}"}

    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))

    if pending_only:
        import tempfile

        lines = suggestions_file.read_text(encoding="utf-8").splitlines(keepends=True)
        pending = [ln for ln in lines if ln.startswith("- [ ]")]
        if not pending:
            print("No pending (unprocessed) suggestions found.")
            return {"ok": True, "pending_count": 0}
        with tempfile.NamedTemporaryFile("w", suffix="-suggestions.md", delete=False, encoding="utf-8") as f:
            f.writelines(pending)
            tmp = f.name
        print(f"{len(pending)} pending suggestion(s) — editing in {editor}")
        print(f"(edits saved to temp file; copy back to {suggestions_file} if desired)")
        os.execlp(editor, editor, tmp)

    print(f"Opening {suggestions_file} in {editor}")
    os.execlp(editor, editor, str(suggestions_file))
    return {"ok": True}  # unreachable if execlp succeeds


def cmd_classify_suggestions(
    cfg: Dict[str, Any],
    apply: bool = False,
    limit: int = 0,
) -> Dict[str, Any]:
    """Batch-classify unprocessed SUGGESTIONS.md entries via LLM (PP-DOCFLOW-001 Phase 2).

    tgw classify-suggestions [--apply] [--limit N]

    Default (dry-run): prints a classification report without modifying anything.
    --apply: marks already-done entries [x] in SUGGESTIONS.md and creates todos for
             new-work entries. plan_append and review_flag are listed in report only.
    """
    from tgw import suggestions as sug_mod

    suggestions_path = cfg['plan_vault_path'] / 'suggestions' / 'SUGGESTIONS.md'
    master_plan_path: Path = cfg['plan_master_path']

    entries = sug_mod.parse_pending(suggestions_path)
    if not entries:
        print('No unprocessed suggestions found.')
        return {'ok': True, 'total': 0}

    if limit and limit > 0:
        entries = entries[:limit]

    print(f'Classifying {len(entries)} pending suggestion(s)...')

    plan_text = master_plan_path.read_text(encoding='utf-8') if master_plan_path.exists() else ''
    plan_headings = '\n'.join(ln for ln in plan_text.splitlines() if ln.startswith('#'))

    classified = sug_mod.classify_batch(entries, plan_headings, cfg)
    if not classified:
        print('LLM returned no classifications.')
        return {'ok': False, 'error': 'empty_response', 'total': len(entries)}

    result = sug_mod.apply_classifications(suggestions_path, entries, classified, write=apply)
    print(sug_mod.format_report(result, applied=apply))
    return result


def cmd_quiet_check(cfg: Dict[str, Any], *, notify_on_idle: bool = False, kdc_device: str = "") -> Dict[str, Any]:
    """
    'Workers finished — what next?' nudge (PP-CAPTURE-001).

    When the pipeline is idle (no active jobs in any queue), surface the count of
    pending suggestions ([ ] entries in SUGGESTIONS.md) and open operator TODOs so
    ideas don't escape into ephemeral chat. Read-only over PostgreSQL + the vault.

    kdc_device: KDE Connect device id or name; when non-empty and pipeline is idle,
        pushes the summary to the phone via send_text(). Errors are swallowed.
    """
    import re

    from .queue import state_machine

    state_machine.init(cfg["postgres_dsn"])
    depths = state_machine.active_depths()
    active_total = sum(depths.values())
    quiet = active_total == 0

    state_summary = state_machine.queue_state_summary()

    pending_suggestions = 0
    sfile = cfg["plan_vault_path"] / "suggestions" / "SUGGESTIONS.md"
    if sfile.exists():
        pending_suggestions = sum(1 for ln in sfile.read_text(encoding="utf-8").splitlines() if re.match(r"\s*-\s*\[ \]", ln))

    open_todos = 0
    try:
        from . import todo

        open_todos = len(todo.todo_list())
    except Exception:
        open_todos = 0  # DB unavailable — degrade gracefully

    result = {
        "ok": True,
        "quiet": quiet,
        "active_total": active_total,
        "active_by_queue": depths,
        "queued": state_summary["queued"],
        "processing": state_summary["processing"],
        "dead_letter": state_summary["dead_letter"],
        "pending_suggestions": pending_suggestions,
        "open_todos": open_todos,
    }

    if quiet:
        msg = f"Pipeline idle — {pending_suggestions} pending suggestion(s), {open_todos} open TODO(s)."
        print(msg)
        result["message"] = msg
        if notify_on_idle:
            try:
                from .notify import notify

                notify("quiet-check", msg, level="info")
            except Exception:
                pass
        if kdc_device:
            try:
                from .apis.kdeconnect import get_device_id, send_text

                device_id = get_device_id(kdc_device) or kdc_device
                ok = send_text(device_id, msg)
                result["kdeconnect_pushed"] = ok
            except Exception as exc:
                result["kdeconnect_pushed"] = False
                result["kdeconnect_error"] = str(exc)
    else:
        print(f"Pipeline busy — {active_total} active job(s) across {len(depths)} queue(s).")

    return result


def _parse_prompt_section(text: str) -> str:
    """Extract the body under a `## Prompt` heading, up to the next `##`/`---`."""
    import re

    out: List[str] = []
    capture = False
    for ln in text.splitlines():
        if not capture:
            if re.match(r"^##\s+Prompt\b", ln, re.IGNORECASE):
                capture = True
            continue
        if re.match(r"^##\s", ln) or re.match(r"^---\s*$", ln):
            break
        out.append(ln)
    return "\n".join(out).strip()


def cmd_perp_run(cfg: Dict[str, Any], brief_id: Optional[str] = None, list_briefs: bool = False) -> Dict[str, Any]:
    """
    Load a Perplexity research brief's prompt to the clipboard (PP-PERP-AUTO-001).

    Cuts the mechanical open-brief / find-prompt / copy step to one command for
    the existing research briefs under the vault's perplexity/ dir. Pushes the
    `## Prompt` body to the clipboard (degrades to stdout when no clipboard tool).
    """
    perp_dir = cfg["plan_vault_path"] / "perplexity"
    if not perp_dir.exists():
        return {"ok": False, "error": f"perplexity dir not found: {perp_dir}"}

    briefs = sorted(perp_dir.glob("*.md"))

    if list_briefs or not brief_id:
        ids = [p.stem for p in briefs]
        for i in ids:
            print(i)
        return {"ok": True, "count": len(ids), "briefs": ids}

    needle = brief_id.lower()
    matches = [p for p in briefs if needle in p.stem.lower()]
    if not matches:
        return {"ok": False, "error": f"no brief matching {brief_id!r}"}
    if len(matches) > 1:
        return {"ok": False, "error": f"ambiguous {brief_id!r}", "matches": [p.stem for p in matches]}

    brief = matches[0]
    prompt = _parse_prompt_section(brief.read_text(encoding="utf-8"))
    if not prompt:
        return {"ok": False, "error": f'no "## Prompt" section in {brief.name}'}

    clipboard_ok = _push_clipboard(prompt)
    print(prompt)
    return {"ok": True, "brief": brief.stem, "clipboard": clipboard_ok, "prompt_chars": len(prompt)}


def _clean_transcript(raw: str) -> str:
    """Collapse whisper-cli stdout into a single suggestion line."""
    import re

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def cmd_whisper_to_suggest(cfg: Dict[str, Any], wavfile: str, model: Optional[str] = None) -> Dict[str, Any]:
    """
    Voice → suggestion (PP-WHISPER-001).

    Normalize an audio file to 16 kHz mono via ffmpeg, transcribe it with
    whisper-cli, and file the transcript through the existing cmd_suggest sink —
    zero-friction voice capture during hands-full item processing. Fails
    gracefully (clear message) if the ggml model is not present on disk.
    """
    import subprocess
    import tempfile

    wav = Path(wavfile)
    if not wav.exists():
        return {"ok": False, "error": f"audio file not found: {wavfile}"}

    whisper_bin = cfg.get("whisper_bin", "/usr/local/bin/whisper-cli")
    model_path = Path(model or cfg.get("whisper_model", "/opt/TGW/models/ggml-base.en.bin"))
    if not model_path.exists():
        return {"ok": False, "error": f"whisper model not found: {model_path} — operator must download it"}

    with tempfile.TemporaryDirectory() as td:
        norm = Path(td) / "norm.wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav), "-ar", "16000", "-ac", "1", str(norm)],
                check=True,
                capture_output=True,
            )
        except Exception as exc:
            return {"ok": False, "error": f"ffmpeg normalize failed: {exc}"}
        try:
            proc = subprocess.run(
                [whisper_bin, "-m", str(model_path), "-f", str(norm), "-nt"],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            return {"ok": False, "error": f"whisper-cli failed: {exc}"}

    transcript = _clean_transcript(proc.stdout or "")
    if not transcript:
        return {"ok": False, "error": "empty transcript"}

    result = cmd_suggest(cfg, transcript)
    result["transcript"] = transcript
    return result


def cmd_claude_help(cfg: Dict[str, Any], *, issue: str = "", worker: str = "", launch: bool = False) -> Dict[str, Any]:
    """
    Launch (or print) a Claude troubleshooting session preloaded with TGW context
    (PP-CLAUDE-HELP-001).

    Builds a `claude --append-system-prompt-file CLAUDE-TROUBLESHOOT.md` invocation
    with the repo added as context and the operator's issue/worker as initial
    prompt. Prints the ready-to-run command by default; --launch execs it.
    """
    import shlex
    import shutil

    root = Path(__file__).resolve().parents[2]
    doc = root / "CLAUDE-TROUBLESHOOT.md"
    if not doc.exists():
        return {"ok": False, "error": f"troubleshoot doc not found: {doc}"}

    claude_bin = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    cmd = [claude_bin, "--append-system-prompt-file", str(doc), "--add-dir", str(root)]

    ctx = []
    if worker:
        ctx.append(f"Focus on the {worker} worker.")
    if issue:
        ctx.append(issue)
    initial = " ".join(ctx).strip()
    if initial:
        cmd.append(initial)

    if launch:
        if not Path(claude_bin).exists() and not shutil.which("claude"):
            return {"ok": False, "error": "claude CLI not found on PATH"}
        os.execvp(cmd[0], cmd)  # replaces this process — not reached in tests

    print(" ".join(shlex.quote(c) for c in cmd))
    return {"ok": True, "doc": str(doc), "command": cmd, "issue": issue, "worker": worker}


def cmd_mvitems(
    cfg: Dict[str, Any],
    to_location: str,
    skus: List[str],
    from_location: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    check_only: bool = False,
) -> Dict[str, Any]:
    """Move items to to_location. Selects by explicit SKUs, --from, --search, or --status."""
    import time as _time

    started = _time.time()

    # Build the full target set
    target: Set[str] = set(skus)

    if from_location:
        target |= resolve(cfg, location=from_location)
    if search:
        target |= resolve(cfg, search=search)
    if status:
        target |= resolve(cfg, status=status)

    if not target:
        return {"ok": True, "to_location": to_location, "moved": [], "count": 0, "elapsed_seconds": 0.0, "note": "no items matched the given selectors"}

    moved: List[str] = []
    failed: List[Dict[str, Any]] = []

    for sku in sorted(target):
        if check_only:
            moved.append(sku)
            continue
        try:
            result = locationupdate(cfg, sku, to_location)
            if result.get("ok"):
                moved.append(sku)
            else:
                failed.append({"sku": sku, "error": result.get("error", "unknown")})
        except Exception as e:
            failed.append({"sku": sku, "error": str(e)})

    return {
        "ok": len(failed) == 0,
        "to_location": to_location,
        "moved": moved,
        "failed": failed,
        "count": len(moved),
        "elapsed_seconds": round(_time.time() - started, 3),
        "check_only": check_only,
    }


def _cmd_alt_text_batch(cfg: Dict[str, Any], args: Any) -> Dict[str, Any]:
    """Enqueue alt_text jobs for all eligible items in the catalog."""
    import psycopg2.errors

    from tgw.queue import state_machine

    limit = args.limit
    dry_run = args.dry_run
    filter_status = args.status.strip()

    itemdata_root = Path(cfg["itemdata_root"])
    enqueued = 0
    skipped = 0
    eligible = 0

    for sku_dir in sorted(itemdata_root.iterdir()):
        if not sku_dir.is_dir():
            continue
        sku = sku_dir.name
        json_path = sku_dir / f"{sku}.json"
        if not json_path.exists():
            continue

        try:
            item = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Filter by status if requested
        if filter_status:
            status = str(item.get("#STATUS") or item.get("status") or "")
            if status != filter_status:
                continue

        # Skip if already has alt_text
        if item.get("draft_listing", {}).get("alt_text"):
            skipped += 1
            continue

        # Skip if no photo
        from tgw.alt_text import _ALT_STEM_SUFFIX, _primary_image

        alt_path = sku_dir / f"{sku}{_ALT_STEM_SUFFIX}.jpg"
        if alt_path.exists():
            skipped += 1
            continue
        if _primary_image(sku_dir) is None:
            skipped += 1
            continue

        eligible += 1
        if limit and eligible > limit:
            break

        if not dry_run:
            try:
                state_machine.enqueue_job(
                    queue_name="alt_text",
                    payload={"sku": sku},
                    dedupe_key=f"alt_text:{sku}",
                    max_attempts=3,
                )
                enqueued += 1
            except psycopg2.errors.UniqueViolation:
                skipped += 1

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "eligible": eligible,
            "skipped_already_done": skipped,
            "would_enqueue": eligible,
            "note": "run without --dry-run to enqueue",
        }
    return {
        "ok": True,
        "enqueued": enqueued,
        "skipped_already_done": skipped,
        "eligible_found": eligible,
    }


def cmd_set_template(
    cfg: Dict[str, Any],
    *,
    group_key: Optional[str] = None,
    sku: Optional[str] = None,
    list_groups: bool = False,
    camera_only: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Apply category group defaults to an item (PP-INTAKE-001 Phase 1).

    Writes category_group, ai_hint (prepended), size_class, and
    ebay_category_id (if not already set) to the item JSON.
    Optionally pushes SETTEMPLATE: to the clipboard for KDE Connect relay.
    """
    from .config import sku_json
    from .ebay.pricing import _load_groups
    from .items import atomic_write_json
    from .resolver import load_item_doc

    groups_data = _load_groups(cfg)
    groups = groups_data.get("groups", {})

    # --list: show all available templates
    if list_groups:
        rows = []
        for key, grp in groups.items():
            pricing = grp.get("pricing", {})
            rows.append(
                {
                    "key": key,
                    "name": grp["name"],
                    "size_class": grp.get("size_class", ""),
                    "ai_hint": grp.get("ai_hint", "")[:60],
                    "cats": len(grp.get("ebay_categories", [])),
                    "typical": pricing.get("typical_used"),
                }
            )
        print(f"{'Key':<35} {'Name':<30} {'Size':<12} {'Cats':>4}  {'Typical':>8}  AI Hint")
        print("-" * 110)
        for r in rows:
            typ = f"${r['typical']:.2f}" if r["typical"] else "   N/A"
            print(f"{r['key']:<35} {r['name']:<30} {r['size_class']:<12} {r['cats']:>4}  {typ:>8}  {r['ai_hint']}")
        print(f"\n{len(rows)} templates  |  file: {cfg['category_groups_path']}")
        return {"ok": True, "count": len(rows)}

    # --camera GROUP_KEY: push SETTEMPLATE: to clipboard only, no JSON update
    if camera_only:
        key = camera_only
        grp = groups.get(key)
        if not grp:
            return {"ok": False, "error": f"unknown group key: {key!r}"}
        template_str = f"SETTEMPLATE:{grp['name']}"
        _push_clipboard(template_str)
        return {"ok": True, "clipboard": template_str, "json_updated": False}

    # Resolve group
    if not group_key:
        return {"ok": False, "error": "group_key required (or use --list / --camera)"}
    grp = groups.get(group_key)
    if not grp:
        available = ", ".join(sorted(groups.keys()))
        return {"ok": False, "error": f"unknown group key: {group_key!r}", "available": available}

    # Resolve SKU
    if not sku:
        from .context import current_sku as _current_sku

        sku = _current_sku(cfg)
        if not sku:
            return {"ok": False, "error": "no SKU provided and no current-item context set (use tgw set-context <sku>)"}

    json_path = sku_json(cfg, sku)
    if not json_path.exists():
        return {"ok": False, "error": f"item not found: {sku}"}

    if dry_run:
        fields = _build_template_fields(cfg, grp, group_key, {})
        return {"ok": True, "sku": sku, "group_key": group_key, "group_name": grp["name"], "dry_run": True, "would_write": fields}

    doc = load_item_doc(json_path)
    fields = _build_template_fields(cfg, grp, group_key, doc)
    doc.update(fields)
    atomic_write_json(json_path, doc, pretty=True)

    # Push SETTEMPLATE: to clipboard for KDE Connect camera relay
    template_str = f"SETTEMPLATE:{grp['name']}"
    clipboard_ok = _push_clipboard(template_str)

    return {
        "ok": True,
        "sku": sku,
        "group_key": group_key,
        "group_name": grp["name"],
        "fields": fields,
        "clipboard": template_str if clipboard_ok else None,
    }


def _build_template_fields(cfg: Dict[str, Any], grp: Dict[str, Any], group_key: str, existing: Dict[str, Any]) -> Dict[str, Any]:
    """Build the dict of fields to write for a template application."""
    fields: Dict[str, Any] = {"category_group": group_key}

    if grp.get("size_class"):
        fields["size_class"] = grp["size_class"]

    # Prepend group ai_hint to any existing hint
    group_hint = grp.get("ai_hint", "").strip()
    if group_hint:
        existing_hint = existing.get("ai_hint", "").strip()
        if existing_hint and existing_hint != group_hint:
            fields["ai_hint"] = f"{group_hint}; {existing_hint}"
        else:
            fields["ai_hint"] = group_hint

    # Set first category if not already assigned
    cats = grp.get("ebay_categories", [])
    if cats and not existing.get("ebay_category_id"):
        fields["ebay_category_id"] = cats[0]

    return fields


def _generate_sku(ts: datetime) -> str:
    """Generate a canonical SKU: tgwYYYYMMDDHHMMSSs (18 chars, tenths-of-second)."""
    return ts.strftime("tgw%Y%m%d%H%M%S") + f"{ts.microsecond // 100000:01d}"


def cmd_create_item(
    cfg: Dict[str, Any],
    *,
    template: Optional[str] = None,
    count: int = 1,
    dry_run: bool = False,
    _now_fn=None,
) -> Dict[str, Any]:
    """Pre-create SKU folder(s) + blank JSON with template applied; push COMMAND:SKU to phone (PP-INTAKE-001 Phase 2.5)."""
    import time as _time
    from datetime import datetime as _dt

    from .ebay.pricing import _load_groups
    from .items import create_item as _create_item

    if _now_fn is None:
        _now_fn = _dt.now

    if count < 1 or count > 20:
        return {"ok": False, "error": f"count must be 1–20, got {count}"}

    # Resolve template group
    grp: Optional[Dict[str, Any]] = None
    group_key: Optional[str] = None
    if template:
        groups = _load_groups(cfg).get("groups", {})
        grp = groups.get(template)
        if not grp:
            available = ", ".join(sorted(groups.keys()))
            return {"ok": False, "error": f"unknown template: {template!r}", "available": available}
        group_key = template

    # Generate N unique SKUs (small sleep between each to guarantee ms differs)
    seen_skus: set = set()
    planned: List[Dict[str, Any]] = []
    for i in range(count):
        if i > 0:
            _time.sleep(0.002)
        while True:
            ts = _now_fn()
            sku = _generate_sku(ts)
            if sku not in seen_skus:
                seen_skus.add(sku)
                break
            _time.sleep(0.001)

        item_data: Dict[str, Any] = {"#STATUS": "New"}
        if grp and group_key:
            item_data.update(_build_template_fields(cfg, grp, group_key, {}))
        planned.append({"sku": sku, "data": item_data})

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_create": [d["sku"] for d in planned],
            "count": count,
            "template": template,
        }

    # Create items
    created_skus: List[str] = []
    errors: List[Dict[str, Any]] = []
    for d in planned:
        sku = d["sku"]
        try:
            _create_item(cfg, sku, d["data"])
            created_skus.append(sku)
        except FileExistsError:
            errors.append({"sku": sku, "error": "already exists"})
        except Exception as exc:
            errors.append({"sku": sku, "error": str(exc)})

    if not created_skus:
        return {"ok": False, "error": "no items created", "errors": errors}

    # Set context to first created SKU
    first_sku = created_skus[0]
    ctx_result = set_context(cfg, first_sku, set_by="create-item")

    # Push COMMAND:SKU to phone via KDE Connect (fail-soft)
    kdc_result: Dict[str, Any] = {"pushed": False}
    kdc_device = cfg.get("kdeconnect_device_id", "")
    if kdc_device:
        try:
            from .apis.kdeconnect import get_device_id
            from .apis.kdeconnect import send_text as _send_text

            device_id = get_device_id(kdc_device) or kdc_device
            msg = f"COMMAND:SKU:{first_sku}"
            ok = _send_text(device_id, msg)
            kdc_result = {"pushed": ok, "device": kdc_device, "text": msg}
        except Exception as exc:
            kdc_result = {"pushed": False, "error": str(exc)}

    result: Dict[str, Any] = {
        "ok": True,
        "created": created_skus,
        "count": len(created_skus),
        "template": template,
        "context_set": ctx_result.get("ok", False),
        "kdeconnect": kdc_result,
    }
    if errors:
        result["errors"] = errors
    return result


def _current_item_sku() -> Optional[str]:
    """Legacy shim — reads CurrentItem symlink.  New code should use context.current_sku(cfg)."""
    from .context import _sku_from_symlink

    return _sku_from_symlink()


def _push_clipboard(text: str) -> bool:
    """Push text to the system clipboard via pyperclip."""
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def main() -> int:
    argv = ['--' + a[1:] if a == '-help' else a for a in sys.argv[1:]]
    parser = _build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(Path(os.path.expanduser(args.config)))
    check = getattr(args, "check_only", False)

    try:
        if args.op == "get":
            result = get_item(cfg, args.sku)

        elif args.op == "search":
            result = list_items(cfg, search=args.text, location=args.location, status=args.status, limit=args.limit, empty_field=args.empty_field)
            if args.skus_only:
                for item in result["items"]:
                    print(item.get("sku", ""))
                return 0

        elif args.op == "list":
            result = list_items(cfg, search=args.search, location=args.location, status=args.status, limit=args.limit, date_from=args.date_from, date_to=args.date_to, search_field=args.search_field)
            if args.skus_only:
                for item in result["items"]:
                    print(item.get("sku", ""))
                return 0

        elif args.op == "resolve":
            sel: Dict[str, Any] = {}
            if args.sku:
                sel["sku"] = args.sku
            if args.location:
                sel["location"] = args.location
            if args.status:
                sel["status"] = args.status
            if args.date_from:
                sel["date_from"] = args.date_from
            if args.date_to:
                sel["date_to"] = args.date_to
            if args.ebay_item_id:
                sel["ebay_item_id"] = args.ebay_item_id
            if args.upc:
                sel["upc"] = args.upc
            if args.search:
                sel["search"] = args.search
            skus = resolve(cfg, **sel)
            if args.skus_only:
                for sku in sorted(skus):
                    print(sku)
                return 0
            result = {"ok": True, "selectors": sel, "count": len(skus), "skus": sorted(skus)}

        elif args.op == "update":
            result = update_item(cfg, args.sku, args.field, args.value, check_only=check)

        elif args.op == "update-where":
            sel = {}
            if args.location:
                sel["location"] = args.location
            if args.status:
                sel["status"] = args.status
            if args.date_from:
                sel["date_from"] = args.date_from
            if args.date_to:
                sel["date_to"] = args.date_to
            if args.search:
                sel["search"] = args.search
            result = update_where(cfg, sel, args.field, args.value, check_only=check)

        elif args.op in ("update-title", "titleupdate"):
            result = titleupdate(cfg, args.sku, args.value, check_only=check)

        elif args.op in ("update-location", "locationupdate"):
            result = locationupdate(cfg, args.sku, args.location, check_only=check)

        elif args.op in ("update-verified", "verifiedupdate"):
            result = verifiedupdate(cfg, args.sku, args.value, check_only=check)

        elif args.op in ("update-status", "statusupdate"):
            results = [statusupdate(cfg, sku, args.value, check_only=check) for sku in _expand_skus(args.skus)]
            if len(results) == 1:
                result = results[0]
            else:
                ok = all(r.get("ok") for r in results)
                result = {"ok": ok, "count": len(results), "results": results}

        elif args.op in ("set-shipping", "setshipping"):
            result = update_item(cfg, args.sku, "shipping_profile", args.value, check_only=check)

        elif args.op == "picklist":
            result = cmd_picklist(
                cfg,
                status=args.status,
                location=args.location,
                search=args.search,
                pdf=args.pdf,
                output=args.output,
            )

        elif args.op == "print-label":
            result = cmd_print_label(cfg, args.sku, output=args.output)

        elif args.op == "enqueue-sku":
            expanded = _expand_skus(args.skus)
            if not expanded:
                result = {"ok": False, "error": "no SKUs provided"}
            elif len(expanded) == 1:
                result = cmd_enqueue_sku(cfg, expanded[0], args.queue)
            else:
                results = [cmd_enqueue_sku(cfg, s, args.queue) for s in expanded]
                ok = all(r.get("ok") for r in results)
                result = {"ok": ok, "queue": args.queue, "count": len(results), "results": results}

        elif args.op == "quiet-check":
            kdc_dev = cfg.get("kdeconnect_device_id", "") if getattr(args, "kdc", False) else ""
            result = cmd_quiet_check(cfg, notify_on_idle=args.notify, kdc_device=kdc_dev)

        elif args.op == "perp-run":
            result = cmd_perp_run(cfg, brief_id=args.brief_id, list_briefs=args.list_briefs)

        elif args.op in ("whisper-suggest", "whispertosuggest"):
            result = cmd_whisper_to_suggest(cfg, args.wavfile, model=args.model)

        elif args.op == "claude-help":
            result = cmd_claude_help(cfg, issue=args.issue, worker=args.worker, launch=args.launch)

        elif args.op == "clip":
            from .clip import cmd_clip

            result = cmd_clip(args.clip_action, pattern=args.pattern, limit=args.limit, sku_only=args.sku_only)

        elif args.op == "catlocmvall":
            result = catlocmvall(cfg, args.from_location, args.to_location, check_only=check)

        elif args.op == "mvitems":
            result = cmd_mvitems(
                cfg,
                to_location=args.to_location,
                skus=_expand_skus(args.skus) if args.skus else [],
                from_location=args.from_location,
                search=args.search,
                status=args.status,
                check_only=args.check_only,
            )

        elif args.op == "suggest-edit":
            result = cmd_suggest_edit(cfg, pending_only=args.pending_only)

        elif args.op == "admin-file":
            from tgw.workers.pm_intake import cmd_admin_file
            result = cmd_admin_file(cfg, bypass_delay=getattr(args, "now", False))
            return 0 if result["ok"] else 1

        elif args.op == "classify-suggestions":
            result = cmd_classify_suggestions(cfg, apply=args.apply, limit=args.limit)
            return 0 if result.get("ok") else 1

        elif args.op == "ai-usage":
            result = cmd_ai_usage(cfg, since_days=args.since)
            if result.get("ok"):
                if getattr(args, "as_json", False):
                    print(json.dumps(result, indent=2, default=str))
                else:
                    _print_ai_usage_table(result["rows"], result["since_days"])
            else:
                print(f"error: {result.get('error', 'unknown error')}", file=sys.stderr)
            return 0 if result.get("ok") else 1

        elif args.op == "build-full":
            result = build_full_catalog(cfg, check_only=check)

        elif args.op == "build-search":
            result = build_search_catalog(cfg, source=args.source, check_only=check)

        elif args.op == "build-locations":
            result = build_location_tree(cfg, source=args.source, check_only=check)

        elif args.op == "build-full-csv":
            result = build_full_catalog_csv(cfg, check_only=check)

        elif args.op == "build-search-csv":
            result = build_search_catalog_csv(cfg, source=args.source, check_only=check)

        elif args.op == "build-sqlite":
            result = build_sqlite_catalog(cfg, check_only=check)

        elif args.op == "build-thumbnails":
            result = build_thumbnail_cache(cfg, check_only=check)

        elif args.op == "build-all":
            result = build_all_catalogs(cfg, check_only=check)

        elif args.op == "ensure-catalog":
            if cfg["search_catalog_path"].exists():
                result = {"ok": True, "exists": True, "path": str(cfg["search_catalog_path"])}
            else:
                result = build_search_catalog(cfg, source="auto", check_only=check)
        elif args.op in ("health", "status"):
            result = check_all(cfg, include_ollama=not args.no_ollama, include_ebay=not args.no_ebay)

        elif args.op == "help":
            _build_parser().print_help()
            return 0

        elif args.op == "quality":
            from .config import sku_json
            from .items import atomic_write_json
            from .listing_quality import score_draft

            rows = []
            for sku in _expand_skus(args.skus):
                json_path = sku_json(cfg, sku)
                if not json_path.exists():
                    rows.append({"sku": sku, "ok": False, "error": "item not found"})
                    continue
                try:
                    item = json.loads(json_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    rows.append({"sku": sku, "ok": False, "error": str(exc)})
                    continue
                q = score_draft(item)
                row: Dict[str, Any] = {"sku": sku, "ok": True, **q.to_dict()}
                if args.save and item.get("draft_listing") is not None:
                    item["draft_listing"]["quality"] = q.to_dict()
                    atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))
                    row["saved"] = True
                rows.append(row)
            result = {"ok": True, "items": rows}
            if not getattr(args, "as_json", False):
                print(f"{'SKU':<24} {'Score':>5}  {'Flags'}")
                print("-" * 70)
                for r in rows:
                    if not r["ok"]:
                        print(f"{r['sku']:<24}  ERR    {r.get('error', '')}")
                        continue
                    flags = ",".join(r.get("flags") or []) or "—"
                    print(f"{r['sku']:<24} {r['score']:>5}  {flags}")
                    bk = r.get("breakdown") or {}
                    parts = [f"{k}={v}" for k, v in bk.items()]
                    print(f"  {'  '.join(parts)}")
                return 0

        elif args.op == "lookup":
            from .apis.lookup import lookup_product
            from .config import sku_json
            from .items import atomic_write_json

            json_path = sku_json(cfg, args.sku)
            if not json_path.exists():
                result = {"ok": False, "error": f"item not found: {args.sku}"}
            else:
                item = json.loads(json_path.read_text(encoding="utf-8"))
                if args.force:
                    item.pop("product_lookup", None)
                lookup = lookup_product(item, cfg)
                if lookup is None:
                    result = {"ok": True, "sku": args.sku, "found": False, "note": "no barcode field (upc/ean/isbn) in item JSON"}
                else:
                    result = {"ok": True, "sku": args.sku, "found": True, "result": lookup.to_dict()}
                    if args.save:
                        item["product_lookup"] = lookup.to_dict()
                        atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))
                        result["saved"] = True

        elif args.op in ("suggest", "note", "btw"):
            result = cmd_suggest(cfg, " ".join(args.text))

        elif args.op == "hint":
            result = cmd_hint(cfg, args.sku, " ".join(args.text), force=args.force)

        elif args.op == "hint-trail":
            result = cmd_hint_trail(cfg, args.sku)

        elif args.op in ("requeue-identify", "requeue"):
            result = cmd_requeue(
                cfg,
                no_title=args.no_title,
                unidentified=args.unidentified,
                hint_set=args.hint_set,
                no_draft=args.no_draft,
                no_price=args.no_price,
                catalog_only=args.catalog_only,
                limit=args.limit,
                dry_run=not args.run,
            )

        elif args.op == "resolve-legacy":
            result = cmd_resolve_legacy(cfg, _expand_skus(args.skus), enqueue_stage=not args.no_stage)

        elif args.op == "bulk":
            result = cmd_bulk(
                cfg,
                field=args.field,
                value=args.value,
                skus=_expand_skus(args.skus) if args.skus else args.skus,
                location=args.location,
                status=args.status,
                search=args.search,
                limit=args.limit,
                apply=args.apply,
            )
            # Hard errors (bad selector / non-editable field) carry 'error' and
            # no 'applied'; a partial-success apply has applied=True + a failed
            # list and ok=False — show its summary rather than dumping JSON.
            if result.get("error"):
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                return 1
            if result.get("applied"):
                print(f"Applied {args.field}={args.value!r} to {result['count']} item(s).")
                if result.get("failed"):
                    print(f"Failed: {len(result['failed'])}")
                    for f in result["failed"]:
                        print(f"  {f['sku']}: {f.get('error')}")
            else:
                rows = result.get("preview", [])
                print(f"DRY RUN — would set {args.field}={args.value!r} on {len(rows)} item(s):")
                print(f"{'SKU':<24} {'Current':<28} Title")
                print("-" * 84)
                for r in rows:
                    cur = str(r["current"])[:26]
                    print(f"{r['sku']:<24} {cur:<28} {r['title'][:28]}")
                print("\nRe-run with --apply to write.")
            return 0 if result["ok"] else 1

        elif args.op == "reprice-suggest":
            result = cmd_reprice_suggest(
                cfg,
                skus=_expand_skus(args.skus) if args.skus else args.skus,
                location=args.location,
                status=args.status,
                search=args.search,
                limit=args.limit,
            )
            if getattr(args, "as_json", False) or not result["ok"]:
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                return 0 if result["ok"] else 1
            items = result["items"]
            if not items:
                print(result.get("note", "No items."))
                return 0
            print(f"{'SKU':<24} {'Cur$':>8} {'Sugg$':>8} {'Δ%':>6}  {'Rec':<7} Basis / rationale")
            print("-" * 96)
            for r in items:
                if not r.get("ok"):
                    print(f"{r.get('sku', ''):<24}  ERR    {r.get('error', '')}")
                    continue
                cur = f"${r['current_price']:.2f}" if r["current_price"] is not None else "     —"
                sug = f"${r['suggested_price']:.2f}" if r["suggested_price"] is not None else "     —"
                dpct = f"{r['delta_pct']:+.0f}%" if r["delta_pct"] is not None else "    —"
                print(f"{r['sku']:<24} {cur:>8} {sug:>8} {dpct:>6}  {r['recommendation']:<7} {r['rationale']}")
            print(f"\n{len(items)} item(s).  READ-ONLY — no eBay writes. Reduce/raise thresholds: -5% / +10%.")
            return 0

        elif args.op == "price-freeship":
            result = cmd_price_freeship(
                cfg,
                args.sku,
                shipping_cost=args.shipping_cost,
                apply=args.apply,
            )
            if result["ok"]:
                r = result
                action = "APPLIED" if r["applied"] else "DRY-RUN"
                print(
                    f"{action}: {r['sku']}  "
                    f"base=${r['base_price']:.2f} + ship=${r['shipping_cost']:.2f} "
                    f"({r['shipping_cost_source']})  → ${r['freeship_price']:.2f}"
                )
            return 0 if result["ok"] else 1

        elif args.op == "seo-audit":
            result = cmd_seo_audit(cfg, limit=args.limit, live_only=args.live_only)
            if not getattr(args, "as_json", False) and result["ok"]:
                items = result["items"]
                if not items:
                    print("No live or staged listings found.")
                else:
                    _PC = {"high": "H", "medium": "M", "low": "L", None: "—"}
                    _CC = {"high": "H", "medium": "M", "low": "!", None: "—"}
                    print(f"{'SKU':<24} {'Q':>3} {'PC'} {'CC'}  {'St'} {'Days':>4}  {'Issues':<28} {'Title'}")
                    print("-" * 100)
                    for it in items:
                        q = it.get("quality")
                        q_str = f"{q:3d}" if q is not None else "  —"
                        pc = _PC.get(it.get("price_confidence"), "?")
                        cc = _CC.get(it.get("category_confidence"), "—")
                        st = "L" if it["status"] == "live" else "S"
                        days = f"{it['days_listed']:4d}" if it["days_listed"] is not None else "   —"
                        issues = ",".join(it["seo_issues"])[:28]
                        print(f"{it['sku']:<24} {q_str} {pc:>2} {cc:>2}  {st}  {days}  {issues:<28} {it['title'][:30]}")
                    print(f"\n{len(items)} item(s). Q=quality PC=price-conf CC=cat-conf(!=low) St=L/S Days=days-listed")
                    print(f"Note: {result['note']}")
                return 0

        elif args.op == "velocity-report":
            result = cmd_velocity_report(
                cfg,
                category=args.category,
                refresh=args.refresh,
                min_sold=args.min_sold,
            )
            if args.json_out or not result["ok"]:
                out_text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
                if args.output:
                    Path(args.output).write_text(out_text, encoding="utf-8")
                    print(f"Written to {args.output}")
                else:
                    print(out_text)
            else:
                cats = result["categories"]
                lines = [
                    f"Velocity report  generated={result['generated_at']}  items={result['item_count']}  categories={len(cats)}",
                    "",
                    f"{'Cat ID':<10} {'Category Name':<30} {'Sold':>5} {'Active':>6} {'Stale':>5} {'Med$':>6} {'p25$':>6} {'Med Days':>8}  {'Launch%':>7} {'Retail%':>7} {'Move%':>6} {'?%':>5}",
                    "-" * 108,
                ]
                for cat_id, c in sorted(cats.items(), key=lambda x: x[1].get("sold_count", 0), reverse=True):
                    name = (c.get("category_name") or "")[:30]
                    med_p = f"${c['median_sale_price']:.2f}" if c.get("median_sale_price") else "   N/A"
                    p25_p = f"${c['p25_sale_price']:.2f}" if c.get("p25_sale_price") else "   N/A"
                    med_d = f"{c['median_days_to_sale']:.1f}" if c.get("median_days_to_sale") is not None else "   N/A"
                    launch = f"{c['sell_at_launch_pct'] * 100:.0f}%"
                    retail = f"{c['sell_at_retail_pct'] * 100:.0f}%"
                    move = f"{c['sell_at_move_pct'] * 100:.0f}%"
                    unknown = f"{c['sell_at_unknown_pct'] * 100:.0f}%"
                    lines.append(
                        f"{cat_id:<10} {name:<30} {c['sold_count']:>5} {c['active_count']:>6} {c['stale_count']:>5} {med_p:>6} {p25_p:>6} {med_d:>8}  {launch:>7} {retail:>7} {move:>6} {unknown:>5}"
                    )
                out_text = "\n".join(lines) + "\n"
                if args.output:
                    Path(args.output).write_text(out_text, encoding="utf-8")
                    print(f"Written to {args.output}")
                else:
                    print(out_text)
            return 0

        elif args.op == "staged":
            result = cmd_staged(cfg)
            if not getattr(args, "as_json", False) and result["ok"]:
                items = result["items"]
                if not items:
                    print("No items staged and awaiting review.")
                else:
                    _PC = {"high": "H", "medium": "M", "low": "L", None: "—"}
                    _CC = {"high": "H", "medium": "M", "low": "!", None: "—"}
                    print(f"{'SKU':<24} {'Q':>3} {'PC'} {'CC'}  {'Price':>7}  {'Location':<10} {'Title'}")
                    print("-" * 92)
                    for it in items:
                        price = f"${it['price']}" if it["price"] else "  N/A"
                        q = it.get("quality")
                        q_str = f"{q:3d}" if q is not None else "  —"
                        pc = _PC.get(it.get("price_confidence"), "?")
                        cc = _CC.get(it.get("category_confidence"), "—")
                        flags = it.get("quality_flags") or []
                        flag_str = f" [{','.join(flags[:3])}]" if flags else ""
                        print(f"{it['sku']:<24} {q_str} {pc:>2} {cc:>2}  {price:>7}  {it['location']:<10} {it['title'][:30]}{flag_str}")
                    ready_note = f" ({result.get('ready_count', 0)} more in the ready queue — tgw ready)" if result.get("ready_count") else ""
                    print(f"\n{len(items)} item(s) awaiting review{ready_note}. Q=quality 0–100  PC=price conf  CC=cat conf(!=low)")
                    print("Use: tgw ready set <SKU> (rate-limited dole-out) or tgw publish <SKU> (List Now)")
                return 0

        elif args.op == "publish":
            result = cmd_publish(cfg, _expand_skus(args.skus), dry_run=args.dry_run)

        elif args.op == "ready":
            from tgw.ready import cmd_ready

            result = cmd_ready(cfg, args.ready_op, _expand_skus(args.skus))
            # cmd_ready handles its own printing; skip the generic JSON dump
            return 0 if result.get("ok", True) else 1

        elif args.op == "setup-ebay-hooks":
            from .apis.ebay.notifications import get_notification_preferences, set_notification_preferences

            if args.check:
                current = get_notification_preferences(cfg)
                result = {"ok": True, "current_url": current or "(not set)"}
            else:
                set_notification_preferences(cfg, args.url)
                result = {"ok": True, "delivery_url": args.url, "note": "eBay will now POST FixedPriceTransaction events to this URL"}

        elif args.op == "serve":
            import uvicorn

            from .http_server import app

            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                reload=args.reload,
                log_level="info",
            )
            return 0

        elif args.op == "sku-migrate":
            from .queue import state_machine as _sm
            from .sku_migration import check_collisions, run_migration

            _sm.init(cfg["postgres_dsn"])

            if args.check_collisions:
                result = check_collisions(cfg)
            else:
                classes = [c.strip().upper() for c in args.classes.split(",") if c.strip()]
                dry_run = not args.run
                manifest_path: Optional[Path] = None
                if not dry_run:
                    if args.manifest:
                        manifest_path = Path(args.manifest)
                    else:
                        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
                        manifest_path = Path("/opt/TGW/var/log") / f"sku-migrate-{ts}.json"
                result = run_migration(
                    cfg,
                    classes=classes,
                    dry_run=dry_run,
                    include_live_ebay=args.include_live_ebay,
                    limit=args.limit,
                    manifest_path=manifest_path,
                )

        elif args.op == "ebay-sweep":
            result = cmd_ebay_sweep(
                cfg,
                groups=args.groups,
                location=args.location,
                limit=args.limit,
                output=Path(args.output) if args.output else None,
            )
            if args.output:
                print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1

        elif args.op == "build-archive-index":
            from .ebay.pull import build_archive_index

            archive_dir = Path(args.archive_dir)
            cache_path = Path(args.cache)
            if not archive_dir.exists():
                print(f"Archive dir not found: {archive_dir}")
                return 1
            # Force a fresh scan by removing cache first
            if cache_path.exists():
                cache_path.unlink()
            idx = build_archive_index(archive_dir, cfg["itemdata_root"], cache_path=cache_path)
            print(json.dumps({"ok": True, "entries": len(idx), "cache": str(cache_path)}, indent=2))
            return 0

        elif args.op == "history-index":
            from . import history_index as _hi

            target = args.target
            dry_run = args.dry_run
            limit = args.limit
            results: Dict[str, Any] = {}

            if target in ("ItemArchive", "all"):
                print("Indexing ItemArchive (no-eBay-ID zips)…", flush=True)
                stats = _hi.index_archive_unindexed(cfg, limit=limit, dry_run=dry_run)
                results["ItemArchive"] = stats
                n = stats["new"]
                print(f"  total_zips={stats['total_zips']} already_ebay={stats['already_ebay']} "
                      f"already_indexed={stats['already_indexed']} new={n} "
                      f"skipped_no_json={stats['skipped_no_json']}"
                      + (" (dry-run)" if dry_run else ""))
                if not dry_run and n:
                    print(f"  Written to: {stats.get('out_path')}")

            if target in ("loose-csv", "all"):
                print("Indexing loose eBay CSVs in history root…", flush=True)
                stats = _hi.index_loose_csvs(cfg, dry_run=dry_run)
                results["loose-csv"] = stats
                print(f"  files_scanned={stats['files_scanned']} records={stats['records']}"
                      + (" (dry-run)" if dry_run else ""))
                if not dry_run and stats.get("out_path"):
                    print(f"  Written to: {stats['out_path']}")

            print(json.dumps({"ok": True, "dry_run": dry_run, **results}, indent=2))
            return 0

        elif args.op == "import-sold-csv":
            from .queue import state_machine as _sm

            _sm.init(cfg["postgres_dsn"])
            result = cmd_import_sold_csv(cfg, Path(args.file), dry_run=args.dry_run, show_columns=args.show_columns, fuzzy=args.fuzzy, fuzzy_threshold=args.fuzzy_threshold)
            if result.get("ok") and not args.show_columns:
                marked = result.get("marked", 0)
                if marked and not args.dry_run:
                    try:
                        _sm.enqueue_job(
                            queue_name="catalog_rebuild",
                            payload={"reason": "import_sold_csv"},
                            dedupe_key="catalog_rebuild:pending",
                            not_before=time.time() + 30,
                            max_attempts=3,
                        )
                        print("catalog_rebuild job enqueued.")
                    except Exception:
                        pass

        elif args.op == "ebay-pull":
            from .ebay.pull import build_listing_index, sync_active_listings, sync_sold_orders
            from .queue import state_machine as _sm
            from .workers.ebay_legacy_sync import _sold_state_path

            _sm.init(cfg["postgres_dsn"])

            synced_at = datetime.now(tz=timezone.utc).isoformat()
            itemdata_root = cfg["itemdata_root"]
            dry_run = args.dry_run
            total_changes = 0

            active_stats: Dict[str, Any] = {}
            if not args.no_active:
                print("Fetching active listings from eBay...")
                active_stats = sync_active_listings(cfg, itemdata_root, synced_at, dry_run=dry_run)
                total_changes += active_stats.get("updated", 0)
                print(
                    f"  fetched={active_stats['fetched']}  matched={active_stats['matched']}  "
                    f"updated={active_stats['updated']}  orphaned={active_stats['orphaned']}  "
                    f"skipped_inventory={active_stats['skipped_inventory']}  "
                    f"errors={active_stats['errors']}"
                )
                for o in active_stats.get("orphans", []):
                    print(f"  ORPHAN: ItemID={o['listing_id']} label={o.get('custom_label', '')!r} title={o.get('title', '')[:60]}")

            sold_stats: Dict[str, Any] = {}
            if not args.no_sold:
                print("Fetching sold orders from eBay...")
                listing_index = build_listing_index(itemdata_root)
                print(f"  listing index: {len(listing_index)} entries")
                sold_stats = sync_sold_orders(cfg, listing_index, synced_at, _sold_state_path(cfg), dry_run=dry_run)
                total_changes += sold_stats.get("sold_marked", 0)
                print(f"  orders_fetched={sold_stats['orders_fetched']}  sold_marked={sold_stats['sold_marked']}  errors={sold_stats['errors']}")

            if total_changes and not dry_run:
                try:
                    _sm.enqueue_job(
                        queue_name="catalog_rebuild",
                        payload={"reason": "ebay_pull"},
                        dedupe_key="catalog_rebuild:pending",
                        not_before=time.time() + 30,
                        max_attempts=3,
                    )
                    print("catalog_rebuild job enqueued.")
                except Exception:
                    pass

            result = {
                "ok": True,
                "dry_run": dry_run,
                "active": active_stats,
                "sold": sold_stats,
            }

        elif args.op == "store-categories":
            from tgw.apis.ebay.trading import get_store_categories

            cats = get_store_categories(cfg)
            if not cats:
                print("No store custom categories found (or seller has no eBay store).")
                print("Configure store_category_by_ebay_category in tgw-api-config.json")
                print("using the names shown here once your store is set up.")
                result = {"ok": True, "count": 0, "categories": []}
            else:
                print(f"Found {len(cats)} store categories:\n")
                for c in cats:
                    print(f"  [{c['id']:>8}]  {c['path']}")
                print("\nAdd mappings to tgw-api-config.json:")
                print('  "store_category_by_ebay_category": {')
                print('    "default": "Name of catch-all store category",')
                print('    "261": "Video Games",   // eBay cat ID → store category name')
                print("  }")
                result = {"ok": True, "count": len(cats), "categories": cats}

        elif args.op == "store-category":
            import json as _json
            from pathlib import Path as _Path
            cg_path = _Path(cfg['category_groups_path'])

            if args.action == "list":
                from tgw.apis.ebay.trading import get_store_categories
                cats = get_store_categories(cfg)
                if not cats:
                    print("No store categories found (or seller has no eBay store).")
                    result = {"ok": True, "categories": []}
                else:
                    print(f"{'ID':>10}  Path")
                    print("-" * 60)
                    for c in cats:
                        print(f"  {c['id']:>8}  {c['path']}")
                    print("\nUse: tgw ebay store-category set <group_key> <id>")
                    result = {"ok": True, "categories": cats}

            elif args.action == "set":
                if not args.group or args.store_id is None:
                    print("Usage: tgw ebay store-category set <group_key> <store_category_id>")
                    result = {"ok": False, "error": "missing group or id"}
                else:
                    cg_data = _json.loads(cg_path.read_text(encoding='utf-8'))
                    groups = cg_data.get('groups', {})
                    if args.group not in groups:
                        avail = sorted(groups.keys())
                        print(f"Unknown group key {args.group!r}")
                        print(f"Available: {', '.join(avail)}")
                        result = {"ok": False, "error": f"unknown group: {args.group}"}
                    else:
                        groups[args.group]['store_category_id'] = args.store_id
                        # Best-effort: look up name from GetStore and update store_category too
                        resolved_name = None
                        try:
                            from tgw.apis.ebay.trading import get_store_categories
                            store_cats = get_store_categories(cfg)
                            match = next(
                                (c for c in store_cats if c['id'] == str(args.store_id)), None)
                            if match:
                                groups[args.group]['store_category'] = match['name']
                                resolved_name = match['name']
                        except Exception:
                            pass
                        cg_data['updated'] = datetime.now(timezone.utc).date().isoformat()
                        cg_path.write_text(
                            _json.dumps(cg_data, indent=2, ensure_ascii=False) + '\n',
                            encoding='utf-8',
                        )
                        if resolved_name:
                            print(f"Set {args.group}: store_category_id={args.store_id}, "
                                  f"store_category={resolved_name!r}")
                        else:
                            print(f"Set {args.group}: store_category_id={args.store_id}"
                                  f"  (name not resolved — set store_category manually if needed)")
                        result = {"ok": True, "group": args.group,
                                  "store_category_id": args.store_id,
                                  "store_category": resolved_name}

        elif args.op == "strikethrough-check":
            raw_ebay = cfg.get("raw", {}).get("ebay", {})
            enabled = raw_ebay.get("strikethrough_enabled", False)
            # Count items with original_retail_price already set
            root: Path = cfg["itemdata_root"]
            msrp_count = 0
            orp_count = 0
            for child in root.iterdir():
                jf = child / f"{child.name}.json"
                if not jf.exists():
                    continue
                try:
                    d = json.loads(jf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if d.get("product_lookup", {}).get("msrp"):
                    msrp_count += 1
                if d.get("draft_listing", {}).get("original_retail_price"):
                    orp_count += 1
            result = {
                "ok": True,
                "strikethrough_enabled": enabled,
                "items_with_msrp": msrp_count,
                "items_with_original_retail_price": orp_count,
                "next_steps": (
                    "ENABLED — originalRetailPrice will be set on new staged offers when product_lookup.msrp > launch price."
                    if enabled
                    else "DISABLED — verify access in Seller Hub (Marketing → Promotions) then set ebay.strikethrough_enabled=true in tgw-api-config.json"
                ),
            }
            print(f"strikethrough_enabled: {enabled}")
            print(f"Items with product_lookup.msrp: {msrp_count}")
            print(f"Items with draft_listing.original_retail_price: {orp_count}")
            print(f"Status: {result['next_steps']}")

        elif args.op == "data-scrub":
            scrub_pass = args.scrub_pass
            dry_run = not args.write
            if scrub_pass == 1:
                result = data_scrub_pass1(cfg, dry_run=dry_run)
                mode = "DRY RUN" if dry_run else "WRITTEN"
                print(f"[{mode}] Pass 1: #VERIFIED → verified")
                print(f"  Would rename / renamed: {result['renamed']}")
                print(f"  Skipped (no field):     {result['skipped']}")
                print(f"  Errors:                 {result['errors']}")
                if dry_run:
                    print("  Run with --write to apply.")
            elif scrub_pass == 2:
                result = data_scrub_size_class_backfill(cfg, dry_run=dry_run)
                mode = "DRY RUN" if dry_run else "WRITTEN"
                print(f"[{mode}] Pass 2: size_class backfill")
                print(f"  Would update / updated: {result['updated']}")
                print(f"  Skipped (no basis):     {result['skipped']}")
                print(f"  Errors:                 {result['errors']}")
                if dry_run and result.get("sample_would_update"):
                    print("  Sample:")
                    for entry in result["sample_would_update"][:5]:
                        print(f"    {entry['sku']}: {entry['fields']}")
                if dry_run:
                    print("  Run with --write to apply.")
                elif result["updated"]:
                    print("  catalog_rebuild job enqueued.")
            else:
                result = {"ok": False, "error": f"unknown scrub pass: {scrub_pass}"}

        elif args.op == "alt-text":
            if getattr(args, "batch", False):
                result = cmd_alt_text_batch(
                    cfg,
                    limit=args.limit,
                    provider=args.provider,
                    model=args.model,
                    dry_run=args.dry_run,
                )
            else:
                if not args.sku:
                    import sys as _sys
                    print("tgw alt-text: error: sku is required without --batch", file=_sys.stderr)
                    return 1
                result = cmd_alt_text(
                    cfg,
                    sku=args.sku,
                    model=args.model,
                    provider=args.provider,
                    dry_run=args.dry_run,
                )

        elif args.op == "alt-text-batch":
            result = _cmd_alt_text_batch(cfg, args)

        elif args.op == "revise":
            from .revision import cmd_revise
            result = cmd_revise(
                cfg,
                sku=args.sku,
                assignments=args.assignments,
                show=args.show,
                by=args.by,
            )
            if args.show and result.get("ok"):
                for line in result.get("diff_lines", []):
                    print(line)
                print()

        elif args.op == "todo":
            from tgw.todo import cmd_todo

            result = cmd_todo(cfg, args)
            # cmd_todo handles its own printing; skip the generic JSON dump
            return 0 if result.get("ok", True) else 1

        elif args.op == "plan":
            from tgw.plan_render import render_taskboard

            result = render_taskboard(cfg)
            if result["ok"]:
                print(f"Taskboard rendered: {result['path']} "
                      f"({result['open']} open, {result['done_week']} done this week)")
            else:
                print(f"Error: {result.get('error')}")
            return 0 if result["ok"] else 1

        elif args.op == "catalog-verify":
            result = cmd_catalog_verify(
                cfg,
                location=args.location,
                limit=args.limit,
                output=Path(args.output) if args.output else None,
                min_severity=args.severity,
                mark_verified=getattr(args, "mark_verified", False),
                force=getattr(args, "force", False),
                skip_verified=getattr(args, "skip_verified", False),
                fix=getattr(args, "fix", False),
                write=getattr(args, "write", False),
            )
            if getattr(args, "as_json", False):
                print(json.dumps(result, indent=2))
            # markdown report already printed by cmd_catalog_verify
            return 0 if result["ok"] else 1

        elif args.op == "dead-letter":
            result = cmd_dead_letter(
                cfg,
                queue=getattr(args, "queue", ""),
                limit=getattr(args, "limit", 50),
                requeue_id=getattr(args, "requeue", ""),
                requeue_transient=getattr(args, "requeue_transient", False),
                cancel_queue=getattr(args, "cancel", ""),
            )
            return 0 if result["ok"] else 1

        elif args.op == "queue-history":
            result = cmd_queue_history(
                cfg,
                sku=getattr(args, "sku", ""),
                queue=getattr(args, "queue", ""),
                job_id=getattr(args, "job_id", ""),
                limit=getattr(args, "limit", 100),
                json_out=getattr(args, "json_out", False),
            )
            return 0 if result["ok"] else 1

        elif args.op == "build-fingerprints":
            result = cmd_build_fingerprints(
                cfg,
                limit=getattr(args, "limit", None),
                check_only=getattr(args, "check_only", False),
            )
            return 0 if result["ok"] else 1

        elif args.op == "locate":
            result = cmd_locate(
                cfg,
                args.image,
                size_class=getattr(args, "size_class", None),
                top=getattr(args, "top", 10),
                json_out=getattr(args, "json_out", False),
            )
            return 0 if result["ok"] else 1

        elif args.op == "export-catalog":
            result = cmd_export_catalog(
                cfg,
                args.dest,
                no_thumbnails=getattr(args, "no_thumbnails", False),
                limit=getattr(args, "limit", None),
                check_only=getattr(args, "check_only", False),
                push=getattr(args, "push", False),
            )
            return 0 if result["ok"] else 1

        elif args.op == "restart-workers":
            result = cmd_restart_workers(queues=args.queues, dry_run=args.dry_run)
            if args.dry_run:
                return 0 if result["ok"] else 1

        elif args.op == "restart-ebay-token":
            from .queue import state_machine as _sm

            _sm.init(cfg["postgres_dsn"])
            cleared = _sm.clear_dead_letter(queue_name="token_refresh")
            jid = _sm.enqueue_job(
                queue_name="token_refresh",
                payload={"reason": "manual_restart"},
                max_attempts=3,
            )
            result = {
                "ok": True,
                "dead_letter_cleared": cleared,
                "new_job_id": jid,
                "note": "Token refresh job enqueued. Run tgw get-ebay-token first if refresh token is dead.",
            }

        elif args.op == "get-ebay-token":
            from urllib.parse import unquote

            from .apis.ebay.get_access_token import exchange_code_for_tokens, get_access_token, save_token_state
            from .apis.ebay.get_access_token import load_config as _ebay_load_config

            direct_code = getattr(args, "code", None)
            if direct_code:
                direct_code = unquote(direct_code)
                ebay_cfg = _ebay_load_config()
                tokens = exchange_code_for_tokens(direct_code, ebay_cfg, is_sandbox=getattr(args, "sandbox", False))
                save_token_state(tokens)
                token = tokens["access_token"]
                import time as _time

                exp = int(tokens.get("expiry", 0) - _time.time())
                print(f"Token exchanged. Expires in {exp}s ({exp // 3600}h). Run: tgw restart-ebay-token")
            else:
                token = get_access_token(prompt_if_needed=True, is_sandbox=getattr(args, "sandbox", False))
                print("Token written to secrets. Run: tgw restart-ebay-token")
            result = {"ok": True, "token_prefix": token[:20] + "..."}

        elif args.op == "report":
            from .reports import cmd_report_sales

            if args.report_type == "sales":
                result = cmd_report_sales(
                    cfg,
                    stale_only=args.stale,
                    output_dir=args.output,
                    no_vault=args.no_vault,
                )
            else:
                result = {"ok": False, "error": f"unknown report type: {args.report_type!r}"}

        elif args.op == "promo":
            from .promo import cmd_promo_draft, cmd_promo_list

            if args.promo_sub == "draft":
                result = cmd_promo_draft(
                    cfg,
                    discount=args.discount,
                    min_days=args.min_days,
                    min_price=args.min_price,
                    max_items=args.max_items,
                    duration=args.duration,
                    start_offset=args.start_offset,
                    output_dir=args.output,
                    no_vault=args.no_vault,
                )
            elif args.promo_sub == "list":
                result = cmd_promo_list(cfg)
            else:
                result = {"ok": False, "error": f"unknown promo sub-command: {args.promo_sub!r}"}

        elif args.op == "category-groups":
            from .ebay.pricing import _group_for_category, _load_groups

            _load_groups(cfg)  # warm cache

            if args.reseed:
                import json as _json

                vel_path = cfg["catalog_root"] / "velocity-stats.json"
                if not vel_path.exists():
                    result = {"ok": False, "error": "velocity-stats.json not found; run tgw velocity-report first"}
                else:
                    vel_cats = _json.loads(vel_path.read_text(encoding="utf-8")).get("categories", {})
                    groups_path = cfg["category_groups_path"]
                    groups_data = _json.loads(Path(groups_path).read_text(encoding="utf-8"))
                    updated = 0
                    for grp_key, grp in groups_data.get("groups", {}).items():
                        cat_ids = grp.get("ebay_categories", [])
                        total_w, weighted_sum = 0, 0.0
                        for cid in cat_ids:
                            vc = vel_cats.get(str(cid), {})
                            p25 = vc.get("p25_sale_price")
                            sold = vc.get("sold_count", 0)
                            if p25 and sold >= 3:
                                weighted_sum += p25 * sold
                                total_w += sold
                        if total_w:
                            new_typical = round(weighted_sum / total_w, 2)
                            grp.setdefault("pricing", {})["typical_used"] = new_typical
                            grp["pricing"]["typical_new"] = round(new_typical * 1.50, 2)
                            grp["pricing"]["floor"] = round(max(0.99, new_typical * 0.40), 2)
                            grp["pricing"]["source"] = "velocity_p25"
                            updated += 1
                    groups_data["updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
                    Path(groups_path).write_text(
                        _json.dumps(groups_data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    # Invalidate module cache so next call re-reads
                    import tgw.ebay.pricing as _pricing_mod

                    _pricing_mod._groups_cache = None
                    _pricing_mod._groups_reverse = None
                    result = {"ok": True, "groups_updated": updated}

            elif args.category_id:
                grp = _group_for_category(cfg, args.category_id)
                if grp:
                    result = {"ok": True, "category_id": args.category_id, "group": grp}
                else:
                    result = {"ok": False, "category_id": args.category_id, "error": "category not mapped to any group"}

            else:
                # List all groups
                groups_data = _load_groups(cfg)
                rows = []
                for key, grp in groups_data.get("groups", {}).items():
                    pricing = grp.get("pricing", {})
                    rows.append(
                        {
                            "key": key,
                            "name": grp["name"],
                            "category_count": len(grp.get("ebay_categories", [])),
                            "size_class": grp.get("size_class", ""),
                            "store_category": grp.get("store_category", ""),
                            "floor": pricing.get("floor"),
                            "typical_used": pricing.get("typical_used"),
                        }
                    )
                if not getattr(args, "as_json", False):
                    print(f"{'Key':<35} {'Name':<30} {'Cats':>4} {'Size':<12} {'Floor':>6} {'Typical':>8}  Store Category")
                    print("-" * 110)
                    for r in rows:
                        floor = f"${r['floor']:.2f}" if r["floor"] else "   N/A"
                        typical = f"${r['typical_used']:.2f}" if r["typical_used"] else "   N/A"
                        print(f"{r['key']:<35} {r['name']:<30} {r['category_count']:>4} {r['size_class']:<12} {floor:>6} {typical:>8}  {r['store_category']}")
                    print(f"\n{len(rows)} groups  |  file: {cfg['category_groups_path']}")
                    return 0
                result = {"ok": True, "count": len(rows), "groups": rows}

        elif args.op == "set-context":
            result = set_context(cfg, args.sku)

        elif args.op == "get-context":
            result = get_context(cfg)
            if args.sku_only:
                sku = result.get("sku")
                if sku:
                    print(sku)
                    return 0
                return 1

        elif args.op == "clear-context":
            result = clear_context(cfg)

        elif args.op == "create-item":
            result = cmd_create_item(
                cfg,
                template=args.template,
                count=args.count,
                dry_run=args.dry_run,
            )

        elif args.op == "set-template":
            result = cmd_set_template(
                cfg,
                group_key=args.group_key,
                sku=args.sku,
                list_groups=args.list_groups,
                camera_only=args.camera_only,
                dry_run=args.dry_run,
            )

        else:
            result = {"ok": False, "error": f"unknown op: {args.op!r}"}

        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("ok", True) else 1

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

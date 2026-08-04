#!/usr/bin/env python3
"""fleet_baseline_sweep.py — broker B5a: pin every mirrored item's draft to live.

Dave, s46: "Set all items to baseline. Every draft should match the live
data. Photos, everything. That is the state that needs to be maintained."

For each item with an ebay_live mirror (offer and/or inventory_item), compute
the pin via tgw.draft_sync.pin_draft_to_live and PATCH through the fence.
Idempotent and re-runnable: items already at baseline with no delta are
skipped, so a re-run converges to zero writes.

Skips (from the broker lifecycle table, ai-plans/reconciliation-broker.md):
  N1 draft_listing_state == 'editing'  — manipulation in flight
  N3 no ebay_live mirror               — nothing to match FROM (never-listed
                                         drafts and legacy Trading-API items)
  N4 sold items                        — frozen

Usage:
  sudo -u tgw python3 scripts/fleet_baseline_sweep.py --dry-run
  sudo -u tgw python3 scripts/fleet_baseline_sweep.py

Report: /opt/TGW/var/reports/fleet-baseline-sweep-<ts>.json
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.draft_sync import pin_draft_to_live
from tgw.logging import announce_script_run, setup_logging

_cfg = load_config(DEFAULT_CONFIG)
ITEMDATA = Path(_cfg["itemdata_root"])
REPORTS = Path("/opt/TGW/var/reports")
BASE = "http://127.0.0.1:7373"
with open(Path(_cfg["secrets_root"]) / "tgw-api-key.json") as _f:
    KEY = json.load(_f)["api_key"]
HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
    # background: prefix is LOAD-BEARING (s46 incident): without it the fence
    # treats each PATCH as an operator edit and auto-enqueues a forced
    # ebay_stage live push per item — 8,183 jobs in one run.
    "X-TGW-Caller": "background:fleet_baseline_sweep",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N pins (0 = all)")
    args = ap.parse_args()

    # No prior logging configuration in this script (verified live, todo
    # #1369) — without it, announce_script_run()'s event is silently
    # dropped (default root level WARNING, no handlers).
    try:
        setup_logging('tgw.fleet_baseline_sweep')
    except OSError:
        pass  # no writable log root (e.g. CI/test env) — announce still attempted below
    announce_script_run(
        'fleet_baseline_sweep.py',
        'pin every mirrored item draft to live (baseline broker B5a) via the fence',
        dry_run=args.dry_run, limit=args.limit,
    )

    stats = {
        "started": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "scanned": 0, "no_mirror": 0, "no_mirror_but_listed": 0,
        "sold": 0, "editing": 0, "already_baseline": 0,
        "pinned": 0, "state_only": 0, "errors": 0,
    }
    pinned, state_only, errors = [], [], []

    for d in sorted(ITEMDATA.iterdir()):
        jp = d / f"{d.name}.json"
        if not jp.is_file():
            continue
        stats["scanned"] += 1
        try:
            doc = json.loads(jp.read_text())
        except (OSError, ValueError) as exc:
            stats["errors"] += 1
            errors.append({"sku": d.name, "error": f"read: {exc}"})
            continue

        if str(doc.get("status") or "").lower() == "sold":
            stats["sold"] += 1
            continue
        if doc.get("draft_listing_state") == "editing":
            stats["editing"] += 1
            continue
        live = doc.get("ebay_live") or {}
        if not (live.get("inventory_item") or live.get("offer")):
            stats["no_mirror"] += 1
            if (doc.get("ebay_listing") or {}).get("listing_id"):
                stats["no_mirror_but_listed"] += 1
            continue

        try:
            fields = pin_draft_to_live(doc)
        except ValueError:
            stats["no_mirror"] += 1
            continue

        draft_same = doc.get("draft_listing") == fields["draft_listing"]
        pe_same = doc.get("pipeline_error") == fields["pipeline_error"]
        if draft_same and pe_same and doc.get("draft_listing_state") == "baseline":
            stats["already_baseline"] += 1
            continue
        is_state_only = draft_same and pe_same
        if is_state_only:
            # Content already matches — only the lifecycle marker is missing.
            fields = {"draft_listing_state": fields["draft_listing_state"],
                      "baseline_at": fields["baseline_at"]}

        if not args.dry_run:
            try:
                r = requests.patch(f"{BASE}/api/items/{d.name}",
                                   headers=HEADERS,
                                   json={"fields": fields}, timeout=30)
                r.raise_for_status()
            except requests.RequestException as exc:
                stats["errors"] += 1
                errors.append({"sku": d.name, "error": f"patch: {exc}"})
                time.sleep(0.02)
                continue
            time.sleep(0.02)

        if is_state_only:
            stats["state_only"] += 1
            state_only.append(d.name)
        else:
            stats["pinned"] += 1
            pinned.append(d.name)

        if args.limit and (stats["pinned"] + stats["state_only"]) >= args.limit:
            break

    stats["finished"] = datetime.now(timezone.utc).isoformat()
    REPORTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    report = REPORTS / f"fleet-baseline-sweep-{ts}{'-dry' if args.dry_run else ''}.json"
    report.write_text(json.dumps(
        {"stats": stats, "pinned": pinned, "state_only": state_only,
         "errors": errors}, indent=1))
    print(json.dumps(stats, indent=1))
    print(f"report: {report}")
    return 0 if not stats["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())

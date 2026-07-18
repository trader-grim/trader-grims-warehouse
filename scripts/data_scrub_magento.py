#!/usr/bin/env python3
"""
data_scrub_magento.py

Remove legacy Magento/eBay import artifact fields from item JSON files
in /opt/TGW/data/ItemData/.

Usage:
    python scripts/data_scrub_magento.py [--dry-run] [--execute] [--sku SKU] [--limit N]

Default behaviour is --dry-run. Pass --execute to actually write changes.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw import items  # noqa: E402
from tgw.config import DEFAULT_CONFIG, load_config  # noqa: E402
from tgw.logging import announce_script_run, setup_logging  # noqa: E402

FIELDS_TO_REMOVE = {
    "ItemCode",
    "ItemGroup",
    "MagentoID",
    "MagentoSKU",
    "MagentoURL",
    "OriginalSKU",
    "eBayItemID",
    "eBayListingURL",
}


def iter_item_jsons(root: Path, sku: str | None = None):
    """
    Yield (sku, json_path) tuples.

    Each item lives at <root>/<sku>/<sku>.json.
    If sku is given, yield only that one item.
    """
    if sku is not None:
        candidate = root / sku / f"{sku}.json"
        if candidate.exists():
            yield sku, candidate
        else:
            print(f"ERROR: No item JSON found for SKU '{sku}' at {candidate}", file=sys.stderr)
        return

    for sku_dir in sorted(root.iterdir()):
        if not sku_dir.is_dir():
            continue
        json_path = sku_dir / f"{sku_dir.name}.json"
        if json_path.exists():
            yield sku_dir.name, json_path


def process_item(cfg: dict, sku: str, execute: bool) -> int:
    """
    Check and optionally remove target fields from a single item JSON.

    Writes go through items.strip_fields() — one archive entry per item
    (invariant E5) plus the fence's atomic tmp+rename, replacing the
    previous raw json.dump() straight to the target path (audit#1143
    #1162+#1164: --execute mode bypassed the fence + atomic_write_json
    entirely). Inherited side effect: strip_fields() also clears
    'catalog_verified' on any item it actually modifies (see its docstring)
    — the old implementation never touched that field.

    Returns the number of fields removed (or that would be removed).
    Returns -1 on error — including a corrupt/unparseable item JSON, which
    must not kill the rest of the batch (audit#1143 #1235 follow-up: the
    original json.JSONDecodeError/OSError try/except was dropped when this
    switched to strip_fields(), which raises on a bad JSON file instead of
    returning {'ok': False}).
    """
    try:
        result = items.strip_fields(cfg, sku, sorted(FIELDS_TO_REMOVE),
                                    check_only=not execute)
    except Exception as exc:
        print(f"WARNING: Could not process {sku}: {exc}", file=sys.stderr)
        return -1
    if not result.get('ok'):
        print(f"WARNING: {result.get('error')}", file=sys.stderr)
        return -1

    found = result.get('removed', [])
    if not found:
        return 0

    if not execute:
        print(f"[DRY-RUN] {sku}: would remove: {', '.join(sorted(found))}")
    else:
        print(f"[MODIFIED] {sku}: removed: {', '.join(sorted(found))}")
    return len(found)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove legacy Magento/eBay artifact fields from item JSON files."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview changes without writing (default behaviour).",
    )
    mode_group.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually remove fields and write files.",
    )
    parser.add_argument(
        "--sku",
        metavar="SKU",
        default=None,
        help="Process only this SKU.",
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="Stop after processing N items.",
    )

    args = parser.parse_args()

    # Default to dry-run if neither flag was given
    execute = args.execute

    # No prior logging configuration in this script (verified live, todo
    # #1369) — without it, announce_script_run()'s event is silently
    # dropped (default root level WARNING, no handlers).
    try:
        setup_logging('tgw.data_scrub_magento')
    except OSError:
        pass  # no writable log root (e.g. CI/test env) — announce still attempted below
    announce_script_run(
        'data_scrub_magento.py',
        'remove legacy Magento/eBay import artifact fields from item JSON files',
        execute=execute, sku=args.sku, limit=args.limit,
    )

    cfg = load_config(DEFAULT_CONFIG)
    # audit#1143 #1235 follow-up: enumerate from the same root strip_fields()
    # resolves paths against, not a separately hardcoded constant — the two
    # silently drifting apart would make every item report "sku not found".
    itemdata_root = Path(cfg["itemdata_root"])

    if not itemdata_root.is_dir():
        print(f"ERROR: ItemData root not found: {itemdata_root}", file=sys.stderr)
        sys.exit(1)

    scanned = 0
    modified = 0
    total_fields_removed = 0

    for sku, _json_path in iter_item_jsons(itemdata_root, sku=args.sku):
        if args.limit is not None and scanned >= args.limit:
            break

        scanned += 1
        result = process_item(cfg, sku, execute=execute)

        if result > 0:
            modified += 1
            total_fields_removed += result
        # result == -1 means parse/write error; result == 0 means nothing to do

    mode_label = "EXECUTE" if execute else "DRY-RUN"
    print(
        f"\n[{mode_label}] Scanned {scanned}, "
        f"modified {modified} ({total_fields_removed} fields removed)"
    )


if __name__ == "__main__":
    main()

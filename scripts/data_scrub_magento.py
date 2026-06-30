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
import json
import sys
from pathlib import Path

ITEM_DATA_ROOT = Path("/opt/TGW/data/ItemData")

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


def process_item(sku: str, json_path: Path, execute: bool) -> int:
    """
    Check and optionally remove target fields from a single item JSON.

    Returns the number of fields removed (or that would be removed).
    Returns -1 on parse error.
    """
    try:
        text = json_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"WARNING: Could not parse {json_path}: {exc}", file=sys.stderr)
        return -1
    except OSError as exc:
        print(f"WARNING: Could not read {json_path}: {exc}", file=sys.stderr)
        return -1

    found = [field for field in FIELDS_TO_REMOVE if field in data]

    if not found:
        return 0

    if not execute:
        print(f"[DRY-RUN] {sku}: would remove: {', '.join(sorted(found))}")
        return len(found)

    # Execute mode: remove fields and write back
    for field in found:
        del data[field]

    try:
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print(f"[MODIFIED] {sku}: removed: {', '.join(sorted(found))}")
    except OSError as exc:
        print(f"WARNING: Could not write {json_path}: {exc}", file=sys.stderr)
        return -1

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

    if not ITEM_DATA_ROOT.is_dir():
        print(f"ERROR: ItemData root not found: {ITEM_DATA_ROOT}", file=sys.stderr)
        sys.exit(1)

    scanned = 0
    modified = 0
    total_fields_removed = 0

    for sku, json_path in iter_item_jsons(ITEM_DATA_ROOT, sku=args.sku):
        if args.limit is not None and scanned >= args.limit:
            break

        scanned += 1
        result = process_item(sku, json_path, execute=execute)

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


import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

# Add src to path so we can import tgw
sys.path.append(os.path.abspath("src"))

from tgw.config import load_config, DEFAULT_CONFIG

def scan_baseline(limit: int = 500):
    cfg = load_config(DEFAULT_CONFIG)
    root = Path(cfg["itemdata_root"])
    
    # Load category mapping
    cat_to_group = {}
    groups_path = Path("/opt/TGW/config/category-groups.json")
    if groups_path.exists():
        with open(groups_path, "r", encoding="utf-8") as f:
            groups_data = json.load(f)
            for group_id, group_info in groups_data.get("groups", {}).items():
                group_name = group_info.get("name", group_id)
                for cat_id in group_info.get("ebay_categories", []):
                    cat_to_group[str(cat_id)] = group_name

    results = []
    scanned = 0
    
    # Image extensions
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

    # Use find-like iteration
    for item_dir in root.iterdir():
        if not item_dir.is_dir() or not item_dir.name.startswith("tgw"):
            continue
        
        sku = item_dir.name
        jf = item_dir / f"{sku}.json"
        if not jf.exists():
            continue
            
        try:
            with open(jf, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            continue
            
        scanned += 1
        
        # Robust field extraction
        title = str(doc.get("title") or "").strip()
        
        # Price: check draft, offer, and legacy top-level
        price = doc.get("draft_listing", {}).get("price") or \
                doc.get("ebay_offer", {}).get("price") or \
                doc.get("price")
        
        # Photos
        has_photos = any(f.suffix.lower() in IMAGE_EXTS for f in item_dir.iterdir() if not f.name.startswith("."))
        
        # Location
        location = str(doc.get("location") or "").strip()
        
        # Condition
        condition = str(doc.get("condition") or doc.get("Condition") or "").strip()
        
        # Category ID
        ebay_cat = str(doc.get("ebay_category_id") or doc.get("eBay category 1 number") or doc.get("category_ids") or "unknown")

        viols = {
            "missing_title": not title,
            "missing_price": not price,
            "missing_photos": not has_photos,
            "missing_location": not location,
            "missing_condition": not condition,
        }
        
        group = cat_to_group.get(ebay_cat, "Other")
        
        results.append({
            "sku": sku,
            "group": group,
            "ebay_cat": ebay_cat,
            "viols": viols
        })
        
        if scanned >= limit:
            break

    # Aggregate by group
    summary = {}
    total_viols = {k: 0 for k in ["missing_title", "missing_price", "missing_photos", "missing_location", "missing_condition"]}
    unknown_cats = {}
    
    for r in results:
        group = r["group"]
        if group == "Other":
            cat_id = r["ebay_cat"]
            unknown_cats[cat_id] = unknown_cats.get(cat_id, 0) + 1
            
        if group not in summary:
            summary[group] = {
                "count": 0,
                "missing_title": 0,
                "missing_price": 0,
                "missing_photos": 0,
                "missing_location": 0,
                "missing_condition": 0,
            }
        summary[group]["count"] += 1
        for k, v in r["viols"].items():
            if v:
                summary[group][k] += 1
                total_viols[k] += 1

    # Output Markdown Table
    print("# Antigravity Catalog Quality Baseline Scan")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Sample Size: {scanned} items")
    print("")
    print("| Category Group | Count | No Title | No Price | No Photos | No Loc | No Cond |")
    print("|----------------|-------|----------|----------|-----------|--------|---------|")
    
    # Sort by count desc
    sorted_groups = sorted(summary.items(), key=lambda x: x[1]["count"], reverse=True)
    for group, stats in sorted_groups:
        print(f"| {group} | {stats['count']} | {stats['missing_title']} | {stats['missing_price']} | {stats['missing_photos']} | {stats['missing_location']} | {stats['missing_condition']} |")
    
    print(f"| **TOTAL** | **{scanned}** | **{total_viols['missing_title']}** | **{total_viols['missing_price']}** | **{total_viols['missing_photos']}** | **{total_viols['missing_location']}** | **{total_viols['missing_condition']}** |")
    print("")
    print("## Violation Percentage")
    for k, v in total_viols.items():
        pct = (v / scanned * 100) if scanned > 0 else 0
        print(f"- {k.replace('_', ' ').title()}: {pct:.1f}%")
        
    if unknown_cats:
        print("")
        print("## Top Unknown Category IDs")
        sorted_cats = sorted(unknown_cats.items(), key=lambda x: x[1], reverse=True)
        for cat, count in sorted_cats[:10]:
            print(f"- {cat}: {count}")

if __name__ == "__main__":
    scan_baseline(500)

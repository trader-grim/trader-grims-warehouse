#!/usr/bin/env python3
"""
PP-VISION-001 Phase 1 pilot — CPU-only CLIP embedding feasibility.

Read-only against /opt/TGW/data/ItemData (never writes item data). Samples
N real item photos, embeds them with a small OpenCLIP checkpoint on CPU,
and measures:

  1. Throughput (images/sec, single-threaded CPU forward pass).
  2. Match quality vs the existing dhash/histogram baseline
     (src/tgw/fingerprint.py), on a hand-picked set of same-item photo
     pairs (multi-photo SKUs — different angle/photo of the SAME item,
     the "known-similar" case) and different-item pairs (two random
     distinct SKUs, the "known-dissimilar" case).

Not wired into any pipeline/worker. Run manually:

    source .pilot-venv/bin/activate
    PYTHONPATH=/opt/TGW/var/worktrees/1481-vision-pilot/src python3 \
        scripts/pilot_1481_clip_embed.py --sample-size 300 --out scripts/pilot_1481_out

Writes only under --out (default scripts/pilot_1481_out/, gitignored) plus
a summary JSON. Never touches /opt/TGW/data/ItemData.
"""
from __future__ import annotations

import argparse
import json
import random
import resource
import time
from pathlib import Path
from typing import Dict, List, Tuple

ITEM_DATA_ROOT = Path("/opt/TGW/data/ItemData")
IMG_EXTS = {".jpg", ".jpeg", ".png"}


def find_multi_photo_skus(root: Path, need: int, rng: random.Random) -> List[Tuple[str, List[Path]]]:
    """Scan ItemData for SKUs with >=2 photos (gives us same-item pairs)."""
    skus = [d for d in root.iterdir() if d.is_dir() and d.name != "tgw"]
    rng.shuffle(skus)
    hits = []
    for d in skus:
        imgs = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS)
        if len(imgs) >= 2:
            hits.append((d.name, imgs))
        if len(hits) >= need:
            break
    return hits


def sample_single_photo_per_sku(root: Path, n: int, rng: random.Random,
                                 exclude: set) -> List[Tuple[str, Path]]:
    """Sample n distinct SKUs (one photo each) for the throughput measurement,
    excluding SKUs already used for the pair-quality set so samples don't overlap."""
    skus = [d for d in root.iterdir() if d.is_dir() and d.name != "tgw" and d.name not in exclude]
    rng.shuffle(skus)
    out = []
    for d in skus:
        imgs = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS)
        if imgs:
            out.append((d.name, imgs[0]))
        if len(out) >= n:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-size", type=int, default=300,
                     help="total photos for the throughput pass (200-500 per packet spec)")
    ap.add_argument("--pair-skus", type=int, default=15,
                     help="number of multi-photo SKUs to use for same-item quality pairs")
    ap.add_argument("--model", default="ViT-B-32", help="OpenCLIP model name")
    ap.add_argument("--pretrained", default="laion2b_s34b_b79k",
                     help="OpenCLIP pretrained tag (small/fast CPU-friendly checkpoint)")
    ap.add_argument("--seed", type=int, default=1481)
    ap.add_argument("--out", default="scripts/pilot_1481_out")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    import torch
    import open_clip
    from PIL import Image

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from tgw.fingerprint import dhash, color_histogram, hamming, histogram_distance

    print(f"Loading {args.model} / {args.pretrained} (CPU)...")
    t0 = time.time()
    model, _, preprocess = open_clip.create_model_and_transforms(
        args.model, pretrained=args.pretrained, device="cpu")
    model.eval()
    load_elapsed = time.time() - t0
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model loaded in {load_elapsed:.1f}s, {n_params/1e6:.1f}M params")

    # --- Step 1: pick same-item pairs (multi-photo SKUs) + different-item pairs ---
    multi = find_multi_photo_skus(ITEM_DATA_ROOT, args.pair_skus, rng)
    used_skus = {sku for sku, _ in multi}
    print(f"Found {len(multi)} multi-photo SKUs for same-item quality pairs")

    diff_pool = sample_single_photo_per_sku(ITEM_DATA_ROOT, args.pair_skus * 2, rng, used_skus)
    diff_pairs = list(zip(diff_pool[::2], diff_pool[1::2]))
    used_skus |= {sku for sku, _ in diff_pool}

    # --- Step 2: throughput sample (distinct SKUs, one photo each) ---
    throughput_sample = sample_single_photo_per_sku(
        ITEM_DATA_ROOT, args.sample_size, rng, used_skus)
    print(f"Throughput sample: {len(throughput_sample)} photos")

    def clip_embed(path: Path):
        im = Image.open(path).convert("RGB")
        t = preprocess(im).unsqueeze(0)
        with torch.no_grad():
            feat = model.encode_image(t)
            feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat.squeeze(0).numpy()

    def cosine(a, b) -> float:
        import numpy as np
        return float(np.dot(a, b))

    # --- Throughput measurement ---
    print("Measuring throughput...")
    t0 = time.time()
    ok = 0
    for sku, path in throughput_sample:
        try:
            clip_embed(path)
            ok += 1
        except Exception as exc:
            print(f"  skip {sku}: {exc}")
    elapsed = time.time() - t0
    throughput = {
        "n_images": ok,
        "elapsed_seconds": round(elapsed, 2),
        "images_per_second": round(ok / elapsed, 4) if elapsed else None,
        "seconds_per_image": round(elapsed / ok, 4) if ok else None,
        "model_load_seconds": round(load_elapsed, 2),
        "model_params_millions": round(n_params / 1e6, 1),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }
    print(json.dumps(throughput, indent=2))

    projected_55k_hours = (throughput["seconds_per_image"] * 55000 / 3600
                            if throughput.get("seconds_per_image") else None)

    # --- Match-quality measurement ---
    print("\nComputing match-quality pairs (CLIP cosine vs dhash/histogram baseline)...")

    def fp_distance(p1: Path, p2: Path) -> Dict[str, float]:
        d1, h1 = dhash(p1), color_histogram(p1)
        d2, h2 = dhash(p2), color_histogram(p2)
        dh_dist = hamming(d1, d2) / 64.0
        hist_dist = histogram_distance(h1, h2)
        combined = 0.6 * dh_dist + 0.4 * hist_dist
        return {"dhash_distance": round(dh_dist, 4), "hist_distance": round(hist_dist, 4),
                "combined_distance": round(combined, 4)}

    same_item_rows = []
    for sku, imgs in multi:
        a, b = imgs[0], imgs[rng.randrange(1, len(imgs))]
        try:
            ea, eb = clip_embed(a), clip_embed(b)
        except Exception as exc:
            print(f"  skip pair {sku}: {exc}")
            continue
        row = {"sku": sku, "photo_a": a.name, "photo_b": b.name,
               "clip_cosine_similarity": round(cosine(ea, eb), 4),
               **fp_distance(a, b)}
        same_item_rows.append(row)

    diff_item_rows = []
    for (sku_a, path_a), (sku_b, path_b) in diff_pairs:
        try:
            ea, eb = clip_embed(path_a), clip_embed(path_b)
        except Exception as exc:
            print(f"  skip diff pair {sku_a}/{sku_b}: {exc}")
            continue
        row = {"sku_a": sku_a, "sku_b": sku_b,
               "clip_cosine_similarity": round(cosine(ea, eb), 4),
               **fp_distance(path_a, path_b)}
        diff_item_rows.append(row)

    def summarize(rows: List[dict], key: str) -> Dict[str, float]:
        vals = [r[key] for r in rows]
        return {"n": len(vals), "mean": round(sum(vals) / len(vals), 4) if vals else None,
                "min": round(min(vals), 4) if vals else None,
                "max": round(max(vals), 4) if vals else None}

    quality = {
        "same_item_pairs": {
            "n": len(same_item_rows),
            "clip_cosine_similarity": summarize(same_item_rows, "clip_cosine_similarity"),
            "fingerprint_combined_distance": summarize(same_item_rows, "combined_distance"),
        },
        "different_item_pairs": {
            "n": len(diff_item_rows),
            "clip_cosine_similarity": summarize(diff_item_rows, "clip_cosine_similarity"),
            "fingerprint_combined_distance": summarize(diff_item_rows, "combined_distance"),
        },
    }

    # Separation margin: how cleanly does each method's score separate same-item
    # from different-item pairs? CLIP: higher cosine = more similar (want same >> diff).
    # Fingerprint: lower distance = more similar (want same << diff).
    same_clip_mean = quality["same_item_pairs"]["clip_cosine_similarity"]["mean"]
    diff_clip_mean = quality["different_item_pairs"]["clip_cosine_similarity"]["mean"]
    same_fp_mean = quality["same_item_pairs"]["fingerprint_combined_distance"]["mean"]
    diff_fp_mean = quality["different_item_pairs"]["fingerprint_combined_distance"]["mean"]
    quality["separation"] = {
        "clip_cosine_gap": round((same_clip_mean or 0) - (diff_clip_mean or 0), 4),
        "fingerprint_distance_gap": round((diff_fp_mean or 0) - (same_fp_mean or 0), 4),
        "note": "larger gap = cleaner separation between same-item and different-item pairs "
                "for that method (CLIP: same-cosine minus diff-cosine; fingerprint: "
                "diff-distance minus same-distance, since lower distance = more similar)",
    }

    def pairwise_auc(same_vals: List[float], diff_vals: List[float], higher_is_same: bool) -> float:
        """Mann-Whitney-style AUC: P(random same-item pair ranks 'more similar' than
        a random different-item pair). 1.0 = perfect separation, 0.5 = no better than chance."""
        n = 0
        wins = 0.0
        for s in same_vals:
            for d in diff_vals:
                n += 1
                if higher_is_same:
                    wins += 1.0 if s > d else (0.5 if s == d else 0.0)
                else:
                    wins += 1.0 if s < d else (0.5 if s == d else 0.0)
        return wins / n if n else None

    clip_same_vals = [r["clip_cosine_similarity"] for r in same_item_rows]
    clip_diff_vals = [r["clip_cosine_similarity"] for r in diff_item_rows]
    fp_same_vals = [r["combined_distance"] for r in same_item_rows]
    fp_diff_vals = [r["combined_distance"] for r in diff_item_rows]
    quality["auc"] = {
        "clip_cosine": round(pairwise_auc(clip_same_vals, clip_diff_vals, True), 4),
        "fingerprint_combined_distance": round(pairwise_auc(fp_same_vals, fp_diff_vals, False), 4),
        "note": "P(a random same-item pair scores more similar than a random different-item "
                "pair). 1.0 = perfect separation, 0.5 = coin flip. Directly comparable across "
                "the two methods regardless of their different score scales.",
    }

    result = {
        "model": {"name": args.model, "pretrained": args.pretrained},
        "throughput": throughput,
        "projected_full_catalog_hours_55k_items": (
            round(projected_55k_hours, 1) if projected_55k_hours else None),
        "match_quality": quality,
    }

    (out_dir / "same_item_pairs.json").write_text(json.dumps(same_item_rows, indent=2))
    (out_dir / "diff_item_pairs.json").write_text(json.dumps(diff_item_rows, indent=2))
    (out_dir / "summary.json").write_text(json.dumps(result, indent=2))
    print("\n=== SUMMARY ===")
    print(json.dumps(result, indent=2))
    print(f"\nWrote {out_dir}/summary.json, same_item_pairs.json, diff_item_pairs.json")


if __name__ == "__main__":
    main()

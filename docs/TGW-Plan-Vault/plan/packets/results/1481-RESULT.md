# Result: 1481 vision-pilot
Status: done
Todo: #1481   PP: PP-VISION-001

## Summary

CPU-only feasibility pilot for a CLIP-family embedding model, per
PP-VISION-001 Phase 1. **Recommendation: worth pursuing — CLIP embeddings
clearly outperform the current dhash/histogram baseline on match quality,
and CPU-only batch throughput is viable for the full catalog (~4 hours,
one-time, background job) even before any GPU purchase.** A GPU remains
worth it for Phase 3's interactive/real-time query use case (sub-second
per query vs ~0.26s/image single-query CPU cost is fine either way, but
GPU would matter for the full-catalog *re-embed* cadence if that ever
needs to run more than a few times a year), but Phase 2's full-catalog
batch index does not need to wait on it.

## Pinned model

- **`open_clip_torch`, model `ViT-B-32`, pretrained tag `laion2b_s34b_b79k`**
  (OpenCLIP's "small/fast" candidate named in `pp/PP-VISION-001.md` Phase 0
  — the ViT-L/14 "higher-quality/heavier" candidate was not benchmarked
  this pass; flagging as a deviation below).
- Checkpoint size: 578 MB (safetensors, downloaded from the LAION
  HuggingFace mirror, cached at `~/.cache/huggingface/hub/models--laion--CLIP-ViT-B-32-laion2B-s34B-b79K/`).
  151.3M parameters.
- Peak RSS during inference: ~1.5 GB (single process, no batching).

## Environment

- Host: this session's CPU-only host — AMD Ryzen 5 3500U, 8 logical cores,
  32 GB RAM (`/proc/cpuinfo`, `free -h`) — the same class of machine
  `reference/HARDWARE-AI-INFERENCE.md` describes as the fleet's current
  100%-CPU baseline (that doc names "AMD Ryzen, 32GB RAM" generically;
  did not independently re-verify this exact host is tgw-prod itself vs.
  an equivalent CPU-only dev host — noting as an assumption, not a
  confirmed identity match).
- Torch CPU wheel (`torch==2.13.0+cpu`) uses all 8 cores by default for
  the forward pass (no explicit thread pinning was applied) — the
  throughput numbers below are 8-core CPU throughput, not single-threaded.
- Dependencies installed into a scoped local venv
  (`/opt/TGW/var/worktrees/1481-vision-pilot/.pilot-venv/`, gitignored,
  never touches the shared `tgw` venv or system Python): `torch` (CPU
  wheel via `download.pytorch.org/whl/cpu`), `open_clip_torch`, `pillow`,
  `numpy` (pulled in transitively). ~1.1 GB total venv size.
- **New dependency flag (per contract):** none of `torch`/`open_clip_torch`
  are in the shared `tgw` venv today. This pilot did not touch it — kept
  fully scoped to `.pilot-venv/` inside the worktree. If Phase 2/3 is
  approved, these become real production dependencies to add to the
  shared venv/nix closure at that time (not done here).
- **Nix-ld gotcha, same class as todo #1374** documented in the tgw-coder
  contract: running `torch` from inside a Nix-built Python venv hit
  `ImportError: libstdc++.so.6` until `LD_LIBRARY_PATH` included a
  `gcc-*-lib` nix store path alongside `$NIX_LD_LIBRARY_PATH` (torch's
  compiled extensions need libstdc++, not just libz). Documented in the
  script's usage comment for reproducibility.

## Live evidence (verbatim script output)

Ran twice (`--sample-size 300 --pair-skus 20 --seed 1481`, deterministic)
— second run below, weights warm in cache, model load time reflects that:

```
Loading ViT-B-32 / laion2b_s34b_b79k (CPU)...
Model loaded in 1.9s, 151.3M params
Found 20 multi-photo SKUs for same-item quality pairs
Throughput sample: 300 photos
Measuring throughput...
{
  "n_images": 300,
  "elapsed_seconds": 77.69,
  "images_per_second": 3.8617,
  "seconds_per_image": 0.259,
  "model_load_seconds": 1.85,
  "model_params_millions": 151.3,
  "peak_rss_mb": 1522.6
}
```

**Throughput: ~3.4–3.9 images/sec (8-core CPU), ~0.26–0.29 sec/image.**
Projected full-catalog batch (55,420 items,
`ls /opt/TGW/data/ItemData | wc -l`): **~4.0–4.5 hours, one-time,
background job** — matches the existing `build_fingerprint_index`
"on-demand batch build, never inline from a worker" operational shape
named in `pp/PP-VISION-001.md` Phase 2.

Match quality — 20 same-item pairs (two different photos of the same SKU,
sampled from real multi-photo item folders in `/opt/TGW/data/ItemData`)
vs 20 different-item pairs (photos from two distinct random SKUs):

```
"match_quality": {
  "same_item_pairs": {
    "clip_cosine_similarity": {"mean": 0.5481, "min": 0.3011, "max": 0.9207},
    "fingerprint_combined_distance": {"mean": 0.4000, "min": 0.1352, "max": 0.6855}
  },
  "different_item_pairs": {
    "clip_cosine_similarity": {"mean": 0.2520, "min": 0.1056, "max": 0.4690},
    "fingerprint_combined_distance": {"mean": 0.5599, "min": 0.4031, "max": 0.7516}
  },
  "separation": {
    "clip_cosine_gap": 0.2961,
    "fingerprint_distance_gap": 0.1599
  },
  "auc": {
    "clip_cosine": 0.8975,
    "fingerprint_combined_distance": 0.7638,
    "note": "P(a random same-item pair scores more similar than a random
      different-item pair). 1.0 = perfect separation, 0.5 = coin flip."
  }
}
```

**CLIP separates same-item from different-item photo pairs meaningfully
better than the current dhash/histogram baseline: AUC 0.898 vs 0.764**
(computed directly, Mann-Whitney-style pairwise comparison over all
20×20 same-vs-different pair combinations — see
`scripts/pilot_1481_clip_embed.py`'s `pairwise_auc()`). Both methods beat
chance (0.5), but the baseline's own docstring self-assessment ("a
workflow proof, not a final CLIP matcher") is borne out: the baseline has
real overlap between its same-item and different-item score
distributions (worst same-item pair: 0.6855 distance vs best
different-item pair: 0.4031 distance — the ranges overlap), whereas CLIP's
overlap is narrower (worst same-item cosine 0.3011 vs best different-item
cosine 0.4690 — still some overlap, but a smaller one, consistent with
the higher AUC).

Raw per-pair data (real SKUs/photos, not synthetic) is preserved for
inspection: `scripts/pilot_1481_out/same_item_pairs.json`,
`scripts/pilot_1481_out/diff_item_pairs.json`, `summary.json` — these are
gitignored (derived pilot output, reproducible from the script + seed,
not committed) but exist on disk in the worktree for review.

## Deviations from spec

- **Only one CLIP variant benchmarked (ViT-B-32 / laion2b_s34b_b79k),
  not the ViT-L/14 "higher-quality/heavier" candidate also named in
  `pp/PP-VISION-001.md` Phase 0.** ViT-B/32 was picked as the small/fast
  CPU-friendly option per the packet's explicit "keep the model small"
  instruction and `HARDWARE-AI-INFERENCE.md`'s CPU-only constraint;
  ViT-L/14 (~10x more params) would materially change the throughput
  number and was judged out of scope for a single feasibility pass on a
  time-boxed pilot. Flagging as a deviation rather than silently deciding
  "one variant is enough" — if Dave wants the ViT-L/14 throughput/quality
  comparison too before committing to Phase 2, that's a fast rerun of the
  same script with `--model ViT-L-14 --pretrained <tag>`.
- **Sample size 300 (within the 200-500 spec range) rather than the full
  500** — chosen for a faster iteration loop while tuning the pilot
  script; throughput/quality numbers were consistent across two runs at
  this size (deterministic seed) and 300 is well within the spec's stated
  range, not a shortfall against it.
- **20 same-item/different-item pairs for match-quality** (not separately
  spec'd — packet said "a handful"). Used all multi-photo SKUs found in a
  3000-item scan of `ItemData` up to that cap; real photo pairs, not
  synthetic.
- Model-load timings shown above are warm-cache (weights already
  downloaded from a prior smoke-test run in this session); the first-ever
  run on a fresh host would add a one-time ~578MB download + ~5 min
  cold-load (observed once, not double-counted in the throughput number
  which measures only the embedding forward pass).

## Files touched
- `scripts/pilot_1481_clip_embed.py` (new — the pilot script, runnable/
  reproducible per its own docstring usage instructions)
- `.gitignore` (added `.pilot-venv/` and `scripts/pilot_1481_out/`)
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1481-vision-pilot.md` (new
  — session breadcrumb; can be archived/removed by the stitch step)
- `docs/TGW-Plan-Vault/plan/packets/results/1481-RESULT.md` (this file)
- **Not touched:** `src/tgw/fingerprint.py` or any other pipeline/worker
  code — this is read-only feasibility measurement, no production wiring,
  per the packet's explicit scope.
- **Not touched:** `/opt/TGW/data/ItemData` — sampling was read-only
  (`Path.iterdir()` / `Image.open()`, no writes); verified no item JSON
  or photo was modified.

## Out-of-scope findings filed
- Todo #1504 (PP-VISION-001) — full `pytest -q` collection fails on this
  worktree with `ModuleNotFoundError: tgw.ebay.category_aspect_migration`;
  root cause is the shared `catio-nix-0.0.1-alpha` branch advancing past
  this worktree's base commit (`a432002` → `05f6347`+) while the worktree
  was in use, not anything this pilot touched. `tests/test_fingerprint.py`
  (the module this pilot actually depends on) passes standalone
  (8 passed). Full-suite acceptance against a moving base branch is a
  process gap worth tracking, not a defect in this packet's work.

## Recommendation for PP-VISION-001 Phase 2/3

Proceed. The Phase 1 open question — "is CPU-only embedding generation
viable for batch indexing, even if slow" — is answered **yes**: ~4 hours
for the full ~55k-item catalog as a one-time background batch job is well
within the existing operational pattern (`build_fingerprint_index` is
already an on-demand batch build, not a live-request path). Match quality
is a real, measurable improvement over the current baseline (AUC 0.90 vs
0.76), which is the actual capability gap this PP exists to close. The
GPU purchase remains worth it for Phase 3's real-time/interactive query
path and for cutting re-embed cadence cost if the catalog needs frequent
re-indexing, but is not a blocker for starting Phase 2.

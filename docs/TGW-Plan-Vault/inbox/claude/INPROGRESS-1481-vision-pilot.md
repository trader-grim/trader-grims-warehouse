# INPROGRESS — todo #1481 (PP-VISION-001 Phase 1)

Working in isolated worktree `/opt/TGW/var/worktrees/1481-vision-pilot` on
branch `todo/1481-vision-pilot`. Task: CPU-only CLIP embedding feasibility
pilot — pin a small CLIP-family model, embed ~200-500 real item photos
(read-only sample from `/opt/TGW/data/ItemData`), measure throughput +
match quality vs the existing dhash/histogram baseline in
`src/tgw/fingerprint.py`. No production wiring — deliverable is a pilot
script + a written measurement report at
`docs/TGW-Plan-Vault/plan/packets/results/1481-RESULT.md`.

Plan: pip-install a scoped local venv (`.pilot-venv/`, inside the
worktree, gitignored) with `torch` (CPU wheel) + `open_clip_torch` — not
present in the shared `tgw` venv, not installed system-wide. Then build
`scripts/pilot_1481_clip_embed.py` to sample photos, embed with a small
ViT-B/32 OpenCLIP checkpoint, and compare against `fingerprint.dhash` /
`color_histogram` on a hand-picked set of same-item/different-item photo
pairs.

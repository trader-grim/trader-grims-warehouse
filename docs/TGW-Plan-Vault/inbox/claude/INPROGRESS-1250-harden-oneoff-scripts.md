# INPROGRESS todo #1250 — harden one-off scripts pattern (PP-COHESION-001)

Worktree: /opt/TGW/var/worktrees/1250-harden-oneoff-scripts, branch todo/1250-harden-oneoff-scripts.

Found: sub-tasks 1 (announce retrofit) and 3 (dedupe-key fix + run-once
marker) for requeue_ebay_draft_402_dead_letters.py were already done in
prior commits 65f536d (#1206) and fbbb786 (#1265/#1250) — that script was
already live-run and applied on 2026-07-14 (marker file present on
tgw-prod). Added on top: an explicit `_make_dedupe_key()` helper (was
inline), and a second guard layer — a per-SKU attempt cap persisted in the
marker file — since the existing job_id-only marker doesn't stop an
endlessly re-dead-lettering SKU (each failed retry gets a fresh job_id).
Also retrofitted the one real gap found by grepping scripts/*.py for
main()-without-announce: pilot_1481_clip_embed.py. Built the detector
(scripts/check_announce_script_run.py) + tests. Full pytest suite green
(2477 passed/1 skipped) run with worktree PYTHONPATH override, confirmed
tgw.logging.__file__ resolves under the worktree.

Script left unrun this session, per the todo's own instruction (and it was
already run+applied before, so there's nothing pending to run).

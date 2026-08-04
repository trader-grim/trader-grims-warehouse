# Result: 1366 review-md-gate
Status: done
Todo: #1366   PP: PP-HERMES-EA-001
Files touched:
- scripts/check_review_md.py (new) — mechanical pre-stitch gate: verifies
  `docs/TGW-Plan-Vault/plan/packets/results/<id>-REVIEW.md` exists for one
  or more todo ids, or for every local `todo/<id>-<slug>` branch via
  `--scan-branches`; prints a per-id OK/MISS report and exits 1 if any are
  missing, 0 if all present.
- tests/test_check_review_md.py (new) — 7 tests: single-id match, missing
  returns None, hyphenated multi-id batch filename matching (mirrors real
  `1292-1293-...-REVIEW.md` / `1278-1279-REVIEW.md` naming), a batch check
  that reproduces the actual incident's id set (1280 present / rest
  missing → exit 1), an all-present pass, and `main()` exit-code checks.
  All filesystem access is monkeypatched to `tmp_path`, fully offline.
- .claude/skills/tgw-runner-review/SKILL.md — added a "Required pre-merge
  step" note under step 6 ("Clean path — hand off to stitch") pointing
  whoever performs the stitch at `scripts/check_review_md.py` as a
  mandatory pre-merge check, referencing the #1366 incident it closes.

Live evidence:
- `pytest -q` (worktree copy confirmed: `tgw.__file__` resolves to
  `/opt/TGW/var/worktrees/1366-review-md-gate/src/tgw/__init__.py`, run
  with `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH`) →
  `2477 passed, 1 skipped, 1 warning in 47.39s`. 7 of those are the new
  `tests/test_check_review_md.py` (all pass in isolation too: `7 passed in 0.57s`).
- Real pass case: `python3 scripts/check_review_md.py 1280` against the
  actual repo `docs/TGW-Plan-Vault/plan/packets/results/` →
  `OK   #1280: TGW-Plan-Vault/plan/packets/results/1280-REVIEW.md` /
  `CLEAR: all 1 todo(s) have a -REVIEW.md, safe to stitch.` / exit 0.
- Real fail case: `python3 scripts/check_review_md.py 999999` (id with no
  corresponding file) →
  `MISS #999999: no -REVIEW.md found under .../results` /
  `BLOCKED: 1 of 1 todo(s) missing -REVIEW.md, do not stitch: 999999` /
  exit 1.
- Batch case mirroring the actual incident's 7-todo wave
  (`python3 scripts/check_review_md.py 1280 1282 1284 1288 1291 1297`) →
  all 6 report OK (they've since been backfilled per the todo body) →
  `CLEAR: all 6 todo(s) have a -REVIEW.md, safe to stitch.` / exit 0. This
  confirms the tool would have reported `MISS` for 5 of these 6 at the
  time the omission was live (all except #1280 — #1280 belonged to a
  different todo id already covered), i.e. the exact condition it's built
  to catch.
- `--scan-branches` against the live worktree's own `todo/1366-review-md-gate`
  branch correctly reported it as MISS/BLOCKED (exit 1) — this task's own
  branch has no `1366-REVIEW.md` yet, which is correct: writing that file
  is `tgw-runner-review`'s job (the reviewer), not the executor's — this
  gate is meant to block premature stitching of this very branch until
  that review step runs.

Deviations from spec: none. The spec named `scripts/check_review_md.py`
and `tests/test_check_review_md.py` as example names — used as given. Root
cause of the original silent skip was explicitly stated as "not confirmed"
in the todo body and out of scope for this packet (mechanical gate only,
not a root-cause investigation) — not attempted here.

Out-of-scope findings filed: none. No new operational friction hit beyond
what's already documented in CLAUDE.md's worktree/PYTHONPATH/LD_LIBRARY_PATH
notes, which were followed as specified.

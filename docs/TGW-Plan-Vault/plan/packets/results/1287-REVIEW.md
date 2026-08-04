# Review: 1287 ai-identify-model-var-clobber
Status: cleared — stitched in `9bcf73b`, paired with `#1296` (merged 18s
apart at 11:31:11/11:31:29 on 2026-07-13; #1296's own merge message
confirms "2-in-a-row clean"). CORRECTED 2026-07-13: an earlier pass at
this file claimed #1287 was stitched solo with no pairing candidate and
invoked a new cadence-rule exception to justify it — that was wrong,
based on misreading `git log --all`'s branch-mixed commit order as a
timeline. Real commit timestamps show #1287 (fixed 11:25) and #1296
(fixed 11:30) were run back-to-back and stitched as the intended 2-in-a-
row pair, fully compliant with the original cadence rule as written — no
exception needed. The lone-task exception this mistake prompted
(`PP-HERMES-EA-001.md`) remains adopted as forward-looking policy on its
own merits, just not evidenced by this todo.
Reviewer: Claude (main session, tgw-runner-review)

Checked: Spec (exact 3-site rename, provider-model provenance untouched at
all 6 other reference points as specced), Out-of-scope (only
ai_identify.py + a new test file — covered by the carve-out regardless of
new/existing), invariants.md (n/a — local variable fix, no fence bypass,
no ItemData write path change), Live evidence (module load confirmed
from worktree, all 4 acceptance criteria verified — LLM provenance
correct in both identification_history and vision_results, product model
still correctly captured in both places it's needed, full suite green).
No deviations, no out-of-control triggers fired.

Stitched.

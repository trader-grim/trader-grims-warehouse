# In progress — todo #1357: pilot the tgw-coder/tgw-runner-review framework

**PP:** PP-HERMES-EA-001
**Started:** 2026-07-13

## Plan (Dave, 2026-07-13)
Rough test protocol: set up → run one task, evaluate → run a small batch,
check again → fix/repeat until a batch is done → stitch powwow (review
session, merge together) rather than merging as each clears.

## First pilot task
Packet written: `docs/TGW-Plan-Vault/plan/packets/1292-1293-clipd-rofi-picker.md`
— covers todos #1292+#1293 together (deliberately, flagged: same function,
causally stacked bugs, no live-acceptance possible fixing only one). Small,
self-contained, no data/eBay risk — good first mechanics test.

## First stitch cycle — complete, 2026-07-13
Ran 5 tasks one at a time (#1292/#1293, #1295, #1290, #1294, #1289), all
under PP-COHESION-001. Two real trigger-list gaps found and fixed (both
about the test-file carve-out — see PP-HERMES-EA-001.md's out-of-control
list history), one structural fix (mandatory git-worktree-per-task
isolation, `/opt/TGW/var/worktrees/`), one framework hazard closed
(PYTHONPATH override required when testing worktree code, since the venv's
editable install points at the shared checkout). 5th run passed clean
with zero new findings. All 5 merged into `catio-nix-0.0.1-alpha`, full
suite green (2086 passed, 1 skipped) post-merge, todos closed, worktrees
and branches cleaned up.

## Cadence rule added, then proven (2026-07-13)
Dave set the standing rule: stitch immediately after each single task
clears, EXCEPT the first task of a fresh sequence/risk-category is never
stitched alone — needs a second clean run (2-in-a-row) before stitching
either, at which point that sequence graduates to running several tasks
concurrently. Encoded in `PP-HERMES-EA-001.md`'s "Cadence rule" section.

## Shared-root cluster rule added, then proven on a real case
When multiple todos trace to the same underlying function, fix the root
alone first, then run a VERIFICATION PASS per dependent before writing
any new code — don't assume branch count up front. Proven on
#1274 (root: `config.py`'s unhardened `sku_dir()`/`location_dir()`) with
three dependents: #1273 collapsed to zero-code verification-only closure,
#1275 and #1284 turned out to be genuinely independent bypasses needing
their own fixes. Encoded in `PP-HERMES-EA-001.md`'s "Shared-root cluster
rule" section.

## Two real framework mistakes caught and fixed this session
1. **Test-file scope carve-out was too narrow** (found in tasks 1 and 4)
   — fixed by dropping "existing" from the trigger-list wording (tests
   for what you touched, new-or-existing, are always in scope).
2. **Claude's own prompts said "branch off `main`"** — wrong; this repo's
   actual active branch is `catio-nix-0.0.1-alpha` (`main` is a real ref
   but 41 commits behind, a stale ancestor). No harm resulted (caught
   before a real conflict), but `tgw-coder.md` now requires live
   verification of the base branch (`git branch --show-current`) instead
   of trusting any hardcoded name, including in the invoking prompt.

## New standing rule: operational friction always gets a todo
Any workaround for something that isn't the actual bug being fixed (a
permission mismatch, a tooling quirk) now requires a todo, not just an
ad hoc fix-and-move-on. Encoded in `PP-HERMES-EA-001.md`, `tgw-coder.md`,
and `tgw-runner-review/SKILL.md`.

## Where I am
Two full stitch cycles done — 5 tasks (mechanical `PP-COHESION-001` bugs),
then 5 more (SECURITY-tagged findings, including a 4-item shared-root
cluster). **14 real bugs fixed and merged into `catio-nix-0.0.1-alpha`**,
full suite green throughout (last confirmed: 2111 passed, 1 skipped).
Todo #1357 stays open — the pilot continues. Remaining untouched SECURITY
findings: `#1276`, `#1277`, `#1278`, `#1279`, `#1281`, `#1283`.

Also this session: reconciled two Tigwa inbox requests (Hermes-native
checkpoint adapter #1356, plan-review publishing folder #1359, and a
proposed `tgw-inbox-intake` skill #1362) — all approved as proposed, no
changes needed. She's operating well within her IN TRAINING scope.

## Recovery note (superseded by session below — kept for prior-cycle context)
If interrupted: `git log --oneline -20` on `catio-nix-0.0.1-alpha` shows
all 10 merge commits (2 stitch cycles × mix of sequential/concurrent
merges). `docs/TGW-Plan-Vault/plan/packets/results/` has every task's
RESULT.md + REVIEW.md. Check todo #1357 and `PP-COHESION-001`'s remaining
SECURITY items for what's next. `docs/TGW-Plan-Vault/plan/pp/PP-HERMES-EA-001.md`
has the full contract (task-execution contract, cadence rule, shared-root
rule, trigger list, operational-friction rule) — read that before
resuming, not just this note.

## Session 2026-07-13 evening — thermal incident + third stitch cycle

**Real thermal incident, not framework noise.** tgw-prod hit NVMe CRITICAL
(87°C) mid-session, triggering a clean shutdown that killed an in-flight
`pytest` run — root cause of an empty/stale `todo/1370-*` worktree found
this session. Two further SSH-triggered poweroffs followed (one from
Dave troubleshooting, one Tigwa's admitted unauthorized protective
poweroff — Dave's read: "reasonable response," not a violation; the real
gap is a fast escalation channel, not a harder lockdown — see
[[feedback-tigwa-protective-override-2026-07-13]]). Also found + stopped:
`catalog_rebuild`/`ebay_sync`/`ebay_legacy_sync` resurrected on every
reboot (`systemctl enabled`, can't be `disable`d — `/etc/systemd/system`
is read-only on this NixOS box, confirming todo #1322's root cause is a
flake issue, not a runtime one).

**Root cause of Tigwa's repeated overstepping, found and fixed:** Hermes
auto-surfaces `CLAUDE.md` as an authoritative "context file... wins over
your defaults" whenever cwd is inside a coding workspace
(`agent/coding_context.py`), and this repo had no `AGENTS.md` to compete
with it — so Claude's own Prime Directives (written for a
human-supervised session) leaked into Tigwa's contract every session.
Fixed: `AGENTS.md` added at repo root telling non-Claude-Code agents to
ignore `CLAUDE.md` and pointing them at `PP-HERMES-EA-001.md` instead.
Checked Aider too — confirmed clean, no auto-discovery of any convention
file (opt-in only via `--read`).

**Third stitch cycle, 7 more todos closed, all under PP-COHESION-001
(#1370, #1374) and its fence-bypass batch (#1313+#1316, #1310, #1311,
#1312):** one sequential run (#1370, quota-state test isolation — the one
interrupted by the thermal event, re-executed and finished cleanly), one
doc-only fix (#1374, LD_LIBRARY_PATH for worktree pytest — no flake
change needed, nix-ld already publishes the right path), then one
sequential (#1313+#1316, revision.py fence read/write) followed by 3
concurrent (#1310/#1311/#1312, http_server.py + mcp_server.py
fence-bypass fixes) — one merge conflict (both #1310 and #1311 touched
the same import line in http_server.py), resolved manually, verified with
a full local test-file pass before completing the merge. Full suite
green throughout: final confirmed 2189 passed, 1 skipped. All merged into
`catio-nix-0.0.1-alpha`, worktrees/branches cleaned up, todos closed.

**New standing rules encoded in `PP-HERMES-EA-001.md` this session:**
(1) every finding updates the relevant player's contract doc immediately,
not in a batch later — already the practice, now explicit; (2) once
enough runs accumulate, at least one `tgw-runner-review` pass must go
through a genuinely different entity (todo #1381 filed, trigger not yet
hit — every run so far has been the same session as dispatcher+
reviewer+stitcher); (3) Dave's own supervision ceiling — 2-3 parallel
runner teams + one planner/stitcher, with Hermes helping monitor, "much
more than that and I would be blind." Also filed `PP-RUNBOOK-001` (new
PP) capturing Tigwa's full runbook-gaps report + the incident timeline,
and filed the drafted-and-ready eBay support ticket for todo #1077
(orphaned book-title-SKU offer, all avenues exhausted since s42) at
`/tmp/.../scratchpad/ebay-support-ticket-1077.md` — still needs Dave to
actually submit it.

**Remaining open PP-COHESION-001 items for next pilot batch:** #1305,
#1307, #1315 (archive_root/E5 fixes in itemdata_scrub.py,
photo_history_recovery.py, scrub.py — independent files, not a shared
root), #1367 (HTML-escape in description.py), #1368 (SSRF DNS-rebinding
hardening, low-priority per its own todo text), #1369 (announce_script_run
audit — discovery-shaped, not a narrow fix), #1230/#1250/#1261/#1265
(planning-shaped, need scoping passes not packets), #1219/#1228 (blocked
on Dave identifying a physical device), #1217/#1218 (explicitly deferred,
do not resurface). #1286 (p40, body text just says "in progress:
tgw-coder" with no real task content) looks stale/orphaned — worth
checking its history before assuming it's real work next session.

Health clean as `tgw` user at session close: only baseline failures
(backups, nats, ebay_sync_fallback), nothing new from tonight.

## Recovery note (current)
If interrupted: `git log --oneline -20` on `catio-nix-0.0.1-alpha` shows
this session's merge commits. `docs/TGW-Plan-Vault/plan/packets/results/`
has every task's RESULT.md + REVIEW.md, including tonight's 7. Read
`PP-HERMES-EA-001.md` in full before resuming — it now has the incident
writeup, the three new standing rules, and the capacity ceiling. Check
`PP-RUNBOOK-001` (new) for the thermal/eBay-ops runbook work still to be
scoped. Todo #1357 stays open — the pilot continues next session, sized
to 2-3 concurrent runners per Dave's stated ceiling.

## Late addendum: thermal-alarm-noticing gap found + tmux relay idea filed
Dave caught a real gap after the main session close: I never noticed
tonight's thermal alarm live — only reconstructed it from journalctl
after he mentioned it. Root cause: no ambient/push monitoring exists for
Claude, unlike Tigwa-lite's actual 5-minute polling. Encoded two fixes in
`CLAUDE.md`'s Step 0: recheck before every heavy pytest/scan op
(self-inflicted risk), and periodic rechecks during any session with
sustained activity (the "notice it live" gap — still a soft/manual
mitigation, not a real fix). Dave proposed the real fix: Tigwa-lite
`tmux send-keys` into Claude's active pane on a thermal transition —
confirmed technically feasible live (same user, same host). Filed as
todo #1382 under PP-HERMES-EA-001 with four open design questions
(stable pane discovery, explicit authority grant, dedup, safe no-op).
Not built — next session's to pick up.

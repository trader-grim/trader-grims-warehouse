# PP-COHESION-001 — full-codebase cohesion+correctness audit (full detail)

## PP-COHESION-001 — full-codebase cohesion+correctness audit (2pm agenda, todo #1143)
**Given a real PP designation 2026-07-11** — was source-tagged only
(`audit#1143`, `audit#COHESION-2026-07`) despite being a real, recurring,
already-substantial body of work with its own section here. Now also
covers the 2026-07-07 follow-up cohesion pass (45 findings, todos
#1273-1317), not just the original #1143 batch — both batches share this
heading/PP going forward.

**Dave: "I want to right the ship... check the whole thing and make sure
each part and the whole are cohesive."** Prompted by discovering that a
full week of code (2026-06-24 through 2026-07-02, the `ae9b1e6` commit
and everything before it) never went through `/code-review`/ultrareview
— diffs had grown too large to review by the time anyone tried. Same-day
finding: an 8-angle review of just today's 47-file/3,800-insertion diff
(todo #1114 fix + drive-index work) found 7 real confirmed bugs, all
fixed same session — real signal that unreviewed accumulation is a
genuine regression source, not a hypothetical.

**Plan:** a `Workflow`-based audit, staged per-subsystem (workers/,
apis/ebay/, `http_server.py` on its own — it's grown into a multi-
thousand-line file, queue/state-machine, scripts/, the Nix flake) rather
than by git history — sidesteps the "one commit mixes noise and signal"
problem that blocked ultrareview entirely. Two passes per subsystem:
correctness-bug finding (same 8-angle method as today) plus a **cohesion
pass** checking cross-subsystem consistency (is "tgw-api is the fence"
actually honored everywhere, do invariants.md's rules hold everywhere
they claim to, are there now-drifted duplicate implementations across
files).

**Sizing (calibrated from today's real pass):** ~830K tokens for one
47-file diff-sized review. Full codebase ≈ 8-10 subsystem-sized chunks
+ a cohesion pass ≈ **~8-11M tokens total**, order-of-magnitude. Deliberately
NOT scoped to one session — each subsystem chunk is independent and
resumable (Workflow's run-caching), so this runs opportunistically
whenever usage allows (Dave: "having a project like this would be an
excellent use of that [bonus] usage"), picking up wherever a prior run
left off. Not started — gated on Dave's go-ahead at 2pm.

**Prevention going forward** (Dave: "I need to do the reviews more
regularly"): review each day's diff before it accumulates — plain
`/code-review` for a free/quick inline pass, `/code-review ultra` for a
periodic cloud pass while diffs are still small enough to clear its
size guard.

**Status (2026-07-10, refactored — the "Remaining subsystems" note below was
stale): the discovery phase is COMPLETE.** All 6 planned subsystem audits
have research docs — `workers/` (2026-07-05), `apis/ebay/`, `http_server.py`,
`queue/state-machine`, `scripts/`, and the nix flake ("FINAL SLICE",
confirmed in `RESEARCH-1143-nix-flake-audit.md`). What's left is executing
the findings each audit spun off, not more discovery.

Findings-execution status by subsystem (2026-07-10 check):
- `workers/` — DONE. #1162-#1170 (9 correctness bugs) fixed earlier; #1171
  (8 batched cohesion findings) fixed 2026-07-10 (path-construction cleanup,
  itemdata_scrub.py root/sku validation hardening, photo_history_recovery.py
  catalog-refresh trigger, shared `_format_ebay_error`, ebay_sku_migrate.py
  write-pattern documented in invariants.md A5). One follow-up deferred as
  todo #1261 (itemdata_scrub.py's ad-hoc queue — bigger execution-model
  change, out of scope for a cohesion batch). **2026-07-10, re-examined and
  deliberately left deferred again (Dave: document for a future planning
  session rather than force a fix now)** — see below.

**PLANNING ITEM — itemdata_scrub.py queue migration (deferred 2x, needs a
real scoping pass):** `itemdata_scrub.py`'s `main()` uses a bare
`queue_dir = Path.cwd()` file-based queue (job = a file in the cwd; success
= the file gets deleted) instead of `state_machine`/`QueueWorker` like every
other worker — no visibility in `tgw queue-status`, no postgres-backed
retry/dead-letter semantics. **Checked live 2026-07-10: it isn't currently
scheduled anywhere** — no cron entry, no systemd timer, no reference in the
nix flake (`grep -rn itemdata_scrub ~/tgw-flake` → nothing). So the
practical impact of the visibility gap is zero today; nobody is missing
status on jobs that aren't flowing through it.

The real fix is a genuine migration, not a quick conversion: a new systemd
service + timer (or on-demand queue entry point), converting the dequeue
model from "file exists in cwd" to postgres rows, deciding how
`ScrubRules`/`--config` get supplied in that model (currently CLI args to a
one-shot batch run), and deciding whether this becomes a `tgw-worker@` unit
like everything else or stays a manual on-demand tool with better status
reporting bolted on. That's real design work — worth scoping properly in a
dedicated session rather than forcing a partial fix (e.g. just logging queue
depth somewhere `tgw health` can see, without fixing the underlying
file-vs-postgres model split) into a batched cleanup pass. Todo #1261
remains open, now explicitly framed as "needs a scoping pass," not "needs a
quick fix."
- `apis/ebay/` — DONE. #1182 fixed 2026-07-10 (conditions.py policy-cache
  memoization, trading.py 429-retry shared across all 3 Trading API
  generators).
- `http_server.py` — DONE. #1198 fixed 2026-07-10 (shared catalog_rebuild
  enqueue helper, sku traversal guards on 2 routes, store-category dropdown
  dead-code + fragile-fallback cleanup, deduped price formatter).
- `queue/state-machine` — findings executed in earlier sessions (see commit
  history around #1202, #1206 fixes); no open audit#1143 todos remain for
  this subsystem.
- `scripts/` — DONE. #1213 fixed 2026-07-10 (photo_repair_iss013.py
  ITEMDATA_ROOT now config-derived, matching sibling
  photosync_canary_probe.py). Todo #1203 is `done` — this section used to
  say "INPROGRESS," which was stale.
- **nix flake — 3 SECURITY findings remain open, not yet fixed** (sentence
  reunited 2026-07-12, Fable independent review #1338 — this list had been
  severed mid-clause by misfiled notes for over a week):
  - #1219 (NFS Queue export writable to the whole 192.168.60.0/24 subnet,
    should be host-locked like the ro exports below it) — **BLOCKED** on
    #1228 (no static IP/DHCP reservation exists yet for the intake
    camera/phone device; checked live ARP table 2026-07-10, several
    unidentified LAN hosts, none confirmable as the intake device from
    tgw-prod alone — needs Dave to identify the device + reserve its lease
    on the router).
  - #1217/#1218 (Syncthing GUI/second-instance bind exposure) — explicitly
    set to p95 by Dave 2026-07-07, deferred until dev settles (see
    `feedback-deprioritize-syncthing-auth` memory) — intentionally not
    being worked, not an oversight.

**Other audit#1143 fixes landed this stretch** (misfiled notes consolidated
2026-07-12): #1168 (ebay_publish condition fallback now writes corrected
condition back to draft_listing, tests added), #1171 (workers-audit cohesion
findings, see `DONE-1171`), #1173 (`lookup_epid` re-raises
`QuotaBudgetExceeded`), #1181 (`best_category()` fallback chain fixed),
#1182 findings #2/#3 (ebay conditions memoization + trading retry backoff,
[DONE-1182-ebay-cohesion-cache-retry.md](reference/DONE-1182-ebay-cohesion-cache-retry.md)),
#1206 (requeue-402 dedupe guard), #1235 (atomic-write sweep: 6 sites fixed,
8 new tests, 1861 passing — deviation: `itemdata_scrub.py` write stays
outside fence, PP-FENCE-001 gap documented). Session 48 (2026-07-06)
completed dead-letter/atomic-write/multi_intake fixes; code reviews
addressed all critical findings except 4 PLAUSIBLE deferred as todo #1246;
PR #8 not yet merged.

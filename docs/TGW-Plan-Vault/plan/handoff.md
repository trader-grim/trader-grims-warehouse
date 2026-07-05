# TGW Handoff — rolling (last 2 sessions + current risks)

**Rules for this file (R3.2, session 42):** hard cap ~150 lines. Holds ONLY: current
risks, the last two sessions' summaries, and the recommended next sequence. When a new
session is added, the oldest moves to `archive/SESSION-LOG.md`. Pre-redraw handoff
(v5 + all session logs): `archive/handoff-v5-2026-07-02-preredraw.md`.

Source-of-truth ranking: `tgw todo` (canonical tasks) → `TGW-Master-Plan.md` (spec/
status) → `reference/` docs. Tracker beats plan when they disagree.

---

## Current risks (ranked)

0a. **OPEN (todo #1115) — ebay_upload silently masks partial photo-upload failure,
    and a leftover redraft-loop backlog re-exhausted ebay_eps quota 3 days running
    (07-01/02/03)**. `ebay_upload.py`'s completion guard only fails if ZERO photos
    exist, so quota-blocked photos get silently dropped and logged as "success ―
    0 new". Backlog (2,715 stale retry_wait jobs, ~2,514 legacy SKUs, left behind by
    the #1107 loop) auto-requeued every ~6h and raced the worker at every midnight-PST
    quota reset, burning the full daily EPS budget before real work ran. Backlog
    CANCELLED 2026-07-03 (Dave authorized); code fix still open. `tgw202606021133367`
    still short 17/26 photos — needs a decision on how to finish it (see
    `dev-workflow/research/` session-43 note). Full detail:
    `inbox/DONE-ebay-photo-desync.md` (or `INPROGRESS-` if not yet closed).
0. **RESOLVED s42 evening (todo #1107, closed)** — the R1.3 requeue test exposed a
   chain that was diagnosed to root cause: the http PATCH endpoint's
   auto-redraft-on-draft_listing-change fired on WORKER fence patches too, creating an
   infinite draft→patch→redraft loop (one SKU: 287 draft jobs; 2 live listings PUT to
   eBay every ~90s for hours; the all-day 4-jobs/min queue drip). **Fixed**: fence
   clients send `X-TGW-Caller`; auto-redraft is operator-edits-only. The feared price
   reverts NEVER reached eBay (capture ground truth: only the 2 loop SKUs were PUT;
   the 5 flagged items are legacy-Item# and stage always skipped them). Local damage
   fixed: **784 items** carried stale pre-s41 draft prices above their live markdown —
   backfilled from the live mirror (before/after in `var/backups/s42-price-backfill/`)
   + a never-raise clamp added to ebay_stage (C5-extended, `allow_price_raise` to
   override, 4 tests). All workers running again.

1. **No backup running** — Postgres work ledger (todos + job history) is NOT
   re-derivable from ItemData. PP-BACKUP-001 operator todos #61/#146/#147. Weeks old.
2. **Test suite rot** — true state 1,399 pass / 11 fail / 236 errors (most:
   test_http_server.py broken since cookie-auth refactor). "Suite green" claims from
   earlier sessions were stale. Repair: todo #1102. Until fixed, only targeted test
   runs are meaningful.
3. **RESOLVED s45 (2026-07-04/05 night): ebay_draft 402 pile fully drained.**
   Final pass 2,656/2,658 succeeded (99.92%); day total ~6,500 jobs, ~$1.08
   OpenRouter spend. Only failures: 4 corrupt-photo SKUs (Feb-2022 migration
   truncation — recovery roster in #1145 note; fleet integrity sweep running
   on a1131, todo #1154). dead_letter table rows are historical (D4 clones).
4. **Live-fire gates unexecuted** — listeditor revision apply (R1.1) and action
   console operator test (R1.2) are the current critical path; everything else waits.
5. **todo #1077** — orphaned bad-SKU offer keeps ebay_sync on per-SKU fallback
   (health red). Dave must contact eBay support.
6. **15 Syncthing conflict files** in the vault (master-plan edit races 07-01/02).
   NOTE: the plan was redrawn s42 — resolve conflicts in favor of the new plan; the
   pre-redraw content is archived.
7. **Thermal hook not installed** — agent shell commands are not yet blocked at
   THROTTLE/SHUTDOWN; harness denied agent self-modification; needs Dave's explicit
   authorization or manual file drop (script in s42 transcript/inbox note).

---

## Session 45 — 2026-07-04→05 (provider flip · a1131 buildout · tool fixes · knowledge-plane plan) — COMPLETE

Committed as-we-went (Dave's instruction), 7 commits on catio-nix-0.0.1-alpha.

- **LLM provider flip (todo #1144 DONE, live-verified):** Google dole
  free-tier quota PER PROJECT (~20 req/day/model here vs published 1,000) —
  2,171 llm_google 429s in one day from the 402-requeue backlog. Dave's call:
  OpenRouter is PRIMARY; Google free tier = OPERATOR EMERGENCY RESERVE
  (interactive-only fallback); failover pattern kept + precheck-gated for a
  future paid Google key. Docs: reference/LLM-Providers-Quotas.md (canonical,
  finding was rediscovered 3× before being written down), invariant E8,
  CLAUDE.md row, memories. Backlog drains ~10× faster since (no 429+40s tax).
- **#1145 PP-UIPIPE-001 opened (p5): web UI pipeline defect audit.** Dave:
  "the web ui pipeline ain't cutting it"; his draft-vs-offer hypothesis
  CONFIRMED by evidence sweep — tgw202605052336026 LIVE at $40.99 with local
  draft_listing.price=None; tgw202605060125081 published 07-04 with 1/8
  photos (after #1115 P1 marked done!); 9/10 items same fulfillment policy;
  publish silently re-runnable (dozens of succeeded publish jobs per SKU,
  C3); published items never get a published status locally. Full evidence:
  inbox/INPROGRESS-1145-uipipe-defect-audit.md. 4pm: Dave names the
  wrong-shipping listing + rest of defect list → root-cause→packet map.
- **Standing rules encoded:** a1131 is shared Dave+Claude for THERMAL RELIEF
  — offload Claude's checks there on hot days, never pause pipeline workers
  for heat (CLAUDE.md + memory); NFS shares for check data = todo #1146.
- Also: archived 6 processed s44 inbox notes; swept last night's uncommitted
  pm-intake vault filings into a labeled commit (verified against FILING-LOG
  first).

**s45 evening/night (continued past 4pm through ~03:00):**
- **a1131 fully built out** (#1146 DONE): ro NFS data/log mounts, claude
  account (key-only + Dave-authorized NOPASSWD sudo), Wake-on-LAN live-fired
  (`wakeonlan c8:2a:14:2a:a1:85`; NEVER initiate suspend — iMac bug).
  nix-syncthing overrideDevices/Folders=false fix (rebuilds were wiping
  GUI-added peers — Dave's vault share); devices restored, Dave re-accepting
  shares.
- **Two UI-pipeline TOOL FIXES live-verified** (Dave's course-correction:
  fix the tool, not the data lists — see memories): per-field policy
  resolution (#1152; config FC4/payment/return now always win) and
  draft-price-only staging (stale ebay_offer.price can never publish
  unreviewed; operator List on unpriced item → HardFailure + no_price_set
  finding persisted). 8 wrong-policy live listings repaired PS→FC4;
  0125081 healed 1→8 photos via C10 chain.
- **Four-item forensics:** one root shape — truth/plan/live planes never
  reconciled. Broker planned (`ai-plans/reconciliation-broker.md`, packets
  B0–B5; B0 = Dave's 20-min rule-table sign-off; cardinal rule: validate
  against TRUTH, never the plan).
- **Knowledge plane planned** (`ai-plans/recoll-annex-jetstream.md` +
  PP-KNOWLEDGE-001 in master plan): stage 1 = organize/make accessible;
  todos #1147-#1151; Dave: annex-gdrive REPLACES Syncthing for data trees
  (vault→git); E0 transport decision leans Postgres-events over JetStream.
- **402 pile FULLY DRAINED:** ~6,500 jobs, 99.9% success, ~$1.08; ~2,650
  fresh drafts now await operator review (NB #1113 ebay_dole not installed).
- **Fleet photo-integrity sweep DONE** (a1131 over NFS, 3.4h): 206 bad/149
  SKUs (0.076%), single Feb-2022 unverified-copy event, 30 LIVE listings
  prioritized; roster = var/reports/photo-integrity-2026-07-05.tsv; plan =
  ai-plans/photo-integrity-mitigation.md (#1154).
- New skill: `/tgw-packet`. New todos: #1145-#1154.

**Open into next session:** B0 broker sign-off (20min, unlocks B1-B5) ·
#1145 walkthrough remainder (Dave's full defect list; 2336026 price via
editor) · #1147 search surface (top delegable) · fleet getOffer policy
sweep (~2k calls, no gate) · #1143 audit (missed again) · #1139 · E0/A0
decision packets.

---


Older sessions: `archive/SESSION-LOG.md`.

---

## First commands for the next session

```bash
cat /opt/TGW/var/run/thermal.status
sudo -u tgw tgw ops-digest          # replaces ad-hoc health/dead-letter checks
sudo -u tgw tgw todo claude
sudo -u tgw tgw plan check
git status --short | head           # s42 work is UNCOMMITTED until Dave says commit
```

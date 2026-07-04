# Session log archive (rolled off handoff.md's 2-session window)

## Session 41 — 2026-07-02 (quota drains, data-preservation bugs, google_direct)

Committed: `a7e7439`, `f511f2d`, `d1cad9a`. Full detail:
`dev-workflow/research/SESSION41-wrapup.md` + `archive/handoff-v5-2026-07-02-preredraw.md`.

- eBay quota drains fixed (QA-telemetry call removed; 25707 fallback capped 24h;
  tree-cache auto-expiry removed; warm-up gated to pre-reset window).
- `google_direct` LLM provider live (free-tier Gemini verified); ai_identify/alt_text/
  ebay_draft/bulk_classify moved to it; OpenRouter auto-fallback.
- ebay_draft aspect-fill now vision-based (up to 10 photos).
- Data-preservation bugs: price reducer never persisted reductions (silent revert on
  re-stage) — fixed; `atomic_write_json` reverted shared-file perms to 0600 — fixed;
  vault permission drift root-caused (stale deployed script + the above).
- `tgw-clipd` crash loop (15,769 restarts) fixed; UTC-as-local timestamp display fixed
  (13 sites).

## Session 42 — 2026-07-02 (retarget + R0 quota independence + data-first redraw)

Nothing committed to git yet — Dave controls commits. All changes live in prod.

- **Retarget approved + executed**: `plan/RETARGET-2026-07-02.md` (diagnosis F1–F5,
  tracks R0–R3, freeze list, work-packet protocol).
- **Quota independence (R0) built and live-verified**:
  - `getRateLimits` probe works (snapshot `/opt/TGW/var/run/ebay-rate-limits-probe.json`).
  - **Bulk aspects**: `tgw warm-ebay-aspects` — ONE call on the untouched
    `commerce.taxonomy.bulk` pool (100/day) cached aspects for ALL 15,105 leaf
    categories (shards + raw gz at `ItemCatalog/ebay-aspects-bulk/`). UI aspect
    lookups now need zero live Taxonomy calls; operator testing unblocked same day.
    Aspects cache is permanent + manual refresh (TTL removed, matches tree policy).
  - **`tgw.quota` budget layer** at every metered choke point (REST/Trading/EPS/LLM):
    daily per-pool counters (PST boundary), background halt at 70%, 30-min post-429
    stand-down, 429s logged as incidents with caller identity
    (`var/log/quota-incidents.jsonl`), new `quota` health check. Caught 181 real 429s
    (ebay_draft/ebay_upload churning exhausted pools) within minutes; churn stopped
    after stand-down deploy. Quota/429/usage-limit errors now TRANSIENT-requeue in
    workers — quota walls can no longer pile up dead letters.
  - **`tgw ops-digest`** — morning one-screen: flagged health, quota spend,
    dead-letter deltas, restart flags, stale inbox notes.
  - Timestamps: 6 naive datetime sites fixed; invariant E6. Verified stored data was
    never wrong (timestamptz + journald store UTC; s41 bug was rendering-only).
- **PRIME DIRECTIVES added to top of CLAUDE.md** (Dave's standing orders, enforcement
  over memory) + **`reference/TGW-Data-Charter.md`** (axiom: eBay is a rented window,
  the local dataset IS the business; asset inventory; rules for new work).
- **Raw eBay capture at the fence** (invariant E7): every eBay response (REST/Trading/
  EPS, errors included) → `incoming/ebay/YYYY-MM-DD.jsonl.gz`, fail-open, capture
  happens in `client.py` before any worker parses. Live-verified.
- **Master plan REDRAWN data-first** (~250 lines): PP designs split byte-exact to
  `plan/pp/`, history to `plan/archive/sections/`; `tgw plan check` all clear after.
- Found: `tgw restart-workers` references nonexistent `ebay_dole` unit (batch fails);
  CLAUDE.md `tgw todo add` syntax stale (it's `--add`).
- Tests: +23 new (quota 17, capture 6); targeted suite green outside pre-rotten files.

**Open from s42:** R1 live-fires; R1.8 dataset backfill (Dave's go); R2.2 digest on
web UI home; R2.3 push-on-red; #1102 suite repair; #1103 dataset-growth digest lines;
#1104 enforce E5 in code; thermal hook authorization.

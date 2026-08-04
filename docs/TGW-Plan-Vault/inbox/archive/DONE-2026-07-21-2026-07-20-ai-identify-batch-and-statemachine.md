# IN PROGRESS — 2026-07-20 session, next: ai_identify 427-item batch

## Where things stand — ready to resume

**Immediate next step, explicitly queued by Dave:** run the full 427-item
ai_identify batch — 2026-added SKUs, not sold, has photos, genuinely NOT
listed on eBay (filter must use `ebay_listing.status`/`listing_status`,
NOT the generic inventory `status` column — that mistake caught live this
session on `tgw202103192241400`). Query used tonight:

```sql
SELECT sku FROM catalog
WHERE sku LIKE 'tgw2026%'
AND status NOT IN ('sold','No Photos')
AND (json_extract(data, '$.ebay_listing.listing_status') IS NULL OR json_extract(data, '$.ebay_listing.listing_status') != 'ACTIVE')
AND (json_extract(data, '$.ebay_listing.status') IS NULL OR json_extract(data, '$.ebay_listing.status') != 'PUBLISHED')
```

**Most of these 427 likely already have `ai_identified: true`** — a plain
`tgw enqueue-sku ai_identify <sku>` silently no-ops on those (confirmed
live tonight). Use `tgw hint --force <sku> "<existing title>"` instead —
sets `ai_reidentify: true` and forces a real re-run regardless of prior
state. Validated end-to-end tonight (title 35→confident real content,
quality score populated). Loop over all 427 with this, not the plain
enqueue-sku path.

Dave confirmed no cost concern — Google key is funded specifically for
this. Approach was "couple at a time" validation throughout; last
checkpoint before session end was 3/3 clean on real 2026-dated items,
model migration (see below) landed mid-batch so results before that
point used the old model.

## Major work completed this session (all merged/deployed/live)

1. **#1604** — real data-loss bug fix: `mark_item_sold()` silently
   dropped a second distinct sold order once an item sold out.
   `ebay_sale` is now a list. Merged, reviewed, live.
2. **#1531/E11/E12** — root-caused why `worktree-guard.py`/
   `app-code-guard.py` never fire: confirmed **upstream Claude Code bug**
   (anthropics/claude-code#74942, #69260, #77212), not a local
   misconfiguration. No fix exists on our side. invariants.md E11/E12/E14
   marked broken-upstream (was incorrectly ✅). Detective compensating
   control (#1602) still needed, not built.
3. **#1605** — `ebay_legacy_sync` worker restored to the flake, then
   immediately generated a 451-row duplicate-job backlog (root cause:
   #1607) — caught within ~4 min, worker re-stopped, backlog cancelled.
   **Worker stays stopped** pending the real fix.
4. **#1607/#1608/PP-STATEMACHINE-001** — the actual root cause: a queue
   lease-expiry race (lease shorter than some workers' real runtime, no
   renewal, silent steal). Mitigated live (`lease_seconds` 300→600s,
   global). The bigger fix — a formal job manifest (`dedupe_key`
   required, `entity_id` required for per-item jobs, config-driven
   `priority` via new `tgw-queue-priorities.json`, `supersede` for
   force-now) with `enqueue_job()` itself as the enforcer — built, tested
   (2744+ passing), reviewed, merged, deployed live. Invariant **E16**
   written. **Still open** from #1607's original scope: the actual
   lease-heartbeat-renewal fix and `mark_succeeded()` rowcount check
   (mitigated by the lease bump, not structurally fixed).
5. **#1610/PP-MULTIMODEL-001** — Gemini 2.5→3.1 migration, finally landed
   and durably documented (Dave had asked multiple prior sessions,
   never stuck because never written down). `tgw-models.json` `default`
   profile → `gemini-3.1-flash-lite`, live-verified via real model-list
   fetch + real text call + real vision call before changing anything.
   `ebay_draft` separately switched from `gemini-3.1-pro-preview` to
   `deepseek_direct`/`default_deepseek_nonthinking` (Dave: text-only task,
   pro-preview was his own cost/quality experiment, not a requirement) —
   **not yet fully verified live**: the description-rewrite call site
   (`ebay_draft.py:538`) is gated behind product-lookup data existing
   (rarely true), so the DeepSeek swap hasn't actually been exercised by
   a real call yet. Low risk (same call_model() infra already proven
   elsewhere for DeepSeek) but worth a real test if a suitable item shows
   up.
6. **`ai_identify.py` prompt tuning** — three real quality issues found
   and fixed live, iteratively, with Dave reviewing each round:
   - SEO: titles no longer open with generic words (Vintage/Antique/
     Unbranded) — moved into the title field's own instruction.
   - Precious-metal overclaiming: `material`/`Metal`/`Metal Purity`
     fields no longer claim gold/silver without a visible hallmark —
     `Color: "Gold"` as a plain color descriptor is explicitly fine,
     only `material` composition claims are gated.
   - Stone/gem overclaiming (same pattern, Dave's suspicion confirmed
     from `"Main Stone Creation": "Natural"` appearing without evidence)
     — added calibration, but **one anomaly not resolved**: that exact
     field stayed byte-identical across 3 different prompt versions on
     the same test item (tgw202605032308315) — worth investigating
     further, smells like something other than prompt wording (caching?
     a code path not actually re-running the full schema?), not
     necessarily still broken on other items.
   - Confidence language also improved as a side effect (Dave noted
     "it looks like a..." → "it is a...").
   - Validated against a real, different category too (books/magazines)
     — SEO fix generalized cleanly, titles led with author/publication
     name consistently.

## Design work captured, NOT built (deliberately deferred)

- **#1609 / PP-STATEMACHINE-001 addendum** — run-once job semantics
  (block re-enqueue even after completion, not just while active) +
  gate-passing-rate observability (surface anomalous re-trigger rates as
  a finding, the way tonight's bug should have been caught days earlier).
- **#1611 / PP-LISTEDITOR-001** — reidentify-as-full-redraft: Dave wants
  reidentify to fully refill fields like `ai_identify` does, with a
  discard-vs-hint-existing-data toggle, result becomes an update
  candidate via R1.1's existing drift-gated apply path (extend, don't
  rebuild), with transactional logging of the propose/accept/discard
  cycle. Real feature, needs its own design/scoping session.
- **#1603 / PP-HERMES-EA-001** — coder-roster-by-specialty (Go/Rust for
  `clip-route`, Kotlin for the camera app, Flutter/Dart+HTTP for UI/UX)
  — explicitly sequenced behind proving out the Aider long-queue eval,
  which itself never got past the planning stage this session (got
  derailed by the live incident chain — see below).

## Threads that got started but interrupted, never finished

- **Aider long-queue eval** — was actively building a candidate backlog
  (#1533, #1422, #1552, #1553, #1519 identified as ready) when the SKU
  double-sale report came in and consumed the rest of the session. Never
  actually dispatched. Still a live open goal from Dave ("run sessions
  where you use both [tgw-coder and Aider] and assign by the coder's
  resume").
- Several quick-decision items were surfaced early (tracker hygiene
  batch-close, #1509 backfill approval, #1564 rofi/wofi choice, #1368
  moot-or-not) — never got Dave's answers, still sitting open.

## If interrupted reading this note

Read this file fully before doing anything — it's dense but covers a
genuinely large session. The 427-item batch (top of this note) is the
explicit next action Dave asked for. Everything else is context.

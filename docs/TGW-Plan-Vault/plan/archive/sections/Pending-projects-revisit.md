## Pending projects (revisit)

### PP-LISTEDITOR-001 — Listing Editor (item detail page restructure)

Full design in `docs/ai-plans/PP-LISTEDITOR-001.md`. Replaces Seller Hub for listing management.

- **Phase 1A**: EPS photo strip + `ebay_live` panel (live eBay data inline)
- **Phase 1B**: Editable fields + aspects form (REQUIRED/RECOMMENDED badges, dropdowns for SELECTION_ONLY); price comps inline
- **Phase 2**: Enable revision apply (`_APPLY_ENABLED=True`); fill eBay PUT stub in `revision.py:399`; extract `build_inventory_body` / `build_offer_body` from `sync.py`
- **Phase 3**: Verify Publish for 18 staged items; end+relist gate test
- **Files**: `http_server.py`, `revision.py`, `ebay/sync.py`, `tests/test_revision.py`
- **Absorbs**: todos #876 #877 #878

**Status**: todo #1062 open (p5, top of claude queue)

---

### PP-CATPICK-001 — Smart Category Assignment (group-shortlist-first, self-improving)

**Opened:** 2026-07-01 (session 39). **Status:** PLANNED — design settled, not yet built.

**Problem:** Choosing an eBay category today means picking from the full ~20k-node live
taxonomy, then separately picking a store category — slow, and a leaf category name alone
often loses the tree context that disambiguates it (e.g. "Books" appears under several
unrelated branches). `category-groups.json` (PP-PRICE-005) already has the raw ingredients
to fix this: each of the 25 groups carries a curated `ebay_categories` shortlist (3–6 IDs)
and a single `store_category_id` — but nothing in the UI or pipeline uses that shortlist to
narrow category choice today; `ai_identify`/`ebay_draft` still resolve categories via an
open-ended live Taxonomy search per item.

**Design (session 39, Dave's answers in full):**

1. **Two-step cascade, not a blended picker.** Operator confirms/changes the item's
   category **Group** first (~25 groups — same list `tgw set-template` already uses), then
   picks from that group's short `ebay_categories` list. Store category follows the group
   (1:1 today) and updates automatically with the group choice.
2. **Self-improving shortlist — this is the core of the feature, not a side effect.**
   Mistakes happen; groupings evolve. If the operator picks a category outside the current
   shortlist (via the fallback full-tree picker — reuses the Browse/Search/ID-entry
   picker built earlier this session for PP-LISTEDITOR-001), that candidate **grows** the
   group's list. If the operator explicitly removes a candidate that doesn't belong, it
   **shrinks** the list. Sometimes the right answer is an entirely new group — the picker
   is the fine-grained fallback; the grouping is the fast path that should keep getting
   faster as it absorbs real corrections. Target: 85%+ correct on the first suggested
   candidate.
3. **Candidate display needs full tree context, not just a bare name.** Each candidate in
   a group's shortlist gets a friendly name (backfilled from the category-tree cache built
   this session — zero extra API calls) **and its full ancestor branch shown from the
   first level down** (not just the immediate parent), since a bare leaf name is exactly
   the ambiguity problem this feature exists to solve. Optional short operator-authored
   disambiguation hint per candidate (e.g. "1105 — Fiction & literature" vs "29223 —
   Nonfiction"); hints are opt-in, blank where the name+path is already clear.
4. **AI assignment becomes group-shortlist-first by construction.** `ai_identify` picks
   `category_group` from `ai_hint` keyword match (as it already can via `set-template`),
   then the top candidate from that group's shortlist — no live Taxonomy call needed for
   the common case. Live search remains the fallback only when the group's shortlist
   doesn't fit. This makes `ebay_draft._validate_category_suggestion()`'s live
   `get_category_suggestions` call (found in the session-39 API audit — fires on **every**
   drafted item purely to log an "agreement" QA metric that already duplicates
   `ai_identify`'s call moments earlier) unnecessary: a category chosen from a curated,
   operator-maintained shortlist is trustworthy by construction, so the separate live
   validation ping can be removed as part of this work.

**Data model change:** `category-groups.json` group schema gains a `category_candidates`
array (superseding the bare `ebay_categories` ID list): `[{id, name, path, hint?}]`. `path`
= full ancestor breadcrumb from the tree cache (session 39). Group's `store_category_id`
stays 1:1 for now — no evidence yet that a group needs multiple store categories; revisit
if operator corrections show otherwise.

**New endpoints needed (build phase, not yet written):**
- `PATCH /api/category-groups/{group_key}/candidates` — add/remove a candidate (id, name,
  path resolved server-side from the cached tree); every change appended to an audit log
  (mirrors the `identification_history` pattern — never silently overwrite).
- Reuse existing `/api/ebay/category-node/{id}`, `/api/ebay/category-children`,
  `/api/ebay/category-search` (session 39, PP-LISTEDITOR-001) for the fallback full-tree
  picker and for resolving names/paths when backfilling candidates.

**Build phases (not yet started):**
- **Phase 1** — Backfill `category_candidates` (name + path) for all 25 existing groups
  from the tree cache; no live API calls needed (tree already cached).
- **Phase 2** — Item-detail UI: replace the current category field with the two-step
  Group → shortlist cascade; fallback picker triggers a grow/shrink action on the group.
- **Phase 3** — `ai_identify` / `ebay_draft`: group-shortlist-first resolution; remove
  `_validate_category_suggestion()`'s redundant live call once the shortlist-first path is
  confirmed accurate in practice (don't remove the safety net before the replacement is
  proven — run both for a trial period, compare agreement rate, then cut over).
- **Phase 4** — Operator hint authoring UI (optional per-candidate disambiguation text).

**Cross-references:** PP-PRICE-005 (category-groups.json origin), PP-INTAKE-001 Phase 5
("template self-update" — already anticipated this exact direction), PP-LISTEDITOR-001
(shares the category-tree cache + Browse/Search/ID picker built session 39).

---

### PP-ACTIONCONSOLE-001 — Item Detail Action Console Redesign

**Opened:** 2026-07-01 (session 39). **Status:** PLANNED — design in progress, not built.

**Problem:** The item detail page currently exposes ~12 separate action buttons across
two sections (Pipeline Tools: Re-identify, Re-draft, Re-upload photos, Re-price, Sync
from eBay; Publish gate: Stage/Re-stage, Approve for Listing, Withdraw Approval, Publish
Now, Update Listing, End Listing) plus a pipeline status bar (Draft → Staged → Approved →
Live) styled as button-like chips that read as clickable when they're purely
informational. Dave: "the operator really should only need 3 [buttons]... Whatever
pipeline steps are necessary to perform an action we do that part. The operator clicks a
button." The pipeline is supposed to be automated — most of these buttons exist "only
because we don't program this correctly."

**Guiding principle (Dave, session 39):** this is a workflow-clarity redesign, not just a
button count reduction. "Put all of the relevant information and tools right where they
are needed... for all day to day tasks without a bunch of clutter and scrolling. The
detailed pipeline controls may exist elsewhere, but when listing the operator needs to
concentrate on getting items listed correctly." The item detail page's job during normal
listing work is singular: help the operator get this item listed correctly, fast, with
nothing competing for attention. Granular/troubleshooting pipeline controls are legitimate
tools but belong on a **separate surface** (an ops/admin page), not on the primary
per-item listing page — this settles the earlier open question about where the
troubleshooting buttons (Re-identify, Re-upload photos, Sync from eBay, manual Stage) end
up: relocate them off this page entirely, they don't need to live here even collapsed.

**Design discussion so far (Dave, session 39 — iterative, not fully settled):**

1. **"Save Draft" isn't a new action** — draft_listing already functions as a notepad
   that the operator and AI workers both write into incrementally (item specifics,
   fields) ahead of an eventual update/publish. Manual field edits (title, price,
   category, condition, aspects) already auto-save immediately via PATCH as edited today
   — that mechanism is correct and doesn't need a separate "Save" step.
   **New idea surfaced**: add a genuine free-text **operator notes field** — a plain
   notepad separate from anything eBay-facing. Not yet scoped; flag as a small follow-on.
2. **Approve vs Publish Now — confirmed, not "almost duplicates" to collapse away:**
   - **Approve** = mark ready for the existing rate-limited dole-lister worker to pick up
     in its own time (today's `set_ready` action) — no live eBay call yet.
   - **Publish Now** = skip the line, perform every necessary step (stage if needed,
     etc.) and go live on eBay immediately (today's `ebay_publish`, already backed by the
     session-38 auto-chain retry-until-staged logic — the backend automation Dave wants
     mostly already exists; this is primarily a UI consolidation, not new plumbing).
3. **Troubleshooting/pipeline-mechanic actions (Re-identify, Re-upload photos, Sync from
   eBay, manual Stage/Re-stage)** — Dave's preferred direction: rather than a static
   always-visible toolbar, surface these as **contextual buttons attached to the specific
   pipeline log entry that needs them** ("If there is some action that needs to be taken
   for something why not put the button next to the log entry if we are hesitant to
   automate it?"). Alternative he also floated: a collapsed picklist-with-trigger at the
   bottom with fuller descriptions. **Neither fully designed yet** — the contextual-log
   idea in particular requires the pipeline log renderer to know which action(s) apply to
   which log line/state, which is a real design task of its own before implementation.
4. **Archive / Delete item / End Listing (once live) — CONFIRMED to stay as first-class,
   always-visible actions** (Dave's follow-up correction after initially lumping them in
   with the buttons to trim: "I was a little harsh on the buttons we still need
   delete/archive/ and once listed end on eBay"). These are irreversible operator
   decisions, not automatable pipeline mechanics — do not fold into the 3-button
   consolidation or the contextual-log-action idea.
5. **Status indicators — bigger rethink than a restyle:**
   - There are really **two distinct "what am I looking at" states** that need surfacing
     clearly: viewing the **draft** (unpublished `draft_listing`) vs. viewing what's
     **actually live on eBay** (`ebay_live`/`ebay_listing`). Dave: "We need a way to see
     both, but that is another issue" — flagged as a separate, not-yet-designed problem.
   - **"Staged" should not be an operator-visible concept at all** — it's an eBay
     Inventory API implementation detail the operator can't see or act on. Drop it from
     the operator-facing pipeline bar.
   - **"Approved" should just toggle based on whether Publish Now was pressed** — once
     live, Approved is subsumed by Live and doesn't need its own indicator state.
   - **Preferred long-term direction — eliminate separate status badges entirely; make
     the action buttons themselves the indicators** (stateful/smart buttons): e.g. press
     "Publish", and once published that same button slot becomes "End on eBay" and the
     live listing data section appears inline. Same pattern per function. This is the
     "smart interface as the indicator" — not yet designed in detail (which button-slots,
     what each transition looks like) — a real design pass needed before building.

**Immediate safe changes made this session (low-risk, no workflow change):**
- Pipeline status bar restyled to a flat breadcrumb (no button-like border/background
  box) so it reads as status, not as clickable controls.
- Dropped "Staged" from the operator-visible pipeline bar per Dave's explicit direction
  (point 5 above).
- Archive / Delete / End Listing left untouched — confirmed to stay as-is.

**Explicitly NOT done yet (needs a dedicated design pass before building):**
- The 3-button (Save Draft / Approve / Publish) consolidation itself — "Save Draft"
  turned out not to need a new mechanism, and the troubleshooting-button relocation
  (contextual-log vs. picklist) isn't settled, so no button removal/relocation was done
  this session beyond the status-bar restyle.
- Stateful/smart buttons replacing status indicators.
- Draft-vs-live view toggle — **principle settled 2026-07-01 session 40, see
  "Design principle settled — state-driven interface" below**; transition table still
  to design.
- Operator notes field.

**Next step when picked back up:** settle the contextual-log-action design (what
triggers what, where per-log-line action metadata comes from) before touching the
Pipeline Tools button set; the status-indicator stateful-button rework and the
draft-vs-live question resolved into one state-driven design (see below).

**Refinement (Dave, same session, follow-up):** the stateful-button direction is not a
new pattern to invent — it's an **extension of behavior that already exists**: today,
once an item is live, "Publish Now" already becomes "Update Listing" and "End Listing"
appears. The redesign is just applying that same state-driven transformation
consistently to every action slot, not building something new from scratch.

- **Indicators disappear as separate elements** — they become **color and function
  changes on the buttons themselves**, driven by item status. Functions and indicators
  consolidate into one surface (fewer elements on screen, not two parallel systems
  saying the same thing).
- **Dave's stated tolerance for iteration**: "we have to work out the logic correctly, on
  a day to day operational basis there is less noise for the operator and it is clear
  what is going on. If it becomes confusing from operator perspective, we adjust a little
  either process or indicators." — i.e. ship a reasonable first pass, watch real daily
  use, adjust the transition logic or the visual language as needed. This isn't expected
  to be perfectly speced up front.
- **The troubleshooting/pipeline-mechanic button set collapses conceptually to one
  operator-facing idea**: *"this AI result sucks, try again."* Whatever specific pipeline
  stage actually needs to be re-run (re-identify, re-draft, re-price, etc.) is an
  implementation detail the button figures out and executes — the operator doesn't pick
  a stage, they just say "this isn't right, redo it."

**Design principle settled (Dave, 2026-07-01 session 40) — state-driven interface:**
The "draft-vs-live toggle" open item is now resolved as a *principle*, not a widget:
**state drives the interface; every control is also an indicator; compaction without
losing anything.** The interface reflects the item's state so the operator gets it at a
glance without separate status elements, and the correct tools appear in useful places
as they become relevant. Color language on the buttons themselves: green = good/settled,
yellow = working/pending, red = error.

Worked example — the publish lifecycle (one instance of the principle, **list is
deliberately non-exhaustive**; more instances to be enumerated in the design pass with
Dave):
1. *Draft state*: operator sees the editor. Button: **"List on eBay"** — pressing it
   saves the draft, runs every pipeline step needed (stage, photos, etc.), publishes.
   Pipeline stages are never operator-visible.
2. *Goes live*: a **Live Listing tab appears** — read-only, showing the published offer
   as eBay has it; the draft sits behind it, now matching live. The tab's *existence* is
   the "this item is live" indicator. "List on eBay" becomes **"Update Item"**;
   **"End Item on eBay"** appears.
3. *Draft edited again*: draft diverges from live → "Update Item" shifts color (pending
   delta). **Clear draft / reset local state from live** are the escape hatches here
   (= discard the revision delta / re-pin the live baseline in PP-REVISION-001 terms).

Notes:
- The read-only live-listing view is NOT a separate page/feature — it's a state
  manifestation (tab exists only when the item is on eBay).
- This dissolves the earlier sequencing question ("stateful-button rework should follow
  the draft-vs-live toggle design") — they are the same design.
- **Build dependency**: "Update Item" on a live listing = revision apply →
  PP-LISTEDITOR-001 Phase 2 (fill eBay PUT stub, extract body builders,
  `_APPLY_ENABLED = True`) is the enabling backend and remains the build prerequisite.
- Remaining design work before building: the concrete state → button-slot transition
  table (the "work out the logic correctly" pass), plus enumerating the other instances
  of the principle across the page with Dave.

**Design pass (Dave + Claude, 2026-07-01 session 40, todo #1083) — settled points:**

1. **Status bar removed entirely.** The session-39 breadcrumb restyle isn't enough —
   the bar displays nothing the buttons and tabs don't already say. Gone. The one
   thing of value it could carry becomes a real tool: a **"View on eBay" link**
   (`ebay.com/itm/<listing_id>`), appearing when live, on/near the Live Listing tab.
   **Correction (Dave):** the element in question is not just a bar — it's the
   collapsible **"eBay Live Data" dropdown panel** (`<details>` section showing the
   raw eBay mirror). The *element* goes away but its *content graduates*: that
   dropdown's content IS the Live Listing tab in the new design — promoted from a
   collapsed section to the first-class read-only live view. Nothing is lost.
   Dave confirmed after reviewing the page: "most of it is the page we are making
   for the live listing." Two content dispositions from the same review:
   - **Pricing History → left column, merged into the existing display there**
     (Dave's call): a pricing display already exists in the left log/history
     column — don't add a second one, merge to ONE clean pricing history section
     alongside Identification History and the jobs trail.
   - **Comps display was redundant** — shown more than once; kill the duplicates,
     comps data appears once (within the merged left-column pricing section).
2. **One action line.** All pipeline buttons + Save Draft collapse into a single row of
   state-appropriate actions. The "Publish gate" vs "Pipeline Tools" section split dies.
   Per-state contents:
   | State | Action line |
   |---|---|
   | Intake (no draft) | **Prepare Listing** · Archive · Delete |
   | Draft ready | **List on eBay** · Approve · Reset Draft · Archive · Delete |
   | Working | **Listing…** (yellow, disabled) · Archive · Delete |
   | Live, in sync | **Update Item** (quiet) · End Listing · Archive · Delete · View-on-eBay |
   | Live, edited | **Update Item** (yellow) · Reset Draft · End Listing · Archive · Delete |
   | Error | **Retry** (red) · state-appropriate rest |
3. **Reset Draft has dual semantics by state:** not yet live → regenerate draft from
   canonical fields via ebay_draft (discard operator edits); live → re-pin draft from
   live (discard revision delta). Same button label, different backing op. Hidden in
   live-in-sync (nothing to reset).
4. **End eBay Listing** — confirmed required, red-bordered, visible in all live states.
5. **The principle is platform-wide** (house style, not an item-detail feature):
   fulfillment/warehousing pages get the same state-driven, task-appropriate,
   decluttered treatment. PP-ACTIONCONSOLE-001 is the pattern-setter.
6. **Pipeline jobs section = the contextual-repair surface** (settled in principle):
   it stays the quiet status trail; repair buttons appear ONLY on actionable failure
   states (e.g. red "Retry"/re-enqueue on a dead-letter line — today a manual
   enqueue_job() from a shell). **Zero-clutter guarantee: happy path shows zero
   buttons.** Which failure states get which button: enumerate during build. This
   answers the earlier console-vs-log-line question: log lines get pipeline-mechanic
   repairs; the primary action slot goes red only when the failure blocks the
   operator's task (and can jump to the offending log line).
7. **Repair actions double as a data-collection / pipeline-improvement loop:** each
   click records failure signature + action taken + outcome (queue_jobs ledger mostly
   has this; needs the operator-action annotation linked to the retry job). Same
   manual fix repeatedly succeeding on the same failure type → promote to automatic
   retry policy (button argues for its own elimination); repeatedly failing →
   aggregated root-cause evidence. Completes the self-healing loop: auto-detect →
   surface → self-service resolution → **learn from the resolution**.

**Final three calls (Dave, 2026-07-01 s40) — design now buildable, no opens left:**
- **Sold state:** the live listing view becomes a **sold listing view and moves to the
  front tab**; the editor stays, just behind. **Relist** button appears. Inventory
  record shows the qty decrement (−sold qty; consistent with mark_item_sold
  qty-decrement behavior — sold only at qty 0).
- **Approve = toggle near primary** ("queue for auto-listing" checkbox next to List on
  eBay) — it's a scheduling preference, not a distinct action; action line keeps one
  real button.
- **"Update Item" when in sync: grey-visible** — slot never jumps; the grey itself
  signals in-sync.

---

### PP-RESCUE-001 — TGW Rescue Live ISO

`nixosConfigurations.tgw-rescue` in the canonical flake (`~/tgw-flake`): minimal NixOS live USB with `claude-code`, `btrfs-progs`, `postgresql` client, `age`, `rsync`, and a restore script pre-loaded.

**Design** (processed from suggestions 2026-06-24):
- `nix build .#tgw-rescue` → write to USB; boots to recovery shell
- Stays in sync with the platform via the flake — not a separate distro
- **AI assistance role**: operator consultation tool for unexpected mid-recovery conflicts where a second opinion helps; NOT a guided install for an uninformed user (Dave knows the system)
- Aider handles the scripted mechanical restore path (known-good sequence: `nixos-anywhere → pg_restore → rsync → tgw health`); Claude handles unexpected failures
- API keys either baked in via age-encrypted secrets on the USB, or prompted at boot
- Primary DR target: `nixos-anywhere + flake` builds a usable system; hardware gaps cannot be resolved until better test hardware is available

**Blocker**: PP-NIXOS-001 production stable + better test hardware.

---

### PP-AGENTIC-PRICE-001 — Agentic Comp Search for Auto-Pricing

The platform differentiator: most resale tools price by category average; TGW prices by agentic comp search with operator-tunable search terms and self-improvement from corrections.

**Problem**: Current pricing fallback chain (brand+MPN → full title → category+short → category name) degenerates badly for specific/obscure titles. Good comp search terms are what a *buyer* would type — shorter, focused on object type, brand only when it has search recognition (Microsoft YES, Ladco NO).

**Design (four phases):**

- **Phase 1 — Manual `search_terms` field** (build as PP-LISTEDITOR-001 sub-task):
  - Operator-editable field on root item (`item.search_terms`)
  - Stage 0 in `suggest_price()` fallback chain
  - "Save & Re-price" button in comp panel — fast iteration loop
  - Every manual entry is a labelled training example

- **Phase 2 — Candidate generation + Browse API scoring**:
  - Single LLM call (Gemini Flash Lite) generates 4–5 candidate queries ranked specific→general
  - Run all in parallel against Browse API; score by count × relevance; pick winner
  - Handles brand recognition automatically

- **Phase 3 — Agentic loop with Browse API as tool**:
  - Model gets `search_ebay(query)` tool; iterates 3–4 steps: try → read → judge → refine or accept
  - Relevance judgment: "are these listings comparable to [item description]?" — Claude Sonnet

- **Phase 4 — Learn from corrections**:
  - Collect Phase 1 `search_terms` as few-shot training examples
  - Input: title + category + what automatic chain tried + result; Output: operator correction
  - 50–100 examples → prompt few-shots capturing domain judgment
  - Price variance as automated relevance signal (high variance = mixed comp set = bad query)

**Technical pieces needed:**
- Browse API relevance scorer (count + semantic match + price variance)
- LLM judge prompt: "Are these listings comparable to [item]? Rate 1–5."
- Parallel Browse API query runner
- Operator correction log (captured by `search_terms` field)

**Blocker**: Phase 1 requires PP-LISTEDITOR-001 Phase 1B (inline comp panel + Save & Re-price).

---

### PP-PORTABLE-CATALOG-001 — Portable / Satellite Catalog

#### Problem
The tablet and spare intake machine need read-access to item catalog + thumbnails to work as intake/browsing stations. Currently `tgwcatalog.db` lives only on the master machine. Syncthing can sync it, but there is no operator-friendly command to prepare a sync-ready bundle, and the catalog needs a stable export shape that works on a machine with no live PostgreSQL.

#### Design (Phase 1 — Syncthing-sync, no conflict resolution)
- `tgw export-catalog <dest>` command: copies `tgwcatalog.db` (55K rows) + `thumbnails/<SKU>.jpg` subset to `<dest>/`
- Syncthing watches `<dest>/` on master and syncs to client machines automatically
- Client machines: read-only browser (tgw-http or MC); writes go back to master via tgw-http when online
- **Phase 1 scope**: export only (no conflict resolution, no return path); Syncthing handles transport
- Snapshots the current catalog state; `tgw export-catalog --incremental` could be added later

#### Architecture
```
master
  tgwcatalog.db + thumbnails/  ← tgw export-catalog → export/
                                                              ↓ Syncthing
                                                         tablet, spare machine
                                                          tgw-http (read-only mode)
                                                          MC extfs tgwcatalog
```

#### Phases
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | `tgw export-catalog <dest>` + Syncthing transport (operator configures Syncthing) | ✅ **DONE (session 18)** — `src/tgw/catalog_export.py`; 8 tests; live verified |
| 2 | Flutter offline-first client: snapshot+copy-to-sandbox; sqflite outbox; connectivity_plus + workmanager Android flush | Future (PERPLEXITY-006 design complete; needs Syncthing API key for API-driven export trigger) |
| 3 | Conflict resolution, per-row change-log, merge audit trail | Future |

#### Phase 2 design — Flutter offline-first client (PERPLEXITY-006)

**Critical pattern — never open the synced file directly:**
```
Syncthing syncs → catalog.db (write-locked or in-use mid-sync → corruption risk)
                        ↓ app startup
                 snapshot + copy to app-private storage
                        ↓
                 open private copy (sqflite) ← safe to read/write
                 offline outbox table (pending mutations)
                        ↓ connectivity restored
                 flush outbox → POST /api/items/{sku} on master
                 server returns latest export → replace private copy
```

**Library stack:**
- `sqflite` + `sqflite_common_ffi` — SQLite on Android + Linux desktop
- `sqlite3` package (not sqlite3_flutter_libs, which is deprecated for 3.x)
- `dio` + `dio_smart_retry` — HTTP client with automatic retry
- `connectivity_plus` + health ping (`GET /api/health`) — connectivity detection
- `workmanager` — Android background flush scheduling
- `flutter_secure_storage` — token/secret storage; requires `libsecret-1-dev` on Linux

**Server-side snapshot export:**
Use `sqlite3.Connection.backup(dest)` for atomic SQLite copy (avoids mid-write corruption).
Endpoint: `GET /api/catalog/snapshot` → streams `tgwcatalog.db` snapshot.

**Sync-conflict resolution worker (DONE 2026-06-13):** `src/tgw/sync_conflict.py` + 47 tests.
Decision tree (see module docstring):

- `identical` → auto-discard (byte-for-byte match)
- `divergent_pipeline` → move to `inbox/review/`, priority-15 todo (conflict has unique/different TGW pipeline data: status `sold` vs `In Stock`, unique `ebay_listing`, etc.)
- `divergent_legacy` → move to `inbox/review/`, priority-65 todo (only obsolete M1/M2/CSV fields differ + stale-default status; low operator urgency)
- `divergent` → move to `inbox/review/`, priority-30 todo (general divergence)
- `no_canonical` → move to `inbox/review/`, priority-45 todo (canonical missing)

Zero-data-loss invariant: nothing is auto-deleted except byte-identical copies. "keep-newer" and "keep-larger" are NOT safe auto-resolution rules — mtime/size do not prove content safety. Semantic JSON analysis does.

**Design principle — zero data loss (Dave, session 19):** A `.sync-conflict-*` file is Syncthing's
*safety* mechanism, not an error. Syncthing never resolves indiscriminately — it completes the
sync and says "hey, look at this," which is precisely why it's the right choice. The worker must
honor that:
- The conflict copy is **usually redundant** (identical to, or strictly older-with-no-unique-
  content vs, the canonical file) → safe to discard.
- But **sometimes** the conflict copy is a local edit made *before* the remote synced — unique
  content that blind discard would permanently lose.
- So the worker **must compare** conflict-copy vs canonical and auto-discard *only when provably
  redundant*; anything with divergent/unique content is **flagged for operator review, never
  auto-deleted**. The invariant is: no path through this worker can cause data loss.
- (Live test case left in the vault on purpose: `.obsidian/community-plugins.sync-conflict-…json`
  — observe how a future worker classifies it before building auto-resolution.)

#### Dependencies
- `tgwcatalog.db` (already built, 55K rows)
- Thumbnail cache (already built, 54K thumbnails)
- Phase 2+: PP-PYIPC-001 (Syncthing REST API), Syncthing API key

#### Status
Plan section added session 18. Phase 1 in Round 4.

---

### PP-PLASMA-001 — KDE Plasma 6 Dual-Desktop Integration

#### Vision
TGW runs two desktop environments: Qtile (primary operator workstation — tiling, Python hooks, TGW bar widgets) and KDE Plasma 6 (general purpose — familiar, full-featured, Firefox, GLabels, LibreOffice). Both are first-class citizens. Plasma handles day-to-day use and GUI app launching; Qtile handles warehouse operations, agent sessions, and pipeline monitoring.

#### Motivation (session 16 suggestion)
The TGW operator workstation will rely heavily on the KDE framework even on Qtile. KDE apps (Dolphin, Gwenview, Konsole, KDialog, KDE Connect, GLabels) are used in the warehouse workflow. Running Plasma 6 in parallel gives a familiar environment for non-TGW tasks without compromising the Qtile operator experience.

#### Integration opportunities
| Area | Qtile | Plasma 6 |
|------|-------|----------|
| File management | F2 menu → Dolphin launch | Dolphin natively |
| Image viewing | chafa in MC / Gwenview launch | Gwenview natively |
| Clipboard relay | KDE Connect (tgw.source ic_*) | Plasma clipboard sync |
| Notifications | notify-send / dunst | Plasma notification daemon |
| GLabels (barcode) | Launch via keybinding | Plasma app launcher |
| Terminal | Konsole / scratchpad | Konsole natively |
| Quick switch | Super+T TGW mode in Qtile | Plasma Activities |

#### NixOS dual-desktop on NixOS
On NixOS, both WMs are declared in the same flake:
```nix
services.xserver.windowManager.qtile.enable = true;  # operator session
services.desktopManager.plasma6.enable = true;         # general session
```
Both available at login; user selects per-session.

#### Phases
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Shared config: dunst notif theme, Konsole profile, Dolphin TGW bookmarks | Future |
| 2 | Qtile→Plasma clipboard bridge via KDE Connect (already works via tgw.source) | Future |
| 3 | NixOS dual-desktop declaration in flake | Depends on spare machine validation |

#### Status
Plan section added session 18. No code this round. Design/tracking only.

---

### PP-PLANDB-001 — Database-Driven Plan Builder (design discussion needed)

#### Concept (session 18 — discuss before building)
Instead of a monolithic Markdown plan file, the plan at any point in time is **rebuilt on demand** from a task + relationship database. Each PP-* item, work track, and todo entry lives in the DB with explicit relationships (blocks/depends-on). The plan document becomes a rendered view, not the source of truth.

**Agent delegation extensions:** The DB can generate self-contained `CLAUDE.md` and `gemini.md` files for any delegated task — baked with exactly the context that agent needs, no more. Useful for employees, mechanical turks, or future agent roles. The todo tracker (PP-TODO-001) is the embryonic form of this; PP-PLANDB-001 is the full realization.

**Dave's note:** "tgw plan builder" — discuss the scope and design before implementing. The todo tracker is already moving in this direction; the question is whether to extend it or build a separate plan-reconstruction layer.

#### ✅ DECIDED 2026-06-12 (session 28) — Option C: DB owns tasks, generated taskboard

Design questions answered:
- **DB vs Markdown:** the DB owns *tasks* only; design prose stays hand-authored Markdown in the
  master plan (the prose was never the drift problem). Task tables leave the plan entirely and are
  rendered into a **wholly-generated companion file** `plan/TGW-Taskboard.md` (one writer — no
  Syncthing mixed-edit conflicts; `/form/todos` renders the same DB for tablet).
- **PP-* items:** stay as plan prose sections; todos link to them via `pp_ref` + `plan_anchor`.
- **Relationships:** `depends_on`/`blocks` (blocker badges on the taskboard) + `pp_ref`. That's it
  for now; tracked-by/delegates-to are covered by the existing `agent` column.
- **Generated agent context:** `tgw todo brief <id>` — self-contained per-agent task spec (the
  Aider message-file pattern from next-process.md) built from the todo + linked plan-section
  extract. Minimal context, link out for more — prevents bloat.
- **Version history:** tasks in PostgreSQL (raises the stakes on PP-BACKUP-001 Phase A #61);
  rendered taskboard lands in git via vault commits — render history for free.

**Write-gateway architecture (Dave, 2026-06-12):** Dave no longer edits the plan directly — all
input flows through `inbox/` + `tgw suggest`, i.e. the **PP-DOCFLOW-001 project admin is the
single write-gateway for both surfaces**: it classifies submissions → creates todos (setting
`pp_ref`/`depends_on` when confident, review-flag when not) → appends prose to the plan only for
design/rationale → the render job regenerates the taskboard. The drift channel is structurally
closed, not discipline-closed. `tgw plan check` (Phase 3) becomes a safety net on the *admin's*
work, and its mismatch reports feed the long-term improve-the-admin loop (misfile → review flag →
correction → prompt/rule update). Script what we know; dump the rest into the inbox for the admin.

**Phases (Round 7 todos #109/#110/#112):**
| Phase | Scope |
|-------|-------|
| ✅ 1 | **DONE 2026-06-12 (session 29)** — `todo_items` gained `pp_ref TEXT`, `depends_on INT[]`, `plan_anchor TEXT` (migration applied); `tgw todo --pp/--depends/--anchor` on `--add` + `--set-meta ID` for existing items; `tgw todo brief <id>` self-contained task spec (todo body + master-plan section extract + dependency status + constraints, Aider message-file pattern); classify-suggestions LLM sets `pp_ref` when confident (format-validated, hallucinated refs dropped); pp_ref backfilled on 23 existing todos from body text; pp_ref + blocker badges in `tgw todo` listing |
| ✅ 2 | **DONE 2026-06-12 (session 29)** — `tgw.plan_render` module + `tgw plan render` → generated `plan/TGW-Taskboard.md` (per-agent tables ID/pri/size/task, blocker badges from open `depends_on`, Obsidian links to plan headings via `pp_ref`/`plan_anchor` with auto heading resolution, done-this-week section, atomic write); coalesced `plan_render` queue job on every todo mutation (dedupe `plan_render:pending` + 30s not_before, catalog-rebuild pattern); `plan_render` worker (`tgw-worker@plan_render.service` — **operator must enable, admin todo #119**); `check_taskboard()` staleness warning in `tgw health` (yellow when tracker changed >10 min after last render). 20 new tests; 637 passing |
| 3 | `tgw plan check` plan↔tracker reconciliation in the session-start ritual (todo #112, depends_on=[110] now cleared) |
| 4 | Only if ever needed: generated PP-status lines inside plan sections |

#### Dependencies
- PP-TODO-001 (already built — provides task storage; PP-PLANDB-001 extends it)

---

### PP-BACKUP-001 — Organized Backup and Disaster Recovery Architecture

#### Problem (session 16)
`trader-grims-backup` repository is archived but still occupies repository space and mental overhead.
A custom unified backup/archiving/restoration/disaster-recovery suite is needed to replace it and
encompass all backup concerns (config, secrets, ItemData, logs, databases, system state).

#### Design goals
- **Backup repository separation**: Move `trader-grims-backup` to a separate, independently managed
  repository so TGW platform repository is uncluttered
- **Unified DR suite**: Replace fragmented backup logic with a single source of truth for:
  - Regular incremental backups (ItemData, databases, config)
  - Archival policy (old history, sold items, legacy data)
  - Restoration (point-in-time recovery, disaster scenario planning)
  - Verification (backup integrity checks, restore dry-runs)
- **Integration with PP-NIXOS-001**: The NixOS rebuild strategy enables atomic system restore
  from config + backup snapshot

#### Scope
- Phase 1: Extract `trader-grims-backup` to separate repo; audit its current usage
- Phase 2: Design unified suite (modules: backup, archive, restore, verify)
- Phase 3: Implement per-module; wire into systemd timer and health checks

#### Dependencies
- PP-NIXOS-001 (system rebuild context)

#### Status
**PLAN APPROVED 2026-06-11 (session 24)** — full plan at `plan/PLAN-backup-dr.md`
(approved by Dave same day, after amendment through all 13 session-24 suggestions;
Phase A build unblocked = todo #60). Host audit found: local snapshot tier healthy
(dedicated 699 G disk, current); cloud tier **27 days stale** (manual rclone only);
**ledger has zero dumps**; **secrets have no backup at all**. Phase A (MX-now: pg_dump
timer, scheduled rclone with --backup-dir trash, gpg-encrypted secrets bundle,
backup-freshness health check, restore drills, archive policy) → Phase B (repo split +
restic engine + `tgw backup` CLI) → Phase C (declarative in the Nix flake — resolves
NixOS plan R9 properly; recovery equation extended). Round 6 #54/#55; todos #60/#61.

---

### PP-DEADLETTER-001 — Dead-letter triage: warn+requeue instead of terminate

#### Problem (observed 2026-06-06 session 9)
Several failure types routinely end up in `dead_letter` when they should instead emit a
notification and requeue — dead-letter requires manual operator intervention to clear,
which builds up silently. The current troubleshooting workflow is: `tgw health` → see
dead_letter count → run SQL to identify → categorize → manually cancel/requeue.

#### Known dead-letter types that should be warn+requeue

| Error pattern | Current behaviour | Better behaviour |
|---------------|------------------|-----------------|
| `token is expired` (ebay_sync, ebay_legacy_sync) | dead_letter after 5 attempts | warn + back off 15min + requeue; clear on next successful sync |
| `section not found in plan` (pm_intake) | dead_letter | warn + log + skip (don't block other inbox items) |
| `no eBay photo URLs yet` (ebay_stage) | dead_letter | retry with longer backoff (ebay_upload may still be running) |
| `Directory not empty` (catalog_rebuild) | dead_letter | retry immediately (transient OS race) |
| `ReadTimeout` / `LEASE_EXPIRED` (ebay_draft) | dead_letter | retry with fresh lease |

#### Dead-letter types that ARE correct

| Error pattern | Reason to keep as dead_letter |
|---------------|-------------------------------|
| `HardFailure: no ebay_category_id` | Needs operator/AI intervention to fix item data |
| `HardFailure: eBay rejected (25002/25021/25709)` | Needs code fix or item data fix |
| `HardFailure: item specific value too long` | Needs item data fix |

#### Design — ✅ IMPLEMENTED (session 13)

**`classify_dead_letter(error_text: str) -> tuple[str, int]`** in `worker_base.py`:
- Pattern matches error text (case-insensitive substring) against `_TRANSIENT_ERRORS` list
- Returns `('requeue', delay_seconds)` or `('dead_letter', 0)`
- `requeue_with_backoff(job_id, owner, delay, error)` in `state_machine.py`:
  transitions running→retry_wait; resets `attempt_count=1`; sets `error_code='TRANSIENT'`
- `QueueWorker._process()` intercepts exhausted-retry `Exception` path:
  checks `attempt_count >= max_attempts`, classifies error, reschedules instead of dead-lettering

**Transient patterns implemented:**
| Pattern (substring, case-insensitive) | Delay |
|---|---|
| `token is expired` | 900s (15 min) |
| `no ebay photo urls yet` | 600s (10 min) |
| `directory not empty` | 30s |
| `readtimeout` | 120s |
| `lease_expired` | 120s |
| `connectionerror` | 120s |

**HardFailure** (raised explicitly) still goes directly to dead_letter — no change.
`section not found in plan` (pm_intake) handled separately in pm_intake.py (warn+skip).

**Remaining work — ✅ DONE 2026-06-12 (session 29, todo #94):**
- ✅ T/H split: `dead_letter_errors()` in `state_machine.py` + `classify_dead_letter_errors()`
  in `health.py`; `check_postgres` detail now reads
  `dead_letter=33 T0/H33 [ai_identify:12(T0/H12), …]` and returns
  `dead_letter_transient/hard/classified`; `tgw_queue_status` MCP tool returns the same
- ✅ Zero-work watchdog (the ebay_sku_migrate silent-stall pattern): `zero_work_queues(h)` —
  worker heartbeat alive + eligible queued jobs (not_before excluded, so self-scheduling
  workers don't false-positive) waiting > `zero_work_stall_hours` (config, default 4.0) with
  zero succeeded transitions in the window → yellow WARN in `check_postgres` + MCP
- `notify.warning()` emit on transient requeue path was already done (session 14)

### PP-HINT-001 — AI hint + eBay enrichment (revisit required)
- First iteration shipped 2026-06-03: `ai_hint` field, `tgw hint` command, hinted vision prompt
- **Known gaps to address:**
  - `tgw requeue` bulk command: filter-based batch re-queue (e.g. "all items with photos but no title") for catalog maintenance — without triggering eBay listing pipeline
  - eBay Browse API enrichment in `ebay_draft`: search similar active listings by title, extract common aspects and category signal to supplement AI-generated specifics
  - ✅ Full item history / hint trail (2026-06-08): `identification_history` list in item JSON; `append_history_event()` in `items.py`; `ai_identify` + `hint_set` event types; `tgw hint-trail <sku>` CLI display
  - eBay Marketplace Insights scope (`buy.marketplace_insights`): contact eBay Developer Support directly (limited-release, no self-service); Finding API discontinued 2025 — not an option
  - Revision of already-identified items: `tgw hint --force` works but downstream ebay_draft/ebay_draft re-runs need to be aware of published state (don't auto-push changes to live listings)
  - Tuning: run difficult items through, observe results, adjust prompt and hint format
  - **Shipping profile at intake**: operator sets shipping profile during physical processing based on item size; simple `tgw` command or camera app field sets `shipping_profile` on the item JSON at intake time, overriding the per-category default (FC4). Low-touch: one field, one tool adjustment. See PP-DEPLOY-001 for camera app context.

### PP-QUALITY-001 ✅ COMPLETE (2026-06-04)
`tgw/listing_quality.py` — `score_draft()`, 7 signals, 100-pt scale. Signals: title length (10), brand in title (25), MPN in title (10), required specifics % (15), recommended specifics % (5), photo count ≥3 (20), description words (5), comp count (10). Scored in `ebay_draft` + rescored in `ebay_price`; `tgw staged` Q/PC columns; `tgw quality <SKU>` CLI.

### PP-PRICE-001 ✅ COMPLETE (2026-06-03)
`tgw/ebay/pricing.py` + `ebay_price` worker (auto-enqueued by `ebay_draft`). Browse API 3-stage fallback → `price_comps {count,min,p25,median,p75,max}`. Launch price = 110% of max→.99; `target_price` = p25. `category_price_defaults` config fallback for thin comps.

#### eBay Sold-Price API Access — status
- **Finding API `findCompletedItems`** ❌ DEAD (discontinued early 2025; error 10001)
- **Marketplace Insights API** ⚠ LIMITED RELEASE — `buy.marketplace_insights` scope required; no self-service; contact eBay Developer Support. Endpoint: `GET /buy/marketplace_insights/v1/item_sales/search`. Dave is applying via new keyset request.
- **Terapeak** — UI-only (Seller Hub → Research → Terapeak); 3 years data; no API; use manually for high-value items
- **Third-party**: 130Point.com, ZIK Analytics — legal approved partners; evaluate via PERPLEXITY-003
- **Interim**: Browse API p25 + PP-PRICE-004 velocity data is the current substitute

### PP-STRIKE-001 — eBay Strikethrough Pricing

#### Background
Dave was approved for eBay's Strikethrough Pricing program many years ago. This lets sellers
display an original/retail price with a strikethrough alongside the sale price on eBay listings,
increasing perceived value and CTR. Approval is at the account level and may persist across
keyset changes.

#### Verify access
Before implementing, confirm access is still active:
- Seller Hub → Marketing → Promotions (or Sales Events) — if strikethrough/sale pricing tools
  appear, the feature is enabled
- eBay Help: search "strikethrough pricing" — if your account shows the "Sale Price" section
  in the Edit Listing form, you're approved
- Alternatively: attempt to set `originalRetailPrice` in an offer via API and observe the
  response — a clean 200 confirms access; a 25500-series error indicates the feature is
  not enabled on this keyset

#### API implementation
Strikethrough pricing is set via the `originalRetailPrice` field in the eBay Inventory API
offer body (same call as `ebay_stage`). It is **not** the Promotions API — it is a standard
offer field that requires account-level approval to use.

Offer body addition (in `ebay_stage.py` `_build_offer_body()`):
```json
{
  "pricingSummary": {
    "price": {"value": "19.99", "currency": "USD"},
    "originalRetailPrice": {"value": "34.99", "currency": "USD"}
  }
}
```

#### TGW integration
- Source field: `draft_listing.original_retail_price` — set from `product_lookup.msrp` if
  available (e.g. upcitemdb returns `msrp` for many products); operator can override via
  item JSON edit or future MC / Flutter field
- Config key: `ebay.strikethrough_enabled: true/false` — global toggle so it can be disabled
  if access lapses
- `ebay_price.py`: populate `draft_listing.original_retail_price` from `product_lookup.msrp`
  when present and > launch price; store alongside `reprice_schedule`
- `ebay_stage.py`: include `originalRetailPrice` in offer body only when field is present and
  `ebay.strikethrough_enabled` is true
- `ebay_draft.py`: may also surface the MSRP in the description footer for items where
  product_lookup returns it

#### Dependencies
- `sell.marketing` scope ✅ already held (covers Promotions API; strikethrough is an offer field)
- Account-level approval — verify before implementing
- `product_lookup.msrp` field — upcitemdb already returns this in many results

#### Status
Planned. Verify account access first; implementation is straightforward once confirmed.

### PP-PROMO-001 — Sale Event Automation (P2 complete)

**P1 DONE 2026-06-12** — Design doc + operator checklist at `reference/PP-PROMO-001-sale-event-design.md`.

**P2 DONE 2026-06-13** — `tgw promo draft` + `tgw promo list` in `src/tgw/promo.py`; 41 tests in `tests/test_promo.py`; CLI wired in `api.py`.

Automates the dead-stock → markdown sale event cycle via the eBay Promotions Management API (`ITEM_PRICE_MARKDOWN`). The `sell.marketing` scope is already held. No PP-STRIKE-001 conflict: strikethrough uses `originalRetailPrice` in the offer body; this uses the Promotions API and is independent.

**Data flow**: `reports._scan_items()` dead_stock list → filter (min_days_stale, min_price, has listing_id) → markdown draft file → operator review → `tgw promo apply` (P3) → creates DRAFT promotion on eBay → operator promotes to RUNNING in Seller Hub.

**Item JSON addition**: `ebay_promo.{promo_id, event_name, discount_pct, start_date, end_date, applied_at}` written via tgw-api fence; cleared on promo end.

**Config keys** (add to `tgw-api-config.json`): `promo.{enabled, min_days_stale, min_price, max_items, discount_pct, duration_days, start_offset_days, marketplace_id}`. Default `enabled: false` until scope verified.

**Risk**: `ebay_price_reducer` must skip items with active `ebay_promo` block (R2 in design doc); wire this in P3 before first production use.

| Phase | Scope |
|-------|-------|
| P1 ✅ | Design doc + operator checklist |
| P2 ✅ | `tgw promo draft` CLI (read-only); `tgw promo list` scope check |
| P3 | `tgw promo apply`: Promotions API write + item JSON writeback; `ebay_price_reducer` promo-skip |
| P4 | `tgw promo end` / `tgw promo status` lifecycle |

**P3 blocked** on P2 scope verification (run `tgw promo list` in production — 200 → scope confirmed → P3 unblocked).

### PP-REPRICE-001 ✅ INITIAL COMPLETE (2026-06-03)
`ebay_price_reducer` worker: launch (day 0, 110%→.99) → retail (p75, day 3) → move (p25, day 17). `reprice_stages` array configurable; `to_99()` rounding; `reprice_skip: true` to exclude. Self-scheduling every 6h. `reprice_schedule` in item JSON tracks stage history.

### PP-REPRICER-001 — Market-aware dynamic repricer (design pending)
- Distinct from `ebay_price_reducer` (scheduled markdown): this watches market prices and adjusts dynamically
- Inputs: sold-price data (needs `buy.marketplace_insights` or Finding API), sell-through rate, days listed, competition count
- Design deferred until sold-price API access obtained — Browse API asking prices are the wrong signal for dynamic repricing
- Will consume `reprice_schedule` as floor (never price below the move price)

#### Sold price data landscape (PERPLEXITY-003, 2026-06-05)
All external options researched; none are clean substitutes for a true sold-data API:

| Source | Status | Verdict |
|--------|--------|---------|
| `buy.marketplace_insights` | Official docs: "restricted, not open to new users" — limited release, no roadmap | Effectively unavailable for independent devs; Dave applied via new keyset |
| Finding API `findCompletedItems` | Dead since early 2025 (error 10001) | Do not use |
| 130Point.com | Acquired MAGPIE (Mar 2025); shows "recent sales history"; **no documented public API** | Manual/semi-manual only; not suitable for automation |
| ZIK Analytics ($39–89/mo) | UI + CSV exports; no confirmed developer API | Seller research tool, not a pricing data backend |
| PriceCharting | Public API exists but **"only current item values — historic prices not supported"** | Good for games/cards/collectibles vertical only; use as supplement |
| Apify eBay sold scraper | $4/1K results; unofficial extractor layered on eBay search | ToS risk; fragile; not recommended for core pricing |
| Terapeak | UI only in Seller Hub; 3-year data; no API | Manual spot-checks on high-value items only |

**Decision:** PP-REPRICER-001 remains blocked on `buy.marketplace_insights` scope.
**Interim strategy:** Browse API p25 + velocity data (PP-PRICE-004) + own sales history as pricing signals.
**PriceCharting integration:** Worth adding to `apis/lookup/` for game/card/collectibles vertical — has free API tier, returns "current market value" derived from eBay sold data. Add as PP-UPC-001 Tier 2 source.

**Architecture note (from Perplexity):** When `buy.marketplace_insights` eventually arrives, wrap it
behind a `market_data` provider interface with fallback to Browse API comps + own sales history.
The `comps` DB table + pluggable provider pattern is the right design (see perplexity folder for full schema).

### PP-PRICING-001 — Image + title price comps via Google Shopping / Bing Visual Search

Interim substitute for `buy.marketplace_insights`. Not sold prices, but active listing prices
across multiple marketplaces (eBay, Amazon, Walmart, etc.) give a strong pricing floor signal
and significantly improve identification accuracy for unknown items.

#### Phase 1 — Title-based Shopping SERP (runs in `ai_identify` after Ollama step)

- Module: `apis/lookup/shopping_search.py` → `search_by_title(title, api_key) -> ShoppingResult`
- API: SerpApi `engine=google_shopping` with the AI-identified title
- Returns: prices across Google Shopping (eBay, Amazon, Walmart, etc.)
- Output written to item JSON:
  ```json
  "price_comps": {
    "shopping_search": {
      "source": "google_shopping", "query": "...", "fetched_at": "...",
      "prices": [29.99, 34.99, 45.00], "p25": 29.99, "p50": 34.99, "count": 12
    }
  }
  ```
- Integration: `suggest_price()` Stage 1.5 — use `shopping_search.p25` alongside Browse API p25
- Key: `secrets_root/serpapi-credentials.json` → `{"api_key": "..."}`
- Cost: ~$0.001/item (SerpApi pro plan); free tier 100 searches/month
- Graceful skip if key absent (same pattern as `igdb.py`, `discogs.py`)

#### Phase 2 — Image-based Visual Search (concurrent with Phase 1 in `ai_identify`)

- Module: `apis/lookup/visual_search.py` → `search_by_image(image_bytes, api_key) -> VisualResult`
- API: **Bing Visual Search API** — accepts multipart image upload, no public URL required
  - Endpoint: `https://api.bing.microsoft.com/v7.0/images/visualsearch`
  - Auth: `Ocp-Apim-Subscription-Key` header
  - Cost: $1.50/1000 queries; free tier 1,000/month (Azure Cognitive Services)
- Returns: `visualSearchTags` (product ID) + `ShoppingSource` actions (merchant prices)
- Output written to item JSON:
  ```json
  "price_comps": {
    "visual_search": {
      "source": "bing_visual", "fetched_at": "...",
      "identified_title": "Sony WH-1000XM4 Wireless Headphones",
      "prices": [34.99, 39.99], "p25": 34.99, "count": 8
    }
  }
  ```
- If Bing's identified title confidence exceeds Ollama's: write to `ai_identify_result.lens_title`
  (stored alongside `ai_identify_result.title`; operator sees both in review queue)
- Key: `secrets_root/bing-search-credentials.json` → `{"subscription_key": "..."}`
- Graceful skip if key absent

#### Integration in `ai_identify` worker

```
1. Ollama vision → title, category, condition  (existing)
2. Barcode lookup → product_context            (PP-LOOKUP-001, existing)
3. Phase 1 + Phase 2 fire concurrently as asyncio tasks after step 1
4. Results merged into item JSON via fence call before job completes
```

- Both phases are additive — they never overwrite Ollama's title/category output
- `identification_history` event type `image_search` added (source, query, identified_title)

#### Feeds into PP-REPRICER-001

- `ShoppingSearchProvider` added as a fourth `MarketDataProvider` in `market_data.py`
- Plugs into `recommend_price()` blend alongside `BrowseCompsProvider` and own sales
- When `buy.marketplace_insights` arrives it slots in as the authoritative sold-price signal
  and `ShoppingSearchProvider` drops to a supplementary role

#### Operator checklist

- [ ] Sign up for SerpApi (serpapi.com) — free tier is enough for evaluation
- [ ] Create Azure Cognitive Services resource → get Bing Search V7 subscription key
- [ ] Write keys to `secrets_root/` (chmod 600)
- [ ] Restart `ai_identify` worker after keys land

### PP-CANONICALIZE-001 — Canonical Inventory Record Promotion

The item JSON's top-level fields (`title`, `description`, `item_attributes`, `category`) are the
eventual source of truth for the inventory record. Until an item clears a trust gate, these fields
hold intake-time guesses only. Two flows promote working copies into canonical fields:

#### Trust gate

A `content_approved_at` ISO timestamp on the item signals the gate has cleared. Before it is set,
workers freely write to their own blocks (`draft_listing`, `item_attributes`, `ai_identify_result`).
After it is set, canonical top-level fields are locked against worker overwrites — workers update
their own blocks only.

#### Flow 1 — AI-confident path

`ai_identify` auto-promotes when its confidence score meets or exceeds a configurable threshold
(e.g. `ai_identify_confidence_threshold` in `tgw-api-config.json`, default 0.85). On promotion:
- `title` ← `ai_identify_result.title`
- `item_attributes` ← merged from `ai_identify_result.aspects`
- `content_approved_at` ← now (ISO UTC)
- `content_approved_by` ← `"ai_identify"`

#### Flow 2 — Operator-approval path (listing editor)

When the operator saves in the listing editor (PATCH `/api/items/{sku}` with `draft_listing` fields),
a dialog asks: **"Update canonical inventory record with these changes?"**

- If `content_approved_at` is **not yet set**: dialog defaults **Yes** (no canonical record exists)
- If `content_approved_at` **is set**: dialog defaults **No** (eBay tweaks assumed eBay-specific)

On Yes:
- `title` ← `draft_listing.title`
- `description` ← `draft_listing.description`
- `item_attributes` ← merge from `draft_listing` aspects
- `category` ← eBay category name (human-readable, not ID)
- `content_approved_at` ← now (ISO UTC)
- `content_approved_by` ← `"operator"`

#### Editability after promotion

The PATCH endpoint already supports direct top-level field edits. After promotion the operator
edits canonical fields directly from the item detail page — no special flow needed.

#### Implementation checklist

- [ ] `content_approved_at` + `content_approved_by` added to item JSON schema doc
- [ ] `ai_identify` worker: promote on high-confidence result; skip if already approved by operator
- [ ] Listing editor JS: after save, if `draft_listing` fields changed, show confirm dialog;
      include `promote_to_canonical: true/false` in PATCH body
- [ ] PATCH handler: if `promote_to_canonical` is true, derive canonical fields from `draft_listing`
      and write them alongside `content_approved_at` / `content_approved_by`
- [ ] Workers: before writing `title`/`description`/`item_attributes` top-level, check
      `content_approved_at`; skip overwrite if set (update worker-owned blocks only)
- [ ] Config key: `ai_identify_confidence_threshold` (default 0.85)

### PP-PRICE-003 ✅ COMPLETE (2026-06-04)
`pricing.py`: stage-0 product_lookup query (`brand+mpn` tightest); condition-filtered comps (same-or-worse rank only, 15-entry `_BROWSE_CONDITION_RANK`); price confidence H/M/L (`draft_listing.price_confidence`, `tgw staged` PC column).

### PP-PRICE-005 ✅ COMPLETE (2026-06-06) — Category Groups Taxonomy
`/opt/TGW/config/category-groups.json` — 24 groups covering 65+ eBay category IDs from velocity data.
Each group: `name`, `store_category` (fill in when eBay store configured), `ebay_categories` (all IDs in group),
`size_class` (flat/packet/small_box — semi-chaotic storage class), `ai_hint` (product description terms for ai_identify),
`pricing.floor` / `pricing.typical_used` / `pricing.typical_new` (seeded from velocity p25).
Top-level: `condition_factors` dict (new=1.50 … for_parts=0.30), `global_floor: 0.99`.
Integration: `suggest_price()` Stage 4 uses group typical × condition_factor when Browse API has insufficient comps.
Hard floor applied to ALL prices (even Browse API results). `tgw category-groups [--list | cat_id | --reseed]`.
**Store category**: fill `store_category` in each group after `tgw store-categories` confirms your store layout.
**Self-updating**: `tgw category-groups --reseed` recomputes typical_used from current velocity-stats.json.
**Design note**: `size_class` encodes semi-chaotic physical storage class — see PP-STORAGE-001.

### PP-STORAGE-001 — Semi-Chaotic Storage System
Inspired by Amazon chaotic storage. Items stored by SIZE not category — no two items in a location look the same.
Size class at intake gives: shipping profile match (flat→FC4/envelope; packet→Priority; small_box→Priority/FRPRI),
physical location hint (which shelf tier), and a visual distinctiveness constraint.
**Components:**
- `size_class` field in item JSON (flat/packet/small_box/medium_box/large_box) — set at intake or derived from weight+category group
- `category-groups.json` size_class = default for items in that group
- Weight hint: ~1 oz → flat/packet; 4+ oz → packet/small_box
- Shipping profile lookup: `size_class` → fulfillment_policy_id override
- Future: intake UI prompts photographer for size_class when item is unusual for its group
**Connection to PP-VISION-001**: same photo set used for visual inventory matching.

### PP-VISION-001 — Visual Physical Inventory Matching
Use item photos to visually match items to their physical location (inventory reconciliation).
Core idea: photo of shelf/item → vision model → match to SKU in catalog.
System design:
- Catalog thumbnails (already built: 54K thumbnails) = visual fingerprint database
- Vision model (Ollama or cloud) queries a candidate set, ranks by visual similarity
- Operator reviews ranked matches → confirms → system self-improves (correct matches become training signal)
- Size class constrains the search space (only look at items with matching size_class for that shelf)
- Semi-chaotic storage constraint (no two similar items together) naturally improves visual matching uniqueness
**Status**: ✅ **Phase 1 DONE (session 18)** — `src/tgw/fingerprint.py` (Pillow-only dHash + RGB
histogram), `tgw build-fingerprints` (full index = 54,314 rows), `tgw locate <image>`. Baseline
matcher; self-match distance 0.0000 verified. **Phase 2+ (embedding/CLIP model + ANN index)
blocked on GPU upgrade.** ⚠ `--size-class` filter inert until `size_class` is populated (0/83,520
items currently) — see PP-STORAGE-001 backfill follow-up.
**Dependency**: PP-STORAGE-001 (size_class field), PP-PRICE-005 (category-groups size_class lookup).

### PP-PRICE-004 ✅ COMPLETE (2026-06-05)
`tgw/velocity.py` + `velocity_stats` nightly worker (✅ enabled 2026-06-05). `tgw velocity-report` CLI. `velocity-stats.json` in catalog_root (1,540 categories). `suggest_price()` gains `velocity_hint: 'hold_launch'` for fast-moving categories. Stage breakdown (launch/retail/move%) populates as new-pipeline items sell.

### PP-LISTING-001 — Description footer and picklist line ✅ DONE (2026-06-04)
- Implemented in `workers/ebay_draft.py` — footer + picklist line built into `draft_listing.description`
- Seller boilerplate text + SKU/location picklist line; config keys: `description_footer`, `picklist_line_format`
- Future: QR code image (generate locally, upload to eBay EPS, embed in HTML) — deferred

### PP-STAGE-001 ✅ COMPLETE (2026-06-03)
`ebay_stage` creates UNPUBLISHED Seller Hub offer; `ebay_price` auto-enqueues it. `stage_draft()` + `publish_offer()` split in `sync.py`. `tgw staged` → operator review → `tgw publish <sku>`.

### PP-REVISION-001 — Live listing revision / update draft (design open)

**Governing principle (Dave, 2026-06-11 18:12 — candidate for Settled architecture once
the first implementation proves it):** for any editable record, changes are made to a
**draft**, never to the curated data directly. Draft → review → queue for application →
applied after approval. New listings approved this way enter the **Ready queue** and are
listed at the configured dole-out rate (PP-EDITOR-001). This holds for every surface —
TGW item data, eBay, Facebook Marketplace, whatever comes: **the current curated data
never has changes applied without review. Agents may update an assigned draft and pull
attributes from any source, but never write to the item's data directly.** Possibly
multiple drafts per record. (Context: the shipping-data recovery process will inform
shipping pricing for new items/revaluation, but that recovery flow is an exception, not
the normal processing pattern.)

- Three distinct workflows identified: new listing draft | live listing revision | ended→relist
- Revision needs: known baseline (live state synced from eBay), proposed delta, drift visibility
- Draft for new listing (`draft_listing`) is a historical record after publish — not the revision staging area
- ✅ **DECIDED 2026-06-12 (session 28): revision payload = sparse delta + pinned baseline.** The
  draft stores only the changed fields plus a snapshot/hash of the live-mirror state it was
  computed against. Apply = drift check (current mirror vs pinned baseline; drift on overlapping
  fields → review flag, never silent) → compose fresh live state + delta → full eBay PUT
  (Inventory API PUT is full-replace, so composition happens at apply time, never earlier).
  The applied-delta list IS the revision history (`revision_history`, the `identification_history`
  pattern). First buildable slice: **dry-run delta computer** — `tgw revise <sku> --set field=value`
  writes the draft + shows the diff vs live mirror, applies nothing (Round 7 todo #111)
- Relist: inventory item already exists on eBay; need fresh pricing + new offer; structurally re-create not update
- `ebay_offer` block now established (PP-PRICE-001) — proceed when ready
- Auto-sync: when offer fields are edited locally (price, condition, aspects), changes should push to eBay without requiring manual Seller Hub edits — design must prevent overwriting live state not yet pulled (depends on PP-SYNC-001 sync pass being authoritative first)
- Note (2026-06-11): the draft-review-apply principle above intersects PP-EDITOR-001
  (Ready state, rate-limited dole-out) and PP-DOCFLOW-001 (agents-write-drafts-only) —
  whichever is designed first carries the principle into code

### PP-FREESHIP-001 — Free Shipping Pricing Mode

**Origin:** Dave suggestion 2026-06-12T19:58. **Status:** todo #123.

**Problem:** Shipping rate increases require manual price edits across all free-shipping offers.
A dedicated mode absorbs shipping cost into the item price automatically.

**Design:** `tgw price-freeship <sku> [--apply]` — sums `ebay_offer.price` + shipping cost,
rounds to nearest `.99`, prints result; `--apply` writes combined price + sets `free_shipping: true`.
Config flag `free_shipping_enabled` (default off): when on, `ebay_stage`/`ebay_price` auto-compute
the free-shipping price. eBay fulfillment: `shippingCostOverrideType: NONE` in offer body.

---

### PP-OFFER-001 — eBay Best Offer Management

**Origin:** Dave suggestion 2026-06-12T19:59. **Status:** todo #124 (design first).

**Problem:** No tooling to view or respond to incoming Best Offers; they expire silently.

**Design:** `tgw offers [--pending] [--sku SKU]` — `GetBestOffers` list (offer ID, title, SKU,
buyer price, expiry); `tgw offers respond <id> --accept|--counter <price>|--decline` via
`RespondToBestOffer`. Auto-accept config (`min_price_pct`, default off — accept only, never
decline automatically). Responses logged in item JSON `offer_history`.

---

### PP-GIT-001 — Git / GitHub + Python Tutorial Resource

**Origin:** Dave suggestion 2026-06-12T18:50. No urgency — track for a future round.

Platform-first tutorial (TGW repo workflow, PR discipline) → generic Git best practices →
Python conventions tied to TGW patterns (pyproject/ruff/pytest). Likely a Gemini authoring
task from a rich context file.

---

### PP-SYNC-001 ✅ ALL PHASES COMPLETE (2026-06-04)
Core principle: every eBay-side ID/URL written back to item JSON immediately after API call. All matches by `listing_id` directly — never through catalog. Four phases done: `ebay_sync` write-back (6h) · `tgw ebay-pull` on-demand CLI · `tgw import-sold-csv` (2-year max, archive tombstone pass built) · `tgw ebay-sweep` physical review checklist (3 groups, clickable links, `--output`). Tier 3 (physical sweep) operator-gated; Tier 4 webhook code done, infra pending.

### PP-PRICE-002 (confirmed strategy — implemented in PP-REPRICE-001)
Launch 110% max→.99 · retail p75 day 3 · move p25 day 17. `ebay_reprice` stub in pyproject.toml; full market-aware version is PP-REPRICER-001 (blocked on scope).

### MILESTONE-001 ✅ (2026-06-03)
tgw.source replacement ~95% complete. Full pipeline: intake → AI identify → eBay draft → upload → price → stage → operator review → publish → sync. 13+ systemd workers; PostgreSQL state machine; SQLite catalog; 55K+ items.
- Full automated pipeline: photo intake → AI identification → eBay taxonomy → AI specifics → pricing → eBay draft staging → operator review → one-click publish
- 13 systemd workers running; PostgreSQL state machine; SQLite catalog; 55K+ item catalog
- Legacy tgw.source is now thin wrappers; new system is the authoritative data path
- Remaining gap (~5%): live listing revision / repricer / relist workflow (PP-REVISION-001)

- **PP-ADD-001** — Satellite / disconnected catalog support. Full design in Phase 6 § Satellite above. Depends on PP-ADD-005 + PP-ADD-003.

---

### Phase 4 — Flutter as Primary Cadillac UI: Seller Hub Parity + Beyond (2026-06-29)

**Design mandate:** "Everything Seller Hub can do — and more and better." Flutter is the primary
operator interface (tgw-prod desktop + Android tablet). Web UI is the secondary console (any
Tailscale browser). Both share the same API; features built on the API are available on both.
Features that exist nowhere yet are also captured here — this section is the definitive gap list.

#### Flutter navigation (replacing web UI's flat URL structure)

```
Tab 1: Home        — dashboard counts, alerts, activity feed, PM chat, quick actions
Tab 2: Inventory   — browse → item detail → full edit → photo management
Tab 3: Work        — drafts, review queue, revisions, bulk ops
Tab 4: eBay        — offers, pipeline / dead letter, categories, store
Tab 5: System      — workers, health, todos, links, settings
```

Android tablet / desktop: persistent left rail with labels. Phone: bottom tab bar.

#### Seller Hub coverage audit

| Feature | Seller Hub | Web UI | Flutter | Gap phase |
|---------|-----------|--------|---------|-----------|
| Inventory browse + filter + sort | ✅ | ✅ | ⚠️ basic | F1 |
| Item full edit (all fields) | ✅ | ✅ | ⚠️ basic | F2 |
| Category context + 3-layer aspects | ❌ | ✅ | ❌ | F2 |
| Readiness score per-field breakdown | ❌ | ✅ | ❌ | F2 |
| Price comps + remove comp | ❌ | ✅ | ❌ | F2 |
| Hint / barcode intake | ❌ | ✅ /form/intake | ❌ | F3 |
| Photo gallery + reorder + delete | ⚠️ | ✅ | ❌ | F4 |
| EPS upload trigger | ❌ | ✅ | ❌ | F4 |
| Bulk edit / bulk action | ✅ | ✅ /form/bulk | ❌ | F5 |
| Staged drafts approval + publish | ✅ | ✅ /form/drafts | ❌ | F6 |
| Revision diff + apply | ❌ | ✅ /form/revisions | ❌ | F7 |
| Buyer offers (view + respond) | ✅ | ✅ /form/offers | ❌ | F8 |
| Pipeline jobs + dead letter | ❌ | ✅ /form/pipeline | ⚠️ partial | F9 |
| Worker restart + system health | ❌ | ✅ /form/system | ⚠️ basic | F10 |
| Dashboard counts + activity alerts | ✅ | ✅ /form/ | ⚠️ basic | F11 |
| PM chat | ❌ | ✅ | ❌ | F11 |
| eBay category search (in edit) | ✅ | ✅ | ❌ | F12 |
| Store category assignment | ✅ | ✅ | ❌ | F12 |
| Todos + suggest (in-app) | ❌ | ✅ | ❌ | F13 |
| External links hub | ❌ | ✅ /form/links | ❌ | F14 |
| **Orders (view, mark shipped, label)** | ✅ | ❌ ext. link only | ❌ | **F15** |
| **Returns management** | ✅ | ❌ ext. link only | ❌ | **F16** |
| **Promotions / Sale events (PP-PROMO-001)** | ✅ | ❌ pending | ❌ | **F17** |
| **Performance metrics (defect rate, etc.)** | ✅ | ❌ | ❌ | F18 deferred |
| **Advertising (Promoted Listings)** | ✅ | ❌ | ❌ | F19 deferred |

**TGW capabilities that exceed Seller Hub (already built):**
AI identification · AI-drafted titles/descriptions/specifics · multi-source barcode lookup ·
category-aware condition filtering · 3-layer aspect form (operator > proposed > live merge) ·
readiness scoring per field · hint trail · location-based inventory (semi-chaotic, size_class) ·
pipeline visibility · revision draft with drift detection · comp source tracking + remove.

#### Flutter phase breakdown

**F1 — Inventory browse completeness**
State filter chips (All / In Stock / Staged / Listed / Sold / Blocked) · free-text search ·
location filter · sort (newest, price ↑↓, readiness). API: `/api/items` already supports all.

**F2 — Item detail + full edit (biggest gap)**
- Category context panel: condition selector (category-filtered), 3-layer aspect form, fulfillment suggestion (`/api/ebay/category-context/{id}`)
- Readiness score breakdown (per-field traffic-light chips)
- Price comps panel + `POST /api/items/{sku}/remove-comp`
- Hint input + hint trail viewer (`/api/items/{sku}/hint-trail`)
- Pipeline action buttons: identify, draft, price, stage, publish, archive
- eBay section: listing_id, live_price, status badge, Seller Hub deep link, offers link

**F3 — Intake form**
Hint / barcode entry · location + size_class selector · category group one-button template ·
camera trigger. API: existing `/api/items/{sku}/set-template` + `POST /api/items`.

**F4 — Photo management**
Photo gallery with tap-to-fullscreen · drag-to-reorder (touch-native, `POST /api/items/{sku}/photo-order`) ·
swipe-to-delete (`DELETE /api/items/{sku}/assets/{filename}`) · upload trigger button.

**F5 — Bulk operations**
Multi-select (long-press) from browse · bulk actions: set template, reprice, stage, archive ·
preview diff before apply. API: `/api/bulk/preview` + `/api/bulk/apply` + `/api/bulk/action`.

**F6 — Drafts + staged approval**
Staged items list (ready_at not null, unpublished) · approve / publish individual or batch ·
"List Now" bypass. API: `/api/items` with status=staged filter + `/api/items/{sku}/action`.

**F7 — Revision management**
Pending revision_draft list · diff table (field | current | proposed, red/green) ·
Apply / Discard. API: `/api/items/pending-revision` + `/api/items/{sku}/revision/apply`.

**F8 — Buyer offers**
Pending offers list: title, buyer price, % of ask, expiry countdown · inline Accept / Counter
(price entry) / Decline · offer history per item. API: `/api/offers` + `/api/offers/{id}/respond`.

**F9 — Pipeline + dead letter**
Job list with state chips · dead letter browser with error detail · Requeue / Cancel per job ·
worker queue depth display. API: `/api/pipeline/jobs` + `/api/jobs/{id}/requeue` + cancel.

**F10 — System / admin**
Health table (worker status, token expiry, disk %, postgres) · worker restart buttons ·
system info. API: `/api/health` + `/api/system/workers` + `/api/system/workers/{unit}/restart`.

**F11 — Dashboard + activity**
Status alert strips (counts, badges) · audible/vibrate alert for critical issues ·
activity feed · PM chat panel. API: `/api/dashboard` + `/api/activity` + `/api/pm/chat`.

**F12 — eBay category tools**
Type-to-search category picker (in edit screen) · store category assignment chip selector.
API: `/api/ebay/category-search` + `/api/ebay/store-categories`.

**F13 — Todos + suggest**
Todo list (open / in_progress / done) · state change inline · quick suggest tap-to-add.
API: existing `tgw todo` MCP tools / direct API.

**F14 — External links hub**
Curated tiles: eBay Seller Hub · Orders · Messages · Returns · Promotions · Performance · Fees ·
Gemini · Claude · Tailscale · GitHub. Flutter: in-app WebView or external launch.

**F15 — Orders / Fulfillment** ⚠️ requires `sell.fulfillment.readonly` scope (not yet held)
View open orders: buyer, item list, total, payment status · mark shipped (tracking + carrier →
Fulfillment API `POST /fulfillment/v1/order/{orderId}/shipping_fulfillment`) · label generation.
API side: new `/api/orders` endpoints. Scope request: block on eBay DS approval.

**F16 — Returns** ⚠️ scope TBD
Open return cases · accept / partial refund / escalate. Design first, then scope request.

**F17 — Promotions / Sale events** (PP-PROMO-001 — `sell.marketing` scope held ✅)
Create sale event (% off, eligible items by category/price range) · active promotions list ·
enroll / remove items. Design exists: `reference/PP-PROMO-001-sale-event-design.md`.
API side: new `/api/promotions` endpoints. This is the highest-ROI missing feature.

**F18 — Performance metrics** — deferred (requires `sell.analytics.readonly`, not held)

**F19 — Promoted Listings advertising** — deferred (separate scope + cost model; research first)

#### Implementation sequence

Priority order based on operational impact:
1. F2 (item edit completeness) — biggest daily friction point
2. F8 (offers) — revenue impact, offers expire silently  
3. F17 (promotions/PP-PROMO-001) — `sell.marketing` scope already held; design done
4. F1 (browse completeness) — QOL for all workflows
5. F6 (staged approval) — pipeline throughput
6. F4 (photos) — frequent warehouse operation
7. F9 (pipeline/dead letter) — ops visibility
8. F3, F5, F7 — intake, bulk, revisions
9. F10–F14 — admin, system, links
10. F15 (orders) — unblock with scope request first
11. F16 — after F15 scope pathway clear

Web UI nav also needs **Fulfillment** and **Marketing** menus once F15/F17 API is built.

---


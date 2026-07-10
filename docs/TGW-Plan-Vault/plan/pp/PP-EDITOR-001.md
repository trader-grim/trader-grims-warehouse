## PP-EDITOR-001 — Item Editor / Inventory Management App

### Vision
Cross-platform graphical app (Linux desktop + Android tablet) for full inventory management.
The Android tablet is the primary mobile interface for warehouse operations — browsing by
location, identifying items, setting prices, staging to eBay, and eventually scanning and
picklist generation. Flutter is the settled technology choice: true cross-platform with
Android as a first-class target; reads `tgwcatalog.db` directly via sqflite when offline;
writes go through `tgw-http` when connected to master. Syncthing handles catalog + thumbnail
sync to the tablet automatically.

**UI surface hierarchy (2026-06-28):** web UI = universal fallback (any browser, any device);
Flutter = near-universal primary (Linux desktop, Android, iOS, web via Flutter web) with
native performance and offline capability. The two are complementary, not competing — web for
reach, Flutter for depth. The event server / clean API separation means both surfaces stay thin.

### Architecture
```
tgw-http (FastAPI)         ← shared API for all write operations
     ↑                ↑
MC console         Flutter app (Linux + Android)
(PP-MC-001)        sqflite reads tgwcatalog.db directly (offline)
                   Dio http client for writes (online)
```

### Phase A — tgw-http FastAPI service ✅ COMPLETE (2026-06-03)
- `tgw serve` subcommand starts FastAPI HTTP server on port 7373
- Bearer token auth — API key at `secrets_root/tgw-api-key.json`
- All 8 endpoints implemented and smoke-tested:
  - `GET /api/items` — SQLite search (text, location, status, date range, limit/offset)
  - `GET /api/items/:sku` — full item JSON + _images/_videos + _queue_jobs (last 50)
  - `PATCH /api/items/:sku` — multi-field atomic update; location tree kept in sync; enqueues catalog_rebuild
  - `GET /api/items/:sku/thumbnail` — serves thumbnail from cache
  - `POST /api/items/:sku/action` — enqueues any pipeline stage (ai_identify sets ai_reidentify); handles dedupe gracefully
  - `GET /api/queue/status` — job counts per queue+state from PostgreSQL
  - `GET /api/ebay/aspects/:category_id` — delegates to existing specifics.py
  - `GET /api/locations` — distinct locations from SQLite
- `src/tgw/http_server.py`; `etc/systemd/tgw-http.service` (installed, enabled, running)
- fastapi + uvicorn[standard] added to pyproject.toml dependencies

### Phase B — Flutter skeleton
- Flutter project at `apps/tgw_app/`; Linux + Android build targets confirmed
- sqflite reading from `tgwcatalog.db` (same path layout as master, synced by Syncthing)
- Dio HTTP client wired to `tgw-http`
- Navigation shell (bottom nav bar)
- Connection state: online (API available) vs. offline (catalog read-only)

### Phase C — Browse + item view
- Gallery screen: thumbnail grid, title, location chip, pipeline status badge
- Filters: location selector, status filter, text search
- Item detail screen: tabbed — Item fields / eBay draft / Offer status

### Phase D — Edit + pipeline actions
- Edit screen: title, condition, price, item_specifics (aspect form), hint field
- Historical title suggestions (pulldown from catalog)
- AI buttons: "Re-identify", "Set hint + re-identify"
- Pipeline action dispatch: pick start/end stage, confirm, enqueue via API
- Save → PATCH /api/items/:sku

### Phase E — eBay offer form
- Aspect fields from `/api/ebay/aspects/:cat` — SELECTION_ONLY as dropdown, FREE_TEXT as field
- Price with comp range display (from ebay_offer.price_comps)
- Stage / Publish actions
- Mirrors Seller Hub form layout

### Admin GUI spec (session 9 additions — mobile-first requirements)
The tablet/phone is the PRIMARY operator interface for warehouse operations. Design must be
**mostly operable without a keyboard** — checkbox and button interfaces wherever possible.

**Welcome screen (first/home tab)**
- Welcome message appropriate to system status — if major issue: prominently red/alerting
  ("eBay token expired — tap to fix", "X jobs dead-lettered" etc.)
- `tgw health` summary display — clickable chips for each service (tap = more detail)
- Key metrics: items live, items staged, queue depths, last sync time
- Operations buttons: most-common actions (publish staged, run sweep, refresh token)
- Notifications panel: worker completion, dead-letter alerts, new sold items
- **Audible alert** for critical issues that require immediate operator attention

**Listings management tab**
- **Ready state**: items fully prepared but not yet listed anywhere — separate from staged
  - "Set Ready" is the default done-state after staging review
  - Queue: items in Ready state are doled out at 1/60 of total (rate-limited automatic listing)
  - "List Now" button bypasses the dole-out rate for urgent items
  - Rate config: configurable; default = 1/60 of ready items per listing cycle
  - ✅ **Backend DONE 2026-06-12 (session 29, todo #88)** — carries the PP-REVISION-001
    draft→review→apply principle into code. `ebay_offer.ready_at` is the ready marker
    (offer `status` stays eBay's UNPUBLISHED/PUBLISHED — ebay_sync rewrites it, so the
    local review verdict has its own field; publish flips status → item leaves the pool
    automatically). `tgw.ready` module: `ready_pool()` (oldest-first), `set_ready`/
    `unset_ready` (validated, through the items fence), `tgw ready [list|set|unset <sku…>]`.
    Self-scheduling `ebay_dole` worker (velocity_stats pattern, queue `ebay_dole`):
    each cycle publishes `max(1, pool // dole_divisor)` oldest ready items via
    `cmd_publish`; config `dole_interval_s` (3600) + `dole_divisor` (60). `tgw staged`
    now excludes ready items (counts them as `ready_count`); `tgw publish` is the
    List-Now bypass. Unit needs operator enable (admin todo #120). GUI surface still
    future scope.
- Staged review queue (approve/reject checkboxes + publish button)
- Live listings browser with filter/search
- Pricing anomaly review tab: listings at extremes, stale reprice, comp mismatches

**Item editor tab**
- Browse by location (semi-chaotic location tree)
- Item detail: all fields editable; no keyboard required for most fields (dropdowns, sliders)
- Pipeline action buttons: re-identify, re-draft, re-price, stage, publish
- Photo gallery inline

**Logs tab**
- Recent worker output; filterable by worker name

**Admin tab**
- Queue management: dead-letter browser, re-queue/cancel buttons
- Health drill-down

#### Design enhancement — Model users and role-based layouts (session 16)
Different operational roles need different UI surfaces:
- **Admin** — full system control, debug access, config
- **Item creation** — intake form, barcode scan, hint entry, category group selection
- **Content admin** — batch title/description edit, photo review, quality scoring
- **Warehouse** — inventory location lookup, item physical processing, picklist
- **Operator** — staged listing approval, eBay publishing, repricing, sweeps

Planned: RBAC gates + role-specific default tabs and field visibility. Model users:
- Photographer (warehouse role)
- Pricing analyst (content admin)
- Operator (mixed admin/staging)
- Supervisor (audit/report focus)

### Phase 2 — Web-based inventory browse + listing detail ✅ COMPLETE (2026-06-14, session 29, todo #845)

Goal: a browser-accessible UI that works on any device on Tailscale — no TGW install required.
Solves the gap between the CLI/MC console and the Flutter app (which requires the Flutter toolchain
and build step). Implemented as additional routes on `tgw-http` using the same inline-HTML pattern
established by `/form/intake`, `/form/bulk`, `/form/todos`, and `/form/suggest`.

**New routes added to `src/tgw/http_server.py`:**

| Route | Auth | Description |
|-------|------|-------------|
| `GET /thumb/{sku}` | none | Thumbnail JPEG — thumbnail_root first, falls back to first ItemData image |
| `GET /media/{sku}/{filename}` | none | Serve any photo/video from ItemData; path-traversal validated |
| `GET /form/items` | none | Inventory browse page (see below) |
| `GET /form/items/{sku}` | none | Item detail page (see below) |

Both media routes use network trust (no Bearer) so `<img src>` tags in the browser work directly.
Path traversal is blocked: filename is checked with `Path(filename).name == filename`; sku is
checked for `..`; only known image/video extensions are served.

**`/form/items` — inventory browse:**
- Card grid: thumbnail, SKU (link to detail), title, status badge (colour-coded), location, price
- Live JS filtering: free-text search + location input (debounced 300 ms) + status chip bar
  (All / In Stock / Listed / Staged / Sold)
- Pagination: 60 per page, Prev/Next buttons, page X of Y display
- Hits `GET /api/items` (Bearer embedded in page JS, same pattern as `/form/intake`)
- Dark theme consistent with all other `/form/` pages

**`/form/items/{sku}` — item detail (server-rendered):**
- Two-column layout: left = photo gallery, right = field sections + diff + jobs
- **Photo gallery**: main large photo + clickable thumbnail strip; clicking a strip thumb updates
  the main photo via inline JS; photos served from `/media/{sku}/{filename}` (no auth)
- **Field sections**: Identity (title, category_group, condition, ai_hint, barcode, description),
  eBay (listing_id, status, live_price, url, qty), Physical (location, weight_oz, size_class)
- **Revision draft diff table**: if `revision_draft` is present in item JSON, renders a three-
  column table: Field | Current (from baseline snapshot) | Proposed (delta) — current in red,
  proposed in green. Shows metadata: by, at, baseline hash prefix. This is the primary use case
  for evaluating Claude's proposed revisions before applying them.
- **Pipeline jobs**: last 10 queue jobs for this SKU — queue name, state (colour-coded), updated
  timestamp, error detail
- Fully server-rendered (Python f-strings, `html.escape()` on all values); no client-side
  auth needed; photos via no-auth `/media/` routes

**Access:** `http://<tgw-host>:7373/form/items` on any Tailscale device, no login required.

### Phase 3 — Full operational console ✅ COMPLETE 2026-06-14 (session 30, todos #846–#859)

**Guiding principle:** The web UI is the primary graphical workflow. Every physical warehouse stage
(receive → identify → review → approve → respond → fulfill) has a corresponding page. A first-time
user can open the browser and know what to do next without prior knowledge of the system.

**Architecture decision: go static (todo #846)**
`src/tgw/static/` directory with `tgw.css`, `nav.js`, `tgw.js`; FastAPI `StaticFiles` mount at
`/static`. All existing embedded CSS/JS string constants replaced with `<link>`/`<script>` includes.
Nav bar becomes one shared component, not 15 embedded copies.

**Navigation structure (persistent top bar on all /form/ pages):**
```
TGW | Dashboard | Inventory ▾ | eBay ▾ | System ▾ | Docs ▾ | Links
```
Inventory: Browse · Intake · Review Queue · Revisions · Bulk Edit
eBay: Offers · Pipeline · (Seller Hub link)
System: Health · Workers · Todos · Dead Letter
Docs: Runbooks · Known Issues · Architecture · Pipeline Flow · Handoff

**Pages planned (todos #847–#859):**

| Todo | Page / endpoint | Purpose |
|------|----------------|---------|
| #847 | `GET /api/dashboard` | Summary counts — needs_review, offers, photos, drafts, dead-letter, ready, workers |
| #848 | `/form/` home dashboard | Status strip + action cards + PM chat + activity feed + quick intake |
| #849 ✅ | `POST /api/pm/chat` + chat UI | LLM project manager chat window; haiku-4-5 with live TGW context; can add todos/suggestions — **DONE 2026-06-14** |
| #850 | `/form/links` | External links hub — eBay, AI/ML services, infrastructure, research |
| #851 | `/docs/{path}` | Vault markdown renderer — runbooks, ISSUES, architecture, handoff |
| #852 | `/form/offers` + offers API | Best Offers UI with % of ask, inline Accept/Counter/Decline, dry-run toggle |
| #853 | `/form/revisions` + revision API | Pending revision_draft list with inline diff + Apply/Discard |
| #854 | `/form/review` + review API | Post-AI-draft human approval queue; Approve/Edit/Re-draft inline |
| #855 | `/form/pipeline` + workers API | Queue depths, active jobs, dead-letter manager, auto-refresh |
| #856 | `/form/system` | Health table, token expiry, disk, postgres stats, worker restart |
| #857 | Intake enhancements | Landing page + photo count warning + pipeline trigger buttons + job poll |
| #858 | Item detail eBay links | View on eBay, Seller Hub deep link, Messages link, offer badge |
| #859 ✅ | Polish pass | Gaps discovered through actual use — **DONE 2026-06-14** |

**PM chat (todo #849):**
Persistent chat window in home dashboard sidebar. `POST /api/pm/chat` builds context from live
system state (todos, queue depths, health, recent jobs, open offers), calls `claude-haiku-4-5`
(configurable as `pm_chat_model` in config). Returns `{message, actions: []}` where actions
can be `add_todo`, `add_suggestion`, or `none`. Chat history in sessionStorage. PM can answer
"what needs doing?", "how many items in pipeline?", "any dead letters?" and take PM actions.

**Build strategy:** iterative — deploy each round, use it, discover gaps, record in polish pass
(#859). The UI is also a test harness for the underlying API: every missing filter, slow query,
or data gap it exposes gets filed as a follow-up.

### Phase 3p — Web UI Rework (session 32, 2026-06-15)

Driven by Dave's live review session. Full Round 8 — see Work Tracks above for the task table.

**Theme A — Photo system fixes (ISS-013)**
- `tgw alt-text` must preserve the original photo filename; `<sku>-alt.jpg` is a new companion file (AI-annotated derivative), never a rename of the original
- Photo gallery display order: by mtime, with SKU-named files first (the display photo intent)
- Gallery UX: lightbox on click; move-to-front / manual reorder; video items visually separated

**Theme B — Item detail page overhaul**
- All Inventory fields visible and inline-editable (PATCH via `tgw-http` already wired)
- eBay section: current price, shipping policy name, eBay categories, store categories
- Section labels clarified: "eBay Offer" = internal draft/listing record (not buyer offer); buyer offers live in `/form/offers`; "Revision Draft" = proposed changes before push
- "Pricing History" expandable link replaces inline price-source text; shows comps, defaults, operator override trail — this is the record Dave described (yes, the process described is correct: price is the inventory field; repricing pushes a draft eBay update that is reviewed before application)
- eBay draft section: show draft price; fix false-positive `no_brand` (scan title); add "Does Not Apply" / "Unknown" to `no_model`
- Pipeline tooltips per worker

**Theme C — Inventory browse (major)**
- Per-card: price, eBay status badge (Listed + eBayID link / Staged / Ready / Needs Review), missing-photo indicator
- Checkbox + bulk-action toolbar (Re-identify / Reprice / Mark Ready / Mark Sold / Delete from eBay / Apply Draft)

**Theme D — Pipeline page drill-down**
- Click failed/dead_letter count → detail panel with job list, error text, re-queue + report buttons
- Stuck active jobs (elapsed > 2× expected) surfaced

**Theme E — Review Queue + Intake form**
- Review Queue: add shipping, category, condition, condition description; filter + search + multi-select + bulk-approve/list-now/mark-ready
- Intake form: add purpose banner + pre-populated field display + pipeline trigger buttons

**Theme F — Data + API fixes**
- ISS-014: qty < 0 validation + repair
- ISS-015: Best Offers rate limit + call-budget display

**Theme G — Page clarification**
- Revisions page: purpose banner + empty-state workflow guide + link to PP-REVISION-001

### Phase 3r — Ollama Retirement + OpenRouter Migration ✅ COMPLETE 2026-06-26 (session 28)

**Problem**: Ollama/Qwen2.5 was the last locally-run model, used only for `ebay_draft` and
`pm_intake`. Generator fuel cost + server slowdown outweighed the benefit of free inference.

**Changes:**
- `tgw-models.json`: `ebay_draft` → `openrouter/google/gemini-2.5-flash`; `pm_intake` → `openrouter/deepseek/deepseek-v4-flash`
- `src/tgw/apis/llm.py`: `_DEFAULTS` updated to match; fallback default changed from `ollama/Qwen2.5:latest` to `openrouter/google/gemini-2.0-flash-lite`
- Workers restarted; 2 dead-letter `ebay_draft` jobs re-queued and processed successfully on first attempt (~2s each vs. timeout/404)
- Ollama is now entirely unused by the pipeline — can be removed from NixOS config

**Result:** `ebay_draft` now uses Gemini 2.5 Flash (multimodal-capable); `pm_intake` uses DeepSeek V4 Flash. Both faster and cheaper than local Qwen2.5. Ollama dependency eliminated.

### Phase 3q — Operator Publish Gate ✅ COMPLETE 2026-06-26 (session 27)

**Problem**: Items were going live on eBay without explicit operator approval from the web UI.
The `ready_at` gate existed in the backend (`tgw ready set`) but had no web surface.

**Process design** (settled, 2026-06-26):
```
ai_identify → ebay_draft → ebay_upload → ebay_price
                    ★ OPERATOR REVIEWS IN ITEM EDITOR ★
ebay_stage → UNPUBLISHED offer
Approve    → set_ready (enters dole pool, lists at next cycle)
             OR Publish Now → lists immediately
```

**Changes:**
- `http_server.py`: item detail page "Publish to eBay" section replaces flat Actions row
  - Pipeline status bar: `Draft → Staged → Approved → Live` (colour-coded per state)
  - Context-aware gate: Approve for Listing / Publish Now / End Listing based on current state
  - `set_ready` / `unset_ready` added to single-item `PIPELINE_ACTIONS`; page reloads on change
  - Pipeline Tools section separated (Re-identify / Re-draft / Re-upload photos / Re-price)
  - Re-upload photos added as explicit action (was missing from UI)
- `workers/ebay_draft.py`: Phase 2b pre-fill aspects from `item_attributes` before LLM call
  (lower priority than `product_lookup`; validates SELECTION_ONLY against allowed values)
- CSS: `.act-publish` style (green, distinct from pipeline tool buttons)

**Remaining publish gate scope (future):**
- Photo management panel (disk vs EPS with checkboxes)    → PP-EDITOR-001 Phase 4
- Live price from `ebay_offer.price` not stale `ebay_live` → PP-EDITOR-001 Phase 4
- Pipeline status badges in inventory browse card grid     → PP-EDITOR-001 Phase 4

### Design notes (session 19/20)

- **Recently-processed SKU sort** — `GET /api/items?sort=recently_processed`: a sort option
  ordering by the timestamp of the last pipeline action (enqueue, PATCH, or catalog_verify).
  Useful for reviewing batches just processed through ai_identify or ebay_draft. Add to catalog
  query options when PP-EDITOR-001 Phase E is in scope.

- **One-at-a-time review mode** — take any search result list and feed items to the editor
  one at a time with prev/next navigation. Operator can approve/flag/edit each SKU in sequence
  without returning to the list. Useful for post-pipeline QA, staged review, and photo review.
  Model: `GET /api/items/review-queue?from=<list_params>` returns a session token; `GET
  /api/items/review-queue/<token>/next` advances. Can be a simple URL-based state machine.

- **Backend contract for Flutter** (GEMINI-003 finding): app uses `/api/queue/status` for
  connectivity check. Correct endpoint is `GET /api/health` → JSON (todo #37); swap once done.

### Later phases (separate PPs)
- Scanner input (barcode/SKU lookup → item detail)
- **PP-INTAKE-001 intake screen** — pre-photo flow: weight, size_class, barcode scan, category group picker, ai_hint; location suggestion (semi-chaotic); Tasker camera trigger → intake form
- Picklist generator (PP-ADD-009) as embedded screen
- Offer management list view
- Fulfillment workflow
- Tasker hooks for push notifications from master → tablet

---

### PP-WHISPER-001 — Audio capture and voice-to-suggest interfaces

#### Problem
Capturing ideas, item hints, and descriptions mid-workflow is friction-heavy when hands are
full during physical processing. Text entry via keyboard or Tasker tap is usable but slow.
Voice capture (Whisper) offers zero-friction idea capture during item photography and sorting.

#### Scope
- `whispertosuggest`: short audio clip → Whisper transcription → `tgw suggest "..."` append
  (the audio-native equivalent of typing a suggestion)
- `whispertoidentify`: whisper a hint or item description → writes `ai_hint` + triggers `ai_identify`
- Tasker integration: Tasker button/shortcut on Android → POST to `tgw-http` with voice text
- Deferred capture: record audio during photo session, process later (batch transcription)
  — avoids Ollama load during active photo runs; audio files dropped to a queue dir

#### Integration ideas (from tgw.source review)
- Whisper.cpp already installed or planned (see PP-REMOTE-001 AI runtime manager)
- `tgw suggest` already the canonical capture back-channel — whisper is a voice front-end to it
- Tasker on Android: press record → transcribe → POST `/api/items/<sku>/action` or `tgw-http`
  hint endpoint; SKU from barcode scan or CurrentItem symlink

#### Whisper.cpp implementation details (PERPLEXITY-004, 2026-06-05)
- **Model**: `base.en` for 5–15s English memos on 32GB CPU-only — 388MB RAM, sub-second to ~3s latency
- **Build**: CMake (most reliable), Docker `ghcr.io/ggml-org/whisper.cpp:main`, or Conan packages
- **Gotcha**: expects 16-bit WAV unless built with FFmpeg support; use `ffmpeg -ar 16000 -ac 1 -c:a pcm_s16le`
- **CLI**: `./build/bin/whisper-cli -m models/ggml-base.en.bin -f memo.wav`
- **Enable BLAS** on CPU for better throughput: `cmake -DGGML_BLAS=ON`
- **v1.8.4** released March 2026 — actively maintained
- Alternatives if needed: `faster-whisper` (Python, heavier), Vosk (lighter, lower accuracy)

#### Dependencies
- PP-REMOTE-001 (Tailscale + `tgw-http` reachable from Android)
- PP-IFDIR-001 (interface configs organized)
- Whisper.cpp binary installed (PP-ADD-010 AI runtime manager)

---

### PP-INTAKE-001 — Photographer Intake: Template-Driven Multi-Surface System

#### Core architectural insight — the Template
The template is the key. One button press selects a template (a category group) and instantly
applies the best available assumptions for that item class: `size_class`, `ai_hint`, typical
price range, store category, fulfillment policy. Everything else is optional fine-tuning.

The system is already partially wired: `category-groups.json` IS the template table (PP-PRICE-005 ✅).
The `SETTEMPLATE:name` / `COMMAND:...` clipboard protocol IS the push channel to the camera app.
The photographer already has this tooling. The work is to complete the integration loop.

**Graceful degradation by design** — the system works at every level of photographer participation:
```
No photographer input → ai_identify derives group → group defaults apply     (baseline)
Template selected     → group defaults + better ai_identify hint             (good)
Template + fine-tune  → all fields correct at intake                         (best)
```
The photographer never blocks the pipeline. More input = better result, but absence of input
is handled automatically. The system self-improves as velocity data refines template pricing.

#### Existing photographer interface — three surfaces
Already operational; PP-INTAKE-001 extends, does not replace.

| Surface | Technology | Role |
|---------|-----------|------|
| **Camera HUD** | Camera app + KDE Connect clipboard relay | Receives SETTEMPLATE:/COMMAND: from TGW; shows current item state during shoot |
| **Desktop HUD** | Qtile widget (PP-WM-001) + floating overlay | Live queue status, current SKU, pipeline progress |
| **Web form** | tgw-http (existing, to be updated) | Fine-grained field entry; opens in browser/WebView from any surface |
| **xmouse macros** | Tablet macro pad → SSH → TGW commands | One-button template selection + quick overrides |
| **USB scale** | `weight()` / `get_weight()` in tgw.source | Physical weight capture → size_class derivation |
| **Whisper dictation** | `whisper-hint()` etc. in tgw.source | Voice → ai_hint, voice → title, voice → condition |

#### The template dispatch loop
```
xmouse button press
    → SSH → tgw set-template <group_key>
        → writes group defaults to CurrentItem JSON
            (size_class, ai_hint, category_group, ebay_category_id)
        → pushes "SETTEMPLATE:<group_name>" via KDE Connect clipboard relay
            → camera app HUD updates to show active template
        → pushes "COMMAND:DATA:size_class=<val>" etc. if fine-tuning needed
    → bundle_intake picks up item with pre-populated fields
    → ai_identify gets group ai_hint as context → better result
    → suggest_price gets category_id → group floor/typical → priced even with thin comps
```

#### `tgw set-template` — ✅ BUILT (CLI session 8, web form session 11)
```bash
tgw set-template <group_key> [sku]           # apply group defaults to CurrentItem or given SKU
tgw set-template --list                      # show all available templates (from category-groups.json)
tgw set-template --camera <group_key>        # push SETTEMPLATE: to camera via KDE Connect only
```
What it writes to item JSON:
- `category_group`: group key
- `ai_hint`: group.ai_hint (prepended, preserves existing if any)
- `size_class`: group.size_class
- `ebay_category_id`: first category in group.ebay_categories (if not already set)
- ~~`fulfillment_policy_id`: derived from size_class → config lookup~~ — **NOT implemented**
  (session 15 audit): the template never writes a fulfillment policy. The cleaner per-item
  mechanism is PP-HINT-001 `shipping_profile` (round-2 rank 8) + PP-STORAGE-001
  `size_class → fulfillment_policy_by_size_class` resolver (round-2 rank 9).

xmouse maps each group to a dedicated button. 24 groups = 24 one-press intake macros.

#### Template table maintenance (self-improving)
- `tgw category-groups --reseed` recomputes typical_used/floor from current velocity data ✅
- As new items sell and velocity grows, template pricing tightens automatically
- Dave can manually curate ai_hint and size_class per group as item knowledge grows
- Future: ai_identify confidence → auto-suggest template corrections back into category-groups.json

#### Fine-grained tailoring (when template isn't quite right)
1. xmouse has additional buttons for common overrides: weight entry, condition override, barcode scan
2. Web form (tgw-http) shows template-applied defaults; photographer edits only what differs
3. Voice: `whisper-hint()` appends to the ai_hint that template already pre-filled
4. Desktop HUD shows the active template; operator can see and correct before pipeline runs

#### Background inference (future — better compute required)
When Ollama runs faster (GPU upgrade, PP-NIXOS-001 migration):
- `ai_identify` enqueued immediately when first photo lands in newitems/
- Preliminary identification returned to camera HUD while photographer is still shooting
- Result feeds back as suggested template confirmation: "Looks like Kitchen Utensils — correct?"
- Operator confirms or overrides → no post-session correction pass needed
- Weight from USB scale + ai_identify result → size_class confirmed automatically

#### Phases
- **Phase 1** — `tgw set-template` command: writes group defaults to item JSON + KDE Connect push. xmouse macro buttons per group. Closes the template→pipeline loop. `tgw set-template --list` for operator discovery.
- **Phase 2** — Web form update: add template picker (24 group chips), weight field, barcode field. Pre-fills from current template; photographer only changes what's wrong.
- **Phase 3** — Camera HUD integration: SETTEMPLATE: response shows group name + ai_hint summary + size_class on camera display; photographer sees confirmation before next shot.
- **Phase 4** — Background inference: ai_identify enqueued on first photo drop; result shown on HUD; operator confirms/overrides mid-session.
- **Phase 5** — Template self-update: ai_identify results with high confidence → suggest category_group refinements; velocity data → auto-reseed pricing monthly.

#### Computer-side intake workflow (session 9 addition)
Current path: camera app creates JSON/photo/folder set on device → Syncthing → bundle_intake.
**Alternative**: initiate the intake workflow from the computer side, reducing steps on the phone.

Concept:
1. Computer pre-creates the SKU folder and blank item JSON (with template pre-applied)
2. Syncthing pushes the folder to camera device
3. Camera app detects new folder → switches to photo mode for that SKU automatically
4. Photos taken → Syncthing returns them → bundle_intake picks up
5. Result: phone is purely a camera; all data entry on computer; faster per-item processing

This is architecturally simpler than the current push-from-camera model and may be faster
in practice. Design as a Phase 2.5 addition: `tgw create-item [--template GROUP_KEY]` that
pre-creates the folder + triggers camera app via KDE Connect COMMAND:.

#### Camera root intent (future — session 9 note)
Goal: root intake cameras to gain file system access during Foldio360 turntable sessions.
**Problem**: Foldio360 app does not expose photos until after zipping them; the zip step
doubles total processing time per spin. Root access bypasses the zip, reading photos directly.
**Path**: target Android devices known to have reliable root methods (Pixel series + Magisk).
Eventually deploy with custom ROMs to get fine-grained control and remove bloatware.
**Custom camera app (PP-INTAKE-002)** — ⬆ elevated to active design 2026-06-12 (Dave suggestion 17:51): replace
Tasker + stock camera with a TGW-native Android app that **incorporates the Tasker functions
directly into the interface** — barcode scan, template select (SETTEMPLATE HUD), camera trigger,
voice hint, upload via Syncthing folder or tgw-http — no third-party dependencies.
**Design RETURNED 2026-06-12** (gemini todo #115 done): full Flutter scaffold proposal at
`reference/PP-INTAKE-002-camera-app-design.md`. Highlights: `mobile_scanner` (ML Kit) barcode,
`flutter_tts` voice, Riverpod state, Dio HTTP, `flutter_rfb` VNC, dual upload (Syncthing
folder + tgw-http POST), Foldio360 zip-bypass via root `su` polling (short-term) + BLE direct
control via `flutter_blue_plus` (long-term). Dave must review before scaffold build begins.
Three open questions: root-privilege packaging strategy (app vs shell script), target device
for root (Pixel/Xiaomi), Syncthing path alignment (`/sdcard/Pictures/TGW_Sync/`).

**xmouse replacement app (PP-INTAKE-003)** — ⬆ elevated to active design 2026-06-12 (Dave suggestion 18:20):
open-source Android app (GitHub-based) replacing the xmouse macro pad, incorporating an
**RDP/VNC client and a form tool** in one interface — macro grid dispatching via SSH/tgw-http
(template buttons, pipeline triggers), embedded remote viewer for desktop sessions, and a form
surface for the `/form/*` tgw-http pages.
**Design RETURNED 2026-06-12** (gemini todo #116 done): full Flutter architecture survey at
`reference/PP-INTAKE-003-xmouse-replacement-design.md`. Recommendation: Flutter stack with
`flutter_rfb` (Apache-2.0 VNC, avoids GPLv3 contamination from aRDP/bVNC), `dartssh2` (MIT
SSH), `flutter_inappwebview` (form surface). 3-phase roadmap: P1 macro grid + SSH/HTTP dispatch,
P2 form tool integration, P3 embedded VNC. Dave must review before any build.
**SETTLED (Dave, 2026-06-12):** Flutter + Apache-2.0/MIT path confirmed. GPLv3 native-Android
path (bVNC/aRDP lineage) rejected. Design doc at `inbox/review/xmouse-replacement-design.md`
pending review; scaffold task to be seeded as a Claude/Aider todo after review.

#### Dependencies
- PP-PRICE-005 `category-groups.json` ✅ DONE — this is the template table
- PP-WM-001 Qtile desktop HUD ✅ Phase 1 done
- PP-WHISPER-001 voice capture (Phase 1+ whisper-hint already works)
- KDE Connect + COMMAND:/SETTEMPLATE: clipboard relay (already in tgw.source — rescued from deprecated in SHELL-AUDIT.md 2026-06-06)
- PP-REMOTE-001 (tgw-http reachable from tablet for web form)
- GPU upgrade / PP-NIXOS-001 (Phase 4 background inference)

---

### PP-TODO-001 — Multi-agent TODO tracker (`tgw todo`)

**Agent rename (Dave, 2026-06-11 18:34):** `db` agent renamed **`sokoban`** (warehouseman)
— existing item delegated, future physical/warehouse tasks use `tgw todo sokoban`.
Dave also flagged that many admin tasks live in plan tables but not the tracker — same
two-surface gap as Round-5 rows (handoff risk 9); seed operator items as todos when
rounds are created, same rule as Claude items.

#### Problem
Tasks and reminders are captured in `tgw suggest` / SUGGESTIONS.md but there is no structured
command to list open TODOs by agent or priority — items mix with ideas and require full plan
review to surface actionable tasks.

#### Concept
`tgw todo [agent]` — lists open tasks, similar to `tgw picklist` but for action items:
- `tgw todo` — all open items across all agents
- `tgw todo admin` — operator physical tasks (shipping, labeling, inventory)
- `tgw todo claude` — Claude Code implementation queue
- `tgw todo gemini` — Gemini Code / large-context analysis tasks
- `tgw todo db` — database / data scrub tasks

Versatile enough to add human and AI agents over time.  Each entry has: agent, priority,
description, added_at, source (suggestion / inbox / session note).

#### Storage design
- Back-end: PostgreSQL table `todo_items (id, agent, priority, body, source, added_at, done_at)` in `state_machine` DB
- Or: flat TOML/Markdown file under `docs/TGW-Plan-Vault/` with front-matter per entry
- `tgw todo add [agent] "text"` — create entry; `tgw todo done <id>` — mark complete
- Could feed the "quiet queue" hook in PP-CAPTURE-001 — surface `tgw todo claude` when workers go idle

#### Unique ID per task (session 9 requirement)
Every todo item must have a **unique numeric ID** to make interaction unambiguous:
```
tgw todo task 265 completed
tgw todo task 832 delegate gemini
tgw todo task 24 update "waiting on IGDB key"
```
IDs are auto-assigned (PostgreSQL `SERIAL`), never reused. This enables precise cross-session
references, especially in SUGGESTIONS.md entries and voice dictation (no spelling ambiguity).
`tgw todo` list output must always show the ID prominently as the first column.

#### Dependencies
- PP-CAPTURE-001 (idea pipeline design) — aligns on storage back-end choice

#### Connection to Work Tracks strategy test
The 4-track delegation model (session 5) is the motivating use case. Work Tracks gives each
agent a queue; PP-TODO-001 makes that queue queryable and persistent across sessions. The
`tgw todo claude` / `tgw todo gemini` / `tgw todo admin` structure maps directly to Tracks 1,
2, and 4. Build PP-TODO-001 so Work Tracks items can be seeded into it on first run.

#### Design enhancement — Quick-access dashboard (session 16)
Dave requested immediate access to todo queue without hunting through the master plan:
- `tgw todo` output must be **quick** (no scrolling, no plan context needed)
- Links to delegated tasks + supervisory duties **inline** in todo list
- This becomes the "source of truth" for daily work flow, especially under duress
- Mobile/tablet-friendly variant planned for future PP-EDITOR-001 admin GUI
- **Rationale**: "All of those simple little things cause distraction and consume time and lead to errors"

---

### PP-PYIPC-001 — Python IPC: Syncthing + KDE Connect Integration

#### Goal
Replace shell-subprocess calls to `kdeconnect-cli` and Syncthing with Python library
bindings so TGW workers and the FastAPI service can interact with both services
programmatically — events, status, clipboard, file transfer.

#### Syncthing (PERPLEXITY-005 findings — session 19/20)
- REST API at `localhost:8384` — Syncthing is **already running** on the production machine
- Config + API key at `/opt/TGW/.local/syncthing/config.xml` (in-project, `chmod 600`)
- API key is parsed from the config.xml `<apikey>` element at PP-PYIPC-001 implementation time
- **Recommended library**: `pyncthing` (PyPI) — requests-based, best-maintained, supports PATCH,
  modern Syncthing versions. `aiosyncthing` is stale (labeled as such even in its own README).
- **Async event streaming**: `pyncthing` is synchronous; for long-polling `/rest/events` or
  `/rest/events/disk`, implement a thin custom `httpx`-based async consumer with `since`/`timeout`
  params. This is the recommended TGW pattern — keeps the event loop non-blocking.
- **Relevant endpoints**: `/rest/events/disk` (pre-filtered file/folder events), `/rest/db/status`
  (folder state + `needBytes`) — use together to confirm sync completion before triggering rebuilds
- TGW integration: when `tgwcatalog.db` folder goes idle → enqueue `catalog_rebuild` job
- Config key to add: `syncthing_config_path` → defaults to `/opt/TGW/.local/syncthing/config.xml`

**⚠ PP-PYIPC-001 is now fully unblocked** — Syncthing is live, API key in-project. No operator action needed.

**Multi-user NixOS design (session 19/20):**
- Current port: 8384; NixOS target port: 8385 (separate from user instances; see PP-NIXOS-001)
- LTSP fat clients: per-hostname config directory symlink for location-specific folder mappings

#### KDE Connect (PERPLEXITY-005 findings)
- No mature Python PyPI package; use **`pydbus`** for D-Bus access (`org.kde.kdeconnect.daemon`)
- `kdeconnect-cli` via subprocess for one-shot operations; `pydbus` for long-running services
- Clipboard strategy: monitor X11 clipboard locally (`python-xlib`) → push via KDE Connect as
  transport. Android 10+/14 restricts clipboard access to foreground apps; desktop side is unaffected.
- TGW integration: `ic_template()`, `ic_command()` wrappers in Python; push from workers

#### Additional findings from PERPLEXITY-005

**DB migration path:**
- psycopg3 (`psycopg` on PyPI) is the clear psycopg2 successor; start synchronous, add async later
- `aiosqlite` for FastAPI catalog read paths (prevents blocking event loop); keep sync writes in workers

**`python-xlib` status:** Effectively stale upstream (no PyPI releases in 12+ months); distribution-
level patches only. Works for X11 clipboard today but not a long-term bet given Wayland migration.
Consider replacing with a Wayland-aware clipboard solution when moving to NixOS + Wayland.

**Whisper.cpp bindings:** `whispercpp.py` and `pywhispercpp` are newer/better than `whisper-cpp-python`;
both embed whisper.cpp as a submodule and track newer versions. Wrap behind a TGW audio-to-text
interface to enable swapping implementations.

**`discogs_client` deprecated:** Discogs officially marked it as "no longer maintained" and now
recommends using a generic REST client. TGW should wrap Discogs access behind an adapter in
`apis/lookup/discogs.py` and migrate to direct `httpx` calls (additive, isolates the breakage risk).

**Shipping APIs:** PirateShip has **no stable public API** (reverse-engineered only; fragile).
**EasyPost** is the recommended alternative: official Python client, rate shopping, label purchase,
address validation, tracking, insurance. Strong candidate for PP-FULFILLMENT-001 Phase 2.

**Barcode scanner:** `python-evdev` reads `/dev/input/event*` focus-independently — better than
keyboard-wedge mode for TGW's dedicated workstations. Relevant to PP-FULFILLMENT-001 hardware phase.

**USB scales:** `hidapi`/`hid` (Python `hid` package wrapping libhidapi) or `pyusb` — open by
vendor/product ID, read raw HID reports, decode weight. Better than shell-based approaches.

**Enrichment upgrades:** Go-UPC and Apify barcode/PriceCharting actors outperform upcitemdb free tier
significantly. Consider as replacement for PP-LOOKUP-001 upcitemdb primary source.

**eBay SDK:** `ebaysdk-python` last release ~April 2020; classified inactive; no support for modern
REST Sell APIs. TGW's current direct REST integration is already correct — no change needed.

#### Dependencies
- Syncthing config at `/opt/TGW/.local/syncthing/config.xml` ✅ present; API key parsed from `<apikey>` element
- Add `syncthing_config_path` to `tgw-api-config.json` (default: `/opt/TGW/.local/syncthing/config.xml`)
- Add `syncthing_url` to config (default: `http://127.0.0.1:8384`)
- PP-WM-001 (Qtile clipboard widget uses subprocess xclip; migrate to pydbus/KDE Connect)

---

### PP-VERIFY-001 — Catalog Assumption Verification + Hall Pass Flag

#### Problem
55K items accumulated over many years contain assumption violations — missing required
fields, invalid status combinations, stale eBay data, inconsistent location formats.
Currently there is no tool to enumerate violations at scale.

#### Design
**`tgw catalog-verify [--location X] [--limit N] [--write] [--fix]`**
- Scans ItemData or a subset; checks each item against a set of assumption rules
- Rules (examples): title not empty, title ≠ SKU, location format valid, has at least
  one photo, `ebay_category_id` is numeric, `verified` is YYYYMMDD format, no stale
  `TEMPLATE:` prefix in title, `#STATUS` is a recognized value, etc.
- Output: markdown checklist of violations grouped by type; SKU + field + violation
- `--write`: stamps `catalog_verified: {timestamp, by: "catalog-verify"}` on passing items

**Hall pass flag**: `catalog_verified` field in item JSON
- Set when item passes verification (or after manual operator review)
- Cleared automatically whenever any field is written (catalog-rebuild resets it)
- `tgw catalog-verify` skips items with `catalog_verified` set unless `--force`
- Prevents re-flagging manually confirmed edge cases (legacy items with intentional quirks)

#### Phases
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | `tgw catalog-verify` command; 9 assumption rules; markdown report by severity | ✅ **DONE (session 13)** |
| 2 | `catalog_verified` hall pass; clear-on-write in `_write_field`; `--force` to re-check | Next |
| 3 | Fix-in-place for auto-fixable issues (stale TEMPLATE: prefix auto-strip, etc.) | Future |

#### Phase 1 implementation (done)
9 rules implemented in `_verify_item()` + `cmd_catalog_verify()` in `api.py`:
- **critical**: `no_title`, `stale_template_prefix`, `json_parse_error`
- **warning**: `title_is_sku`, `title_too_short`, `no_location`, `no_photo`, `invalid_ebay_category`
- **info**: `bad_verified_date`, `unknown_status`

CLI flags: `--location`, `--limit`, `--severity`, `--output`, `--json`. 10 tests.

---


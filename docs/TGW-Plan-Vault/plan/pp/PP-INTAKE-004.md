# PP-INTAKE-004 — unified native Kotlin intake app ("our core data acquisition tool")

**Status:** PROMOTED 2026-07-11 from `docs/ai-plans/tgw-intake-app.md` (draft
2026-07-04). Supersedes PP-INTAKE-002 (camera app), PP-INTAKE-003 (xmouse
replacement); absorbs PP-TASKER-001. Scope EXPANDED 2026-07-11 (Concept 6) —
see "2026-07-11 expansion" below for what's new; everything above that is the
original 2026-07-04 draft, preserved not re-derived.

## Problem / motivation

Current intake UI is Tasker Scenes (floating overlay widgets) + AutoTools
WebScreens (HTML overlay panels) — a plugin-stacked overlay, not a real app.
It's fragile scaffolding for what it's being asked to do: barcode/photo/video
capture, SKU assignment, template selection, and remote macro-pad/form/VNC
control of the TGW desktop (PP-INTAKE-003's scope).

Separately, PP-INTAKE-002 and PP-INTAKE-003 were designed as two **Flutter**
apps. Dave's direction (2026-07-04): build **one universal app in Kotlin**
instead — camera/video/barcode scanning, the Tasker-replacement functions
(barcode scan→intake, photo trigger, notification response, custom SKU/
template/size entry — see `pp/PP-TASKER-001.md`), and basic item bundling —
built as a first-class app UI, not an overlay.

**Note (2026-07-11): this Kotlin app is a SEPARATE app from TGW's existing
Flutter app** — the two are unrelated. Do not conflate them (an earlier
synthesis pass this session made exactly that mistake and corrected it — see
the master plan's "Open discussion items," Web UI vs Flutter fork).

The other half of the ask: the current pipeline is **all-or-nothing**.
`workers/bundle_intake.py` watches `incoming/newitems/` and only creates the
item + enqueues `ai_identify` once every file in a bundle has been stable
(no writes) for 30s (`STABLE_AFTER_S`). Dave wants the backend to start
identification **as soon as it has a SKU and enough photos**, not wait for
the full capture session to finish.

## Constraints (from settled architecture)

- tgw-api fence: all ItemData reads/writes go through it — the app talks to
  `tgw-http`'s existing REST API, never writes ItemData directly.
- Output contract: every API call returns `{ok, ...}`.
- SKU format: `tgwYYYYMMDDHHMMSSmmm`.
- No cloud VM / near-serverless bias (per PP-EVENTD-001) — favor the existing
  tgw-http + Postgres + git-annex/GDrive stack over new infrastructure.

## Key finding: the incremental-ID path already mostly exists

This doesn't need to wait on PP-EVENTD-001 for its MVP (design-complete,
**now unfrozen 2026-07-11** but still not built). The fence already has the
primitives:

- `POST /api/items` — creates an item stub the moment a SKU exists (title
  can be empty/placeholder).
- `POST /api/items/{sku}/append` with `op: "photo"` — appends one photo at a
  time to `item["photos"]` (already implemented, `PP-FENCE-001` Layer 3).

So the app's job is: **create the item stub as soon as a SKU is assigned,
then append each photo/video individually as it's captured**, instead of
writing a local zip/dir bundle for `bundle_intake` to discover later. The
missing piece is entirely backend-side: `append_item`'s photo-append path
needs to check the running photo count and, the first time it crosses a
configurable threshold, enqueue `ai_identify` itself (mirroring what
`bundle_intake.py` currently does after its stability wait) — no new
infrastructure required for the MVP. `ai_identify` itself doesn't need any
change: it already just reads whatever photos exist on disk at call time
(`workers/ai_identify.py`, capped at `_MAX_PHOTOS_CLOUD`), it doesn't require
a "complete" count today.

**Refinement after the fact:** if more photos land after `ai_identify` already
ran once, the existing `ai_reidentify` flag (already wired: `PATCH` sets
`ai_reidentify: true` → re-enqueues `ai_identify`) is the natural re-scan
trigger — the app (or the append endpoint) sets it once the session actually
completes, so the identification gets one refinement pass with the full
photo set. No new mechanism needed here either.

**This is also Concept 4's ("justshoutit") backend requirement** — the same
trigger, same threshold, same fallback. Building it here unblocks that too.

## Proposed approach (2026-07-04 draft)

1. **Kotlin app (new, `PP-INTAKE-004`).** Single native app:
   - Camera/video capture + barcode scanner — see "2026-07-11 expansion"
     below for the settled hybrid approach.
   - SKU assignment UI (scan or generate) → `POST /api/items` immediately.
   - Photo/video capture loop → `POST /api/items/{sku}/append` per shot,
     streamed as captured, not batched.
   - Template/size/location entry — the "custom intake flow" from
     `PP-TASKER-001` — posts the same way `tgw-http`'s existing PATCH
     endpoint already accepts.
   - **Remote-control surface (refined, Dave 2026-07-04) — replaces "xmouse."**
     xmouse is a third-party Android app (Play Store/F-Droid/vendor repo): an
     SSH/xdotool-based macro pad + remote mouse. Dave's replacement design,
     more capable than PP-INTAKE-003's original 3-phase plan:
     - Drop the remote-mouse/cursor-emulation approach entirely — replace it
       with a real **VNC/RDP framebuffer viewer** (screen + keyboard +
       pointer all come for free from the protocol, no synthetic-input
       injection needed). Dave already tried the incremental fix — patching
       xmouse's `xdotool` calls to `ydotool` for Wayland compatibility (since
       xdotool is X11-only) — and it still has Wayland quirks. VNC/RDP
       sidesteps the whole synthetic-input-injection problem rather than
       chasing `ydotool` reliability further.
     - Add a **terminal pane** (SSH) — new, not in the original design.
     - Keep the **macro-button grid** (direct HTTP/SSH command dispatch —
       the `ic_mkitem`/`ic_data`/`ic_template` COMMAND:/DATA:/TEMPLATE:
       clipboard-relay tricks in `tgw.source`/`SHELL-AUDIT.md` get replaced
       by direct calls, no clipboard relay needed).
     - **Views are modular and user-composable, not one fixed layout:** just
       macro buttons; just VNC; VNC + keyboard + macro grid combined; etc.
       Goal — a photographer carries **one small, purpose-configured control
       surface** (macro-only, say) instead of needing a full computer or a
       tablet running someone else's fixed-layout app. This reframes
       PP-INTAKE-003's old "Phase 1/2/3 in sequence" roadmap as **panes to
       build, then compose** — macro-grid pane, VNC/RDP pane, terminal pane,
       inline web-form pane (still useful — was already scoped) — with a
       per-user/per-role layout picker on top, rather than a strict linear
       rollout.
     - **Scope check (Dave, same message): this may be too much to build all
       at once — and that's fine.** xmouse (the existing third-party app)
       remains genuinely valuable as a companion to the camera workflow
       *today*. There's no urgency to replace it wholesale; it can keep
       running as-is while the panes above get built incrementally,
       whichever order makes sense, with no fixed deadline to retire it.
       Not a build-everything-now requirement.
     - **Macro-grid pane: SEQUENCE AFTER PP-EVENTD-001 exists (settled
       2026-07-11)** — discrete macro commands become just another event on
       the same bus that already routes clipboard/barcode events, rather
       than the intake app reinventing its own SSH/dispatch mechanism.
       Doesn't kill the VNC/RDP-viewer or terminal panes (different problem
       — actually seeing/driving the desktop, not firing discrete commands).
       xmouse itself is "mostly just sending ssh commands" — low complexity,
       small/low-risk transport swap whenever this gets built.
   - Replaces the Tasker Scenes + AutoTools WebScreens overlay entirely —
     this becomes the primary on-device intake surface.

2. **Backend: incremental-ID trigger (small, additive change).**
   `http_server.py`'s photo-append path gains: after appending, if
   `len(photos) >= EARLY_ID_THRESHOLD` and `ai_identify` hasn't already run
   for this SKU (checked via existing state on the item — e.g. no
   `ai_identify_result` yet), enqueue `ai_identify` right there.
   **Decided (Dave, 2026-07-04): threshold = the ID call's own batch size**
   (`ai_identify.py`'s `_MAX_PHOTOS_CLOUD`, currently **6**) — fire as soon as
   there are enough photos to fill one full identification batch, not an
   arbitrary smaller number. **Fallback:** if the capture session completes
   with fewer photos than that (e.g. a quick item that only ever gets 2-3
   shots), fire on session-completion with whatever smaller set exists —
   never wait indefinitely for a batch that isn't coming. Both paths reuse
   the same `ai_reidentify` re-scan mechanism already in place for the
   refinement pass once the full ~12-shot set lands after an early fire.

3. **Event-server integration — see "2026-07-11 expansion" below, scope
   significantly raised from "later, not blocking."**

4. **Migration of PP-INTAKE-002/003 content.** PP-INTAKE-002's Foldio360
   root-bypass service is superseded (custom turntable direction, see
   below) rather than migrated. PP-INTAKE-003's remote-control roadmap
   survives in refined/expanded form (see the "Remote-control surface"
   bullet above — modular panes replacing the old fixed 3-phase sequence).

## 2026-07-11 expansion (Concept 6 — "finish the implementation plan")

**Scope confirmed:** handheld app now; turntable hardware + dedicated
data-collector device stay the doc's own separate, deferred, non-blocking
track — untouched by this pass (see "Refinement: custom turntable" below,
unchanged).

**The app must be a full bidirectional event-bus participant, not just a
delivery target.** Two requirements at once: (a) fully self-contained/
standalone — works with zero event-server dependency, offline-capable; AND
(b) both REACTS to the event server (consumer) AND ACTS AS an event server
itself (producer). This elevates PP-EVENTD-001's existing "Android/Tasker
delivery" framing (one-way HTTP POST push) to a true peer on the bus —
direct dependency on PP-EVENTD-001 for Phase 2 below.

**Absorb three specific LIVE Tasker capabilities natively, cross-device**
("directly on the camera or from the PC" — synced, not camera-only):
1. **Send attributes → write into item JSON.** This becomes justshoutit's
   write target too (voice-operated listing, PP-HERMES-EA-001's first
   Tigwa apprenticeship task) — Tigwa's voice-parsed output lands through
   the SAME interface the camera app exposes for manual attribute entry.
   Not a separate voice pipeline bolted on later.
2. **Save the item** — finalize/commit the current capture session.
3. **Advance to the next location** — revives the old `CurrentLocation`
   concept (the retired third symlink from `tgw.source`-era `tgwset()`, see
   PP-EVENTD-001's Radar requirements) as a live in-app action, batch-
   tracking physical location during a capture run.

**"Enhance it with tgw goodness"** — deeper native `tgw-http`/fence
integration than Tasker's HTTP-bridge glue allowed. This is the actual
reason to replace Tasker, not just UI polish.

**This app IS Tigwa's justshoutit execution surface** — confirmed link, not
a separate system.

**Barcode scanner — SETTLED (hybrid, not exclusive):** native ML Kit/ZXing
built in as the default (no external-app dependency, fits the established
no-root/no-workaround philosophy for this device) — **WITH a configurable
option to route to an external app instead.** Dave's current barcode app may
be hard to beat on speed (tuned to use all 8 CPU cores). Architecture
implication: the capture pipeline needs a **pluggable scanner backend**
(native OR external-intent), not a single hardcoded path — design the
interface for this from the start.

### Proposed phased build

*Phase 1 — Core capture + fence integration (standalone, zero event-server dependency):*
- SKU assignment → `POST /api/items` stub creation
- Photo/video capture loop → `POST /api/items/{sku}/append` per shot, streamed
- Native barcode scanner (ML Kit/ZXing default) + configurable external-app override
- Template/size/location entry (native rebuild of PP-TASKER-001's custom-intake-flow)
- "Send attributes / Save item / Advance location" actions — LOCAL-only first (direct `tgw-http` calls, no event-server dependency yet)
- **Backend: incremental-ID trigger** (`http_server.py` append path, threshold=6, session-completion fallback, reuses `ai_reidentify`) — see "Key finding" above; small and additive; unblocks justshoutit's concurrent-ID requirement immediately, independent of the rest of the app build

*Phase 2 — Event-bus integration (depends on PP-EVENTD-001 existing):*
- App becomes bidirectional event participant (producer + consumer, per the expanded requirement above)
- "Send attributes / save / advance location" actions sync camera↔PC
- justshoutit wiring: Tigwa's voice-parsed output → same attribute-write path
- Capture-before-SKU-exists correlation-ID problem (flagged below, unsolved in EVENTD's own design) — real design work lands here

*Phase 3 — Remote-control surface (modular panes, incremental, no fixed deadline):*
- VNC/RDP framebuffer viewer pane, terminal (SSH) pane, inline web-form pane
- Macro-grid pane — sequenced after PP-EVENTD-001 exists (see above)
- Per-user/per-role composable layout picker
- xmouse (third-party) keeps running in parallel, no retirement deadline

**HARD REQUIREMENT — training data is a permanent asset (justshoutit,
Dave 2026-07-11):** "Eventually that training data becomes learning fodder
for pipeline upgrades." The shout transcripts, AI-guessed values, AND
Dave's corrections/overrides must be **captured and persisted as their own
dataset** — NOT transient UI state consumed to fill the form and discarded.
Design the persistence in from the start (Phase 1), not as an afterthought.

**Voice/STT approach — OPEN, research and recommend.** PP-WHISPER-001
already has a proven, tested ffmpeg→`whisper-cli` pipeline
(`cmd_whisper_to_suggest`, `api.py:3462`) — currently sinks to the global
`SUGGESTIONS.md`, not to a specific item. justshoutit reuses this plumbing,
redirects the sink to an item-scoped write into `ai_hint` (the field
`set-template` already merges into, and ai_identify's hinted prompt reads).
Set `ai_reidentify: true` if voice arrives after the first ID pass — reuse
the existing flag, don't invent a new trigger. **Conflict rule, settled:
operator (shouted) always wins** over AI-identified values for any field
addressed.

## Files to change

None yet — planning only. When unblocked:

| File | Change |
|------|--------|
| `src/tgw/http_server.py` | `append_item`'s photo-append branch gains the early-`ai_identify`-enqueue check (small, additive). |
| new Kotlin app repo/module | Net-new — own repo (decided, see below). |

## Acceptance criteria

- [x] This doc exists (promoted from `docs/ai-plans/tgw-intake-app.md`).
- [x] Dave confirms the unification (one Kotlin app replacing both Flutter
      designs) and the incremental-ID approach (threshold=6, session-
      completion fallback) — 2026-07-04.
- [x] Scope expanded + barcode scanner + event-bus requirement settled —
      2026-07-11.
- [x] Seed build todos for the Kotlin app (Phase 1/2/3, mirroring the
      breakdown above) and the backend threshold-trigger change — done
      2026-07-16, todos #1485-#1489 (Phase 1a-1e). Not started building —
      seeded so it's ready to dispatch when Dave prioritizes it, per his
      "we shouldn't be that far yet, but I would rather have it planned
      and ready."
- [ ] `pytest -q` unaffected — no source code changed by this pass.

## Refinement (Dave, 2026-07-04, unchanged): custom turntable + two-device rooting split

**Turntable:** the best path is TGW's own turntable control, not the
Foldio360 rig — Dave wants a smaller shot count (**~12 photos per item**,
down from whatever the commercial option's smallest set currently forces)
and is evaluating alternative turntable hardware in parallel. This means
PP-INTAKE-002 §6.2's root-level Foldio360 zip-bypass is likely **not** the
long-term path — it was a workaround for not controlling the turntable
software. If a custom/DIY turntable is built, its stepper-motor + shutter
sequencing becomes new in-scope hardware-integration work for whichever
device drives it (own protocol, own timing — no vendor app to bypass).

**Two distinct device roles, two different rooting postures — this is the
key architectural split:**

1. **Dedicated turntable/data-collector device(s)** — a device permanently
   mounted at the turntable rig, running an automated capture loop (step
   motor → trigger shot → repeat ×~12 → done). Dave's call: these get
   **rooted deliberately, as a considered engineering choice** — "Android is
   a PITA about oversecuring... we need it to do a job." Root here isn't a
   workaround, it's fit-for-purpose configuration for fixed, single-function
   hardware nobody else touches.
2. **"Our camera" — the general handheld intake app** (the Kotlin app this
   plan is mainly about: barcode scan, ad-hoc photo/video, SKU assignment,
   template/size entry, remote macro-pad/form/VNC). This one **should run
   unrooted** — Dave's framing: *"rooting is a workaround for things we
   don't control."* Since TGW owns this app's entire software stack (no
   third-party app being bypassed), there's no reason it should need root at
   all. If anything in its design turns out to require root, that's a signal
   something is being worked around rather than built properly, and should
   be revisited rather than accepted.

This splits the earlier "target device / rooting strategy" open question
into two separate, already-mostly-answered questions (see below) rather
than one undecided one.

**Decided (Dave, 2026-07-04): own repo.** The Kotlin app lives in its own
repository, not as a module inside `trader-grims-warehouse` — parallels
PP-EVENTD-001's Go `clip-route` daemon, which also has an open "own repo vs.
internal module" question (still unresolved there; this decision doesn't
necessarily carry over automatically, since Go/Kotlin have different
tooling norms — flag if the same answer should apply there too).

## Product framing (Dave, 2026-07-04, unchanged): the handheld camera app may be sold commercially

Dave intends the general handheld intake app ("our camera" — barcode scan,
photo/video capture, SKU assignment) to potentially be **sold as a standalone
product**, not just used internally. His framing: it's "already extremely
useful as is" and would double as a marketing tool for **TGW itself** — the
"marketplace application" he means is TGW (trader-grims-warehouse) **run as
a platform for someone else's inventory**. Three models on the table, not
yet chosen between: (a) Dave operates it as a multi-tenant platform for
other resellers; (b) **resell the platform itself for others to customize
and self-host** — a licensed/distributed product rather than a hosted
service; (c) **TGW/Dave as the service provider for that customization** —
sell or even give away the core platform, monetize via setup/customization/
support services (an open-core-style model — Dave explicitly noted this
"would even work open source"). These aren't mutually exclusive and don't
need to be chosen between now.

**This is bigger than the intake app** — any of these models implies the
whole TGW backend (config per-deployment, generalized category-groups/
shipping-policy/pricing setup instead of Dave-specific hardcoding,
secrets/data isolation, setup/deployment tooling, and — if the open-source
path is ever taken — a real audit of what in this codebase is safe to
publish vs. must stay private) would need to generalize beyond a
single-operator deployment. **2026-07-11: explicitly reconfirmed still
deferred** — its own future planning topic, not decided by this pass, per
the master plan's "Open discussion items" (PP-INTAKE-004 platform question
→ acknowledged, structured, explicitly parked, not resolved).

What this plan DOES commit to now, regardless of how that larger question
resolves: build the handheld app to a genuinely polished, product-quality
bar rather than internal-tool quality, since it's a plausible flagship/demo
surface for that platform either way.

Bar for the handheld app specifically (not the dedicated turntable/
data-collector device, which stays internal-only, rooted, fixed-purpose):

- **UI/UX polish is now a real requirement**, not a nice-to-have — this is a
  product surface, not an internal tool.
- **TGW-backend coupling needs reconsidering.** If sold to other users, the
  app can't hard-depend on this specific `tgw-http` instance/schema — it
  likely needs a pluggable/configurable backend target (or a documented API
  contract other backends could implement), not a hardcoded fence client.
  This is a bigger design fork from the rest of this plan and needs its own
  scoping pass once Dave confirms the marketplace-platform direction — noted
  here so it isn't lost, not resolved.
- **Licensing/distribution** (Play Store, direct APK, pricing model) is
  out of scope for this doc entirely — a business decision, not an
  architecture one.
- The dedicated turntable/collector device is **not** part of this — it
  stays internal, rooted, single-purpose, no product framing.

## Open questions

- ~~"Enough photos" threshold~~ **Decided:** `_MAX_PHOTOS_CLOUD` (6), with a
  session-completion fallback for smaller final sets.
- ~~Barcode scanner integration~~ **Decided 2026-07-11:** hybrid — native
  ML Kit/ZXing default + configurable external-app override.
- **Custom turntable hardware** — what's the actual mechanism (stepper motor
  + microcontroller, e.g. Arduino/ESP32 driven over serial/BLE from the
  data-collector device, vs. a commercial turntable with a simpler open
  protocol)? Needs a hardware decision before the data-collector device's
  capture-loop software can be designed. Not blocking the handheld app work,
  which is independent.
- **Data-collector device target/rooting mechanics** — which device
  (existing spare Android hardware, a cheap dedicated tablet, etc.) and
  rooting method (Magisk/KernelSU per PP-INTAKE-002's original note)? This
  is now scoped to the *dedicated collector* only, not the general app.
- **Does `incoming/newitems/` + `bundle_intake.py` retire entirely** once the
  app talks directly to the REST API, or does it stay as a fallback path for
  manual/non-app photo drops (e.g. someone drags files in via desktop)?
  **Recommend keeping `bundle_intake` alive as the manual-drop fallback** —
  it's a working safety net, not technical debt. Adopted as-is 2026-07-11,
  not re-opened.
- **Capture-before-SKU-exists correlation ID** (2026-07-04, still open): at
  the moment capture is *initiated*, there is not yet a real item record in
  ItemData — SKU assignment and `POST /api/items` may happen slightly
  before, during, or after the first capture events fire. `clip-route` needs
  a session/capture-batch correlation ID (independent of SKU) so events
  arriving before an item exists can be held/buffered and later resolved.
  Real design work for PP-EVENTD-001's ingest path — lands in this doc's
  Phase 2.
- **STT approach for justshoutit** — open, research and recommend (see
  2026-07-11 expansion above).

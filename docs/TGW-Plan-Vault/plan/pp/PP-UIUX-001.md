# PP-UIUX-001 — UI/UX unification: full inventory, mapping, and spec (web + Flutter)

**Status: OPENED 2026-07-16**, absorbing the previously-orphaned "Web UI vs
Flutter app" discussion that had sat under a stale, undated-PP "Open
discussion items (for 2pm 2026-07-04 planning session)" heading in the
master plan since 2026-07-06 — never resolved, never given a real home.
Dave, 2026-07-16, on where this belongs: **"flutter vs web is in with the
ui inventory/tgw mapping/ui ux project. plan is to fully define then have
[the] entire set including web ui and flutter to the spec by [a] ui/ux
specialist coder."**

## The mandate

Not "decide Flutter vs. web" in isolation — that question only makes sense
once the actual scope is known. The real sequence:

1. **UI inventory** — catalog every existing operator-facing surface: web
   UI pages/routes (`PP-EDITOR-001`'s pipeline/editor/dashboard,
   `PP-ACTIONCONSOLE-001`'s action console, `PP-LISTEDITOR-001`'s revision
   apply), the Flutter app's actual screens (`apps/tgw_app/`, confirmed
   2026-07-06 to be browse-only today — no write/action capability), and
   any other operator surface not yet named here.
2. **tgw mapping** — map each surface to the backend it actually calls:
   which `/api/*` endpoints, which `tgw` CLI commands, which capabilities
   exist on one surface but not the other. The 2026-07-06 investigation
   already found the concrete gap this produces: `reference/TGW-HTTP-
   API.md` (dated 2026-06-04) documents what Flutter calls, but
   `PP-ACTIONCONSOLE-001`/`PP-LISTEDITOR-001` landed on a newer surface
   that doc never covered — Flutter isn't broken, it's calling a real API
   that's fallen behind the web UI's newer capabilities. This step is
   about producing a *current*, complete map, not trusting an
   already-known-stale one.
3. **Full spec** — once 1+2 exist, write the complete target spec for
   *both* surfaces (web UI and Flutter) as one coherent design, not two
   separately-evolving ones.
4. **Hand to a UI/UX specialist coder** — a dedicated executor role (new,
   not yet defined — analogous to `tgw-coder`'s scoped-executor pattern
   but for frontend/UX work specifically) implements against the finished
   spec. Not Claude's default role once the spec exists; matches the
   existing PP-HR-001 pattern of role-scoped execution rather than one
   generalist doing everything.

## Prior open threads this PP absorbs (preserved, not re-litigated)

**Web UI vs Flutter, 2026-07-06 investigation (todo #1227):** three
directions were sketched and explicitly left undecided — (A) extract the
action console's server-side logic into `/api/*` so both surfaces share
one backend contract (most work, keeps Flutter's real offline-catalog
advantage, `pp/PP-PORTABLE-CATALOG-001.md`); (B) make Flutter a thin
WebView shell over the existing web pages (fastest, loses native/offline
feel); (C) freeze Flutter, converge on a web PWA (simplest, no offline
satellite catalog). Also found: `flutter/` in this repo is the vendored
Flutter SDK source, not Dave's app — the real app is `apps/tgw_app/`;
orphaned `apps/{android,ios,web,windows,macos}/` folders from an earlier
`flutter create` need cleanup regardless of which direction wins.

**2026-07-11 nuance (still not resolved as a final decision, but real
constraints locked in):**
- Web UI is primary/most-complete today — a pragmatic choice, not a
  philosophical stance against Flutter.
- Flutter is NOT abandoned — real capabilities worth leveraging later
  (Dave: "I really like the flutter app and I believe we will be able to
  take advantage of its capabilities"). Current state: browse-only.
- **Hard constraint, already decided:** Flutter must reuse the same web
  backend functions the web UI calls, never duplicate logic — "so we do
  not have too much extra dev." This survives into this PP unchanged: any
  future Flutter work is a client against existing/extended `/api/*`
  endpoints, never a parallel implementation. Direction (A) above is the
  one consistent with this constraint; (B)/(C) remain on record as
  alternatives the full spec pass should weigh against it, not
  presumed the answer.
- Explicitly separate from PP-INTAKE-004's Kotlin camera app — a
  different, unrelated app; an earlier synthesis pass wrongly conflated
  them before being corrected.

## Sequencing, refined 2026-07-16 (Dave)

Not "pause Flutter until we decide" — **the web UI is the concentrated
focus right now (R1.6, the pipeline's "one true end-to-end" listing
pass), and Flutter keeps existing as-is in the meantime**, not actively
developed further: "even though the implementation is lagging, the
scaffolding exists and it works more or less." Once the web UI reaches
completion under R1, its behavior IS the spec — "when we have a
completed webui we will know what it needs to do exactly and apply that."
Two ways that spec gets applied to Flutter (and any other surface), not
yet chosen between:

1. **Manual application** — the UI/UX specialist coder role (mandate
   point 4, above) implements the finished spec against Flutter
   deliberately, one surface after the web UI.
2. **Simultaneous agentic build** — once the spec is genuinely complete,
   dispatch it to multiple agents building every UI surface (web UI
   refinements, Flutter, any future surface) *at the same time* against
   the one shared spec, rather than sequentially. Dave's own framing:
   "use an agentic approach that develops all of the ui simultaneously to
   our spec." This is a stronger version of mandate point 4's single
   "UI/UX specialist coder" — a fleet against one spec, not one executor
   working through surfaces in series. Which model wins (single
   specialist vs. fleet) is a decision for when the spec actually exists,
   not now.

This is also the concrete instance of the master plan's "parallel-track
discipline" note (near the R1 track table): PP-UIUX-001 stays a background
track — inventory/mapping/spec work continues opportunistically — while
R1 gets the concentrated focus, rather than either PP-UIUX-001 sitting
fully frozen or competing with R1 for attention.

## Why this matters beyond resolving an old fork

PP-INVENTORY-001's manifest/checklist workflow (`pp/PP-INVENTORY-001.md`)
explicitly deferred its own UI question here rather than picking a surface
unilaterally — this PP is where that answer comes from. Any future
operator-facing feature (this one included) should be spec'd against
whatever this project produces, not built against whichever surface
happens to be top-of-mind that session — that pattern (feature-by-feature
divergence between web UI and Flutter) is exactly what created the gap
this PP exists to close.

## Out of scope (this opening pass)

- Actually performing the inventory/mapping — real work for a dedicated
  session, not fabricated here.
- Picking direction A/B/C, or any other Flutter-vs-web resolution — comes
  out of the full spec, not decided in advance of it.
- Defining the UI/UX specialist coder role's contract — follows PP-HR-001's
  process (design ownership, dual review) once there's a real spec for
  that role to execute against.

## Next step

File a todo for the UI inventory + tgw-mapping pass (step 1+2 above) —
this is the concrete, scopeable first packet; steps 3-4 follow once it's
done and can't be usefully pre-specified before then.

## Cross-links
- `pp/PP-EDITOR-001` (master-plan section) — the current web UI umbrella.
- `pp/PP-PORTABLE-CATALOG-001.md` — Flutter's offline-catalog advantage,
  the strongest argument for direction (A).
- `pp/PP-INVENTORY-001.md` — a concrete consumer waiting on this PP's
  output before its manifest/checklist UI can be built.
- `pp/PP-HR-001` — the role-scoped-executor pattern the eventual UI/UX
  specialist coder role follows.
- `reference/TGW-HTTP-API.md` — confirmed stale as of 2026-07-06 relative
  to the web UI's actual current surface; needs refreshing as part of
  step 2, not before.

# PP-PLANDB-001 — plan/tracker tooling (full detail)

## PP-PLANDB-001 — plan/tracker tooling
Phases 1-4 done 2026-06-12→14: **P1** todo_items schema (`pp_ref`, `depends_on`,
`plan_anchor` columns, #109) · **P2** `tgw plan render` — wholly-generated
`plan/TGW-Taskboard.md` (#110) · **P3** `tgw plan check` — reconciles
plan↔tracker (orphaned pp_refs, stale anchors, mismatched done/open, #112) ·
**P4** `tgw plan status [PP-REF]` — one-line open/done/blocked rollup per PP
item (#132). P3 and P4 (`tgw plan check` / `tgw plan status`) run in the
mandatory session-start sequence in CLAUDE.md (Step 3); P2's output
(`TGW-Taskboard.md`) is read as reference, but `tgw plan render` itself is
not invoked at session start — it runs via the `plan_render` worker.

### Phase 5 — execution track / goal view (PROPOSED, Dave 2026-07-10)

**The ask, in Dave's words:** "all of the tasks to achieve the intended
product should be able to be viewed in order without the noise of equally
weighted items in other tracks." Concrete pain point: the audit#1143 cleanup
work just completed (todos #1171/#1182/#1198/#1213 this session, plus
earlier #1162-#1170/#1202/#1206/#1235/#1246) had to be gleaned by hand-
grepping `source=audit-1143` across the flat todo list — there was no single
view of "everything needed to finish this track, in order." Compounding it:
some of the same track's items live in Dave's own todo queue (agent=`db`),
not just Claude's, and today's flat per-agent lists never united them.

**What exists today that a v1 could use as-is (no new schema required):**
`source` (free-text, e.g. `audit-1143`), `pp_ref` (PP-* item), `depends_on`
(ordering signal already in the schema per Phase 1), and `agent`
(claude/admin/gemini/db — the cross-agent-unification piece). A track view
doesn't need new columns to exist; it needs a render mode that **filters** to
one track's items across all agents and **orders** by the dependency graph
(topological, using `depends_on`) rather than each todo's global priority
number — global priority is exactly the "equally weighted noise" problem:
an audit#1143 item at p95 reads identically to an unrelated p95 item from a
totally different track in the flat list, even though within its own track
it might be the very next thing to do.

**Shape (sketch, not yet designed in detail):** something like `tgw plan
track <pp_ref-or-source-value>` producing a rendered, ordered list — same
spirit as `tgw plan render`'s taskboard but scoped to one track and blind to
everything outside it. Test case once built: run it for `audit-1143` and
confirm it reproduces (in the right order) exactly the items worked this
session, with none of the unrelated backlog visible.

**Where this is headed (Dave, forward-looking — more to be planned, not
speced yet):** specific teams executing specific tracks end-to-end. The nix
flake is the working example that surfaced this need — today's session
independently arrived at exactly this pattern by hand: multiple
nix-touching findings (todo #1258's backup-mount durability fix, more
pending) got batched into one pending changeset in `~/tgw-flake` rather than
applied one at a time, because Dave wants "a bunch of flake updates to apply
... all at once." The anticipated future model: Dave (or Claude) submits
requirements against a track, a specialist team (e.g. a "nix specialist
team") compiles the accumulated requests into a single coherent deliverable
(one flake update, one PR, etc.) instead of a stream of one-off changes.
That implies track/goal becomes a first-class routing concept, not just a
display filter — todo metadata may eventually need an explicit
`track`/`owner_team` field once the team-routing design lands. **Not
building that yet** — this phase entry captures the initial ask (the view)
only; the team-routing piece is intentionally left unspec'd until Dave's
next planning pass.

### Done (designs in `pp/` or archive; tracker holds history)

**PP-EDITOR-001, PP-DATALEARN-001, PP-MULTIMODEL-001 removed from this list
2026-07-12** (Fable independent review #1338) — each was given its own
"Open" heading above on 2026-07-11 specifically because it has real open
work (PP-EDITOR-001: #1145 defect map; PP-DATALEARN-001: #1108/#144;
PP-MULTIMODEL-001: #1251), but the promotion never removed the matching
Done-rollup entry. See their headings under "Open — active or gated" above.

PP-EBAY-MIRROR-001 (P1/P1.5/P2) · PP-MIGRATE-001 ✅
2026-06-20 · PP-DEADLETTER-001 · PP-DOCFLOW-001 · PP-INTAKE-001 ·
PP-OFFER-001 · PP-OPS-001 · PP-PROMO-001 · PP-REF-002 ·
PP-REVISION-001 · PP-SHELL-001 · PP-STORE-001 · PP-TODO-001 · PP-VERIFY-001 (scaffold;
integration deferred) · PP-WM-001/PP-HM-001 (Sway/HM desktop) · PP-ADD-009 ·
PP-CI-001 · PP-CONTEXT-001 · PP-GLOBALS-001 (analysis) · PP-LISTING-001 ·
PP-LOOKUP-001 (Tier 1) · PP-PRICE-001/PP-PRICE-003/PP-PRICE-004/PP-PRICE-005 ·
PP-QUALITY-001 · PP-REF-001 ·
PP-REPRICE-001 (the markdown reducer — **defused s42**: minting off, cliff guard) ·
PP-SEO-001 · PP-STAGE-001 · PP-SYNC-001 · PP-FREESHIP-001 · PP-STRIKE-001.

**Superseded/obsolete:** PP-DEPLOY-001 (MX Linux image → superseded by PP-NIXOS-001) ·
PP-PRICE-002 (absorbed into PP-REPRICE-001) · PP-PLASMA-001 (delivered via CatioNIX
desktop split, a1131 Plasma).

**Misc. completed todos, folded from stray loose lines 2026-07-16 (no content
lost, each already had a doc filed under `dev-workflow/research/DONE-*` or
noted above):**

- #1053 data-scrub legacy eBay Trading API fields — 20,419 items modified, zero exceptions
- PP-PRICING-001 (self-powered comp engine extension) + PP-AMAZON-001 (Amazon FBM exploration) — design docs complete
- #1113 ebay_dole interim fix — dead code removed, test added
- #1138 `tgw revise --set` help text corrected (bare field names only, no nested expansion)
- #1338 Fable independent review of the 2026-07-11 retarget — 13 confirmed findings, 12 applied same session (1 live incident: reboot-resurrected workers incl. `pm_intake`, re-stopped)
- #1323 master plan retarget — catio development framework
- #1320/#1319 title-length guard + enforcement
- #1318 restore save-draft button
- #1258 backup alarm (db dump stale, rclone never completed)
- #1257 stale `ai_reidentify` flags cleared
- #1256 per-item Best Offer control
- #1255 Motors category tree cache built
- #1254 sync `marketplaceId` hardcoding fixed
- #1252 condition scrub + secrets facility
- #1249 dead-letter diagnosis (flagged #1265 bulk requeue needing Dave's go)
- #1240 broken tests fixed (`ebay_price.py`)
- #1239/#1210/#1211/#1238 code-review follow-ups (atomic-write fixes)
- #1236 `ebay_backfill_offers` fence-bypass fixed
- #1214 `ebay_motors_census` stale-data/ambiguity fix
- #1213 `ITEMDATA_ROOT` hardcoded path fixed
- #1211 photo-repair unlink safety fixed
- #1210 photosync-canary price diff fixed
- #1209 order-dependency bug fixed
- #1135 category recompile — 5,367 categories recovered

### Gated on R1 — named, designed later


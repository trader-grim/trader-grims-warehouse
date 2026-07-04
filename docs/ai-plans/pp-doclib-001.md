# pp-doclib-001: Research and docs library system (PP-DOCLIB-001)

**Status:** Draft — 2026-07-04
**PP ref:** PP-DOCLIB-001 (todo #1044)

## Problem / motivation

Research and reference material has accreted into six different locations with
no shared convention, audited 2026-07-04:

| Location | Count | Naming convention | Problem |
|---|---|---|---|
| `dev-workflow/research/` | 38 | none | Mixes actual research notes with `DONE-*`/session completion breadcrumbs — two different content types in one folder |
| `gemini/` | 7 | `GEMINI-TASK-NNN-*.md` | Consistent, fine as-is |
| `perplexity/` | 8 | `PERPLEXITY-NNN-*.md` | Mostly consistent; one stray `RESEARCH-*.md` file breaks the pattern |
| `reference/research/` | 8 | none | Ad-hoc titles, several truncated from browser/tool exports (e.g. `how effecient are claude ultracode and ultraplan m.md`) |
| `inbox/archive/` | 51 | timestamp-prefixed | Correct as a landing zone, but nothing ever graduates out — it's becoming the de facto permanent store by accident |
| `plan/pp/` | 26 | `PP-XXXX-001.md` | The canonical PP-item design-doc location; some PP items *also* have a doc in `docs/ai-plans/`, causing split-brain (e.g. `PP-FENCE-001.md`, `PP-LISTEDITOR-001.md` exist in both) |

Symptom that motivated this (Dave, prior sessions): can't reliably answer "did
we already research X?" without a full-text grep across 6+ directories with
inconsistent naming, so research gets silently re-done or lost. cross-links
between a PP-* plan item and the research that informed it are also
undiscoverable — nothing points from `plan/pp/PP-REPRICER-001.md` back to the
Perplexity/Gemini research that fed it, or forward to `docs/ai-plans/` if a
`/tgw-plan` pass exists for the same item.

**New fact as of today (2026-07-04, todo #1066):** PP-SEARCH-001 Phase 0 shipped
— a recoll index at `/opt/TGW/.recoll/` already covers the entire
`docs/TGW-Plan-Vault/` tree (441K docs indexed, includes zip contents). This
changes the shape of this design: full-text retrieval is already solved.
What's missing is (a) a stable taxonomy so *new* docs land in a predictable
place, (b) a thin `tgw docs` CLI wrapper so retrieval doesn't require knowing
recoll's query syntax, and (c) an explicit cross-reference convention.

## Constraints (from settled architecture)

- Workers are thin / tgw-api fence: N/A — this is docs-only, no ItemData reads.
- No hardcoded paths: the `tgw docs` CLI must read the vault root from
  `load_config()`, not a literal path (mirrors how `archive_root` was added
  in #1104).
- Catalog rebuild is always a job: N/A.
- This is additive and reversible — no existing file needs to move for the
  new taxonomy to take effect; a one-time migration pass can happen
  separately and incrementally (see Open questions).

## Proposed approach

### 1. Taxonomy (four buckets, not six)

Collapse the six current locations into four with a clear one-line test for
which one a new file belongs in:

| Bucket | Path | Test |
|---|---|---|
| **Source research** | `research/<source>/<slug>.md` | "This came from an external tool/model (Perplexity, Gemini, ChatGPT, web search) and is a raw or lightly-edited output." |
| **PP design docs** | `plan/pp/PP-XXXX-NNN.md` | "This is the design/status doc for one named PP-* item — the canonical, living reference." |
| **Session/activity log** | `dev-workflow/sessions/<slug>.md` | "This is a record of what happened in a session — a DONE/wrap-up/completion breadcrumb, not new design content." |
| **Inbox** | `inbox/` → `inbox/archive/` | "This just arrived and hasn't been triaged into one of the above yet." (already works this way — no change) |

`research/` replaces `gemini/`, `perplexity/`, `dev-workflow/research/`, and
`reference/research/` as a single parent with per-source subdirectories
(`research/gemini/`, `research/perplexity/`, `research/misc/` for the
ad-hoc/browser-export material). This keeps each source's existing internal
naming convention (`GEMINI-TASK-NNN`, `PERPLEXITY-NNN`) — only the parent
location consolidates.

`docs/ai-plans/` (created by the `/tgw-plan` skill) is retired as a
destination going forward: `/tgw-plan`'s template already matches `plan/pp/`'s
existing PP-*.md shape, so future `/tgw-plan` output should target
`plan/pp/PP-XXXX-NNN.md` directly instead of a second location. Existing
`docs/ai-plans/*.md` files get a one-line pointer added at `plan/pp/` if a
matching PP item exists, or move there directly if not yet duplicated.

`dev-workflow/research/`'s `DONE-*`/session-wrapup files move to
`dev-workflow/sessions/` (new — currently only has 1 file, `sessions/`
duplicate name is confusing; rename this subdir to disambiguate from the
existing `docs/TGW-Plan-Vault/sessions/`, OR consolidate the two — see Open
questions).

### 2. Cross-referencing convention

Adopt the memory-file `[[wikilink]]` convention already used successfully in
the Claude memory system (`docs/TGW-Plan-Vault/reference/*.md` files could
adopt the same). Concretely:
- Every `plan/pp/PP-XXXX-NNN.md` gets a `## Research` section listing
  `[[research/perplexity/PERPLEXITY-NNN-slug]]`-style relative links to any
  research that informed it.
- Every `research/**/*.md` file gets a one-line `**Feeds:** PP-XXXX-NNN` header
  if it was gathered for a specific PP item (optional — not all research is
  PP-scoped).
- Obsidian (the vault is already an Obsidian vault per CLAUDE.md) renders
  `[[...]]` links natively and shows backlinks — this is free, no tooling
  needed beyond adopting the convention.

### 3. `tgw docs` CLI — thin wrapper over recoll, not a new search engine

```
tgw docs search <query>      # recoll -t -q <query>, scoped to docs/TGW-Plan-Vault,
                              # formatted as path + one-line snippet
tgw docs show <path-or-pp-ref>   # cat the file; if given a PP-ref like
                                  # "PP-REPRICER-001", resolve to plan/pp/PP-REPRICER-001.md
tgw docs recent [N]           # newest N files by mtime across research/ + plan/pp/
```

Implementation: a new `src/tgw/docs_cli.py` (thin — matches "workers are thin"
spirit even though this isn't a worker) that shells out to `recoll -c
/opt/TGW/.recoll -t -q <query>` and reformats the output. No new index, no new
database — reuses the Phase 0 index from #1066 as-is. `tgw docs show
<PP-ref>` needs no recoll call at all, just a path-existence check against
`plan/pp/<ref>.md`.

### 4. Markmap integration (per the existing convention, not a new one)

`reference-library.md` (Claude memory) already documents the convention:
`markmap <file> --no-open -o out.html`, snapshots at `/opt/TGW/var/www/`.
Extend this, don't invent a second system:
- Add a `plan/pp/INDEX.md` markmap source (one bullet per PP-item, nested
  under status: active / paused / done) — regenerate whenever `tgw plan
  status` output changes meaningfully (manual for now; a `tgw docs
  render-index` command could automate this later, but that's a
  nice-to-have, not required for Phase 1).
- Do NOT markmap the full `research/` tree — it's retrieval-by-search
  (recoll), not retrieval-by-browse; a markmap of 60+ flat research docs adds
  no navigational value over a directory listing.

## Files to change

| File | Change |
|------|--------|
| `src/tgw/docs_cli.py` | New — `tgw docs search/show/recent` subcommands, thin wrapper over `recoll` CLI + `plan/pp/` path resolution |
| `src/tgw/api.py` | Wire `docs` subcommand into the `tgw` CLI dispatch (mirrors how other subcommands are registered) |
| `tests/test_docs_cli.py` | New — mock `recoll` subprocess call, test path resolution for `tgw docs show <PP-ref>`, test `--recent` sort |
| `docs/TGW-Plan-Vault/reference/reference-library.md` (or equivalent vault doc) | Document the four-bucket taxonomy so it's discoverable at session start, not just in this design doc |
| *(migration, separate follow-up, not this packet)* | Move existing files: `gemini/`→`research/gemini/`, `perplexity/`→`research/perplexity/`, `reference/research/`→`research/misc/`, split `dev-workflow/research/`'s `DONE-*` files into `dev-workflow/sessions/` |

## Acceptance criteria

- [ ] `tgw docs search "402"` returns real hits from today's incident docs
      (same recall as the raw `recoll -q` queries verified in #1066)
- [ ] `tgw docs show PP-REPRICER-001` prints `plan/pp/PP-REPRICER-001.md`
      without requiring the caller to know the full path
- [ ] `tgw docs recent 5` lists the 5 most-recently-modified docs across
      `research/` + `plan/pp/`, newest first
- [ ] New taxonomy documented in a vault reference file, referenced from
      `CLAUDE.md`'s reference table (mirrors how #1066 added the recoll path)
- [ ] Zero existing files broken by the CLI addition (it's read-only against
      the vault; the migration itself is a separate, explicitly-approved pass)

## Open questions

1. **Migration timing/ownership:** should the six-→-four consolidation
   (moving `gemini/`, `perplexity/`, etc.) happen as part of this build, or
   as a separate explicitly-approved pass once the CLI exists and is
   trusted? Recommend separate — moving 100+ files is exactly the kind of
   bulk operation that should get its own sign-off, not ride along with a
   new CLI tool. (Also: Syncthing-synced vault — bulk moves need the same
   care as any Syncthing-conflict-prone operation.)
2. **`docs/TGW-Plan-Vault/sessions/` vs `dev-workflow/sessions/`:** two
   session-log-shaped locations already exist (one has 1 file, the other
   would be new). Consolidate into one, or keep separate with a clear
   distinction (e.g. vault-level `sessions/` = human-authored session notes,
   `dev-workflow/sessions/` = agent-generated wrap-ups)? Needs Dave's call.
3. **`docs/ai-plans/` retirement:** 4 files currently live there
   (`clipboard-concept.md`, `PP-FENCE-001.md`, `PP-LISTEDITOR-001.md`,
   `tgw-intake-app.md`). Two are already-duplicated PP items (delete the
   `ai-plans/` copy once confirmed `plan/pp/` has the canonical version?),
   two (`clipboard-concept.md`, `tgw-intake-app.md`) aren't yet promoted to
   named PP items — do they get PP-refs now, or stay as free-floating design
   docs until promoted? Needs Dave's call, ties into whether PP-CLIP-001's
   Phase 2 work formally absorbs the clipboard-concept doc.
4. Should `tgw docs render-index` (the markmap auto-regeneration) be built
   now or deferred? Recommend deferred — manual `markmap` invocation after
   plan changes is a 10-second operator action, not worth automating until
   it's actually annoying.

## Added 2026-07-04 — Perplexity footnote/citation pass (todo #1141)

Dave: add a plan item to have Perplexity footnote the master plan (and
key PP docs) with citations to our own reference documents and to
external resources — the same numbered-footnote style Perplexity already
produces on its own research drops (e.g. `pricing-research-ui.md`'s
`[^1_1]`-style citations linking claims back to sources).

**Why this fits here — the real intent (Dave, follow-up):** this is not
just documentation hygiene. Right now, when Dave asks "did you read X"
or "how did we decide Y," the answer requires a search — grep the vault,
re-derive context, hope the right doc turns up. **The goal is for the
plan itself to be a direct-lookup surface**: every non-obvious claim in
the master plan/PP docs links straight to its source (an internal
reference doc, a past research thread, an external API doc, a specific
code location) so a "did we look at that" question resolves to "yes,
here it is" — a citation to follow, not a search to run. Footnotes are
the *external*-facing half of this (linking a plan assertion to an eBay
API doc, a research thread, or a `reference/*.md` file); `[[wikilink]]`s
are the *internal* half (vault-to-vault). Both serve the same end: zero
search burden when revisiting a past decision.

**Not scoped yet — needs a decision before building:** does this run as
a one-time pass over the current master plan + active PP docs, or an
ongoing practice applied to new plan writing going forward (or both —
backfill once, then adopt going forward)? Which documents are in scope
first (just `TGW-Master-Plan.md`, or the `pp/*.md` design docs too)?
Does "external resources" mean live Perplexity research calls per claim
(cost/quota implications — Perplexity access is presumably outside our
existing quota-tracked pools, worth checking), or reusing citations
already gathered in past research drops sitting in the vault? Needs
Dave's input on scope before any build.

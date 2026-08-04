# PP-CODEGRAPH-001: structural graph + invariant catalog so agents see design convergences before they miss them

**Status:** Draft — 2026-07-14
**PP ref:** PP-CODEGRAPH-001 (currently filed as a deferred idea in
`docs/TGW-Plan-Vault/plan/FUTURE-IDEAS.md`; this doc is the planning pass that
determines whether/how it promotes to an active master-plan PP)

## Problem / motivation

Dave's framing, direct: coders and planners (agents) don't have insight into
the interconnections and intricacies of the design. When a cross-cutting
convergence exists, agents miss it — and when one is found, it doesn't get
resolved into working process, just logged as a finding for someone to find
again later, one file at a time.

This is not hypothetical. Concrete cost already paid by this project:

- **The fence-bypass pattern** (writes into `ItemData/<SKU>/` or catalog
  files that skip `items.atomic_write_json()` / the tgw-api fence) was found
  independently, across separate PP-COHESION-001 audit sessions, in at least
  9 files: `config.py`, `api.py`, `revision.py`, `scrub.py`,
  `photo_history_recovery.py`, `http_server.py`, `mcp_server.py`,
  `itemdata_scrub.py`, and (todo #1383, filed today) `tools/photo_history_recovery.py`.
  Each instance required a human- or agent-run manual sweep to surface. There
  was never one pass that found all of them — invariant A1/A4 in
  `reference/invariants.md` even says so explicitly: *"a grep audit... in CI
  would pin this; not added as a pytest because it is a style gate, not
  behavior."* That line is this problem, already named, un-acted-on.
- **`status` vs `#STATUS`** — `items.statusupdate()`, `verifiedupdate()`, and
  `bulk_edit` all silently diverged onto the legacy `#STATUS` key instead of
  the canonical `status` field. Undetected until forensic archive-diffing
  (`docs/TGW-Plan-Vault` memory `reference-status-vs-hashtag-status`) — not
  because the bug was subtle, but because nothing connected "these three
  functions write the same logical field" across files.
- **NATS/JetStream wired to the wrong door** — built under PP-AIOPS-001
  Phase 1, then PP-POSTGRES-001's design (filed same week) needs it wired
  differently. Two PPs, same piece of infrastructure, no mechanism that
  would have surfaced the conflict before both were half-built.
- **PP-CATALOG-INCR-001 vs PP-POSTGRES-001** — an explicit, still-open
  premise conflict (JSON-is-truth vs Postgres-is-truth) sitting unreconciled
  in the plan right now.

The common shape: each of these is a relationship between two-or-more parts
of the codebase/plan that's real and load-bearing but isn't visible from
any single file, packet, or PP entry in isolation. Today the only mechanism
that finds these is a wide manual audit (PP-COHESION-001-style), which is
expensive, non-exhaustive by construction (an 8-angle review still missed
instances later found in follow-up sessions), and produces a *finding*, not
a *prevention* — nothing stops the next packet from reintroducing the same
class of bug in file #10.

## Constraints (from settled architecture)

- **tgw-api fence** — the invariant catalog described below should be seeded
  from, not duplicate, `reference/invariants.md`; the fence itself stays the
  actual enforcement point, this is a *detection* layer on top of it.
- **Secrets from `secrets_root`** — any new service credentials (if a
  hosted DB is used) go through the existing single-facility pattern, no new
  per-provider credential file.
- **Catalog rebuild is always a job** — if the code graph needs periodic
  rebuilding, it follows the same job/queue pattern as catalog rebuilds, not
  an inline call from request handlers.
- **Direct tension, surfaced not ignored:** this proposal cuts against two
  standing rules and the design below has to answer for that, not route
  around it:
  - *"Keep flake surface minimal"* — iterated-on tools stay userspace until
    proven; a new graph database server is exactly the kind of thing that
    rule exists to keep out of the flake prematurely.
  - *"Improvements are missing pieces, not new subsystems"* — scope as the
    smallest connecting piece between existing organs. A full 4-layer stack
    (graph DB + SMT solver + trace DB + MCP unification service) is the
    opposite of smallest-piece on its face.

  The proposed approach below resolves this by **not** introducing a new
  database server for Phase 1 — see below.

## Proposed approach

**Reject the source research's FalkorDB recommendation for Phase 1.**
FalkorDB is a new graph-database server — new flake surface, new backup
target, new failure mode, for a codebase this size (a few hundred Python
files). The actual need — "which callers reach this function/pattern" — is
answerable as edge rows in the database TGW already runs and already backs
up: **Postgres (`state_machine`)**. A code graph is just two tables (nodes,
edges); Postgres handles that natively with recursive CTEs for the
transitive-reachability queries this problem actually needs ("everything
that eventually calls `atomic_write_json`"). Revisit FalkorDB only if a
Postgres-table graph proves too slow or too awkward at TGW's actual repo
size — that's an engineering escape hatch, not a Phase-1 requirement.

Similarly, **defer Z3 and DuckDB entirely out of Phase 1.** Neither
directly serves the demonstrated problem (finding convergences across
files/PPs) — Z3 verifies semantic correctness of a single diff against
typed pre/postconditions (a different, harder problem TGW hasn't hit yet),
and DuckDB's execution-trace use case is about runtime behavior, not static
structure. Building either now would be solving problems that haven't cost
this project anything yet, while the graph+catalog pair directly answers
problems that already have receipts (the list above).

### Phase 1 — structural graph + invariant catalog (this is the actual ask)

**1a. Code graph, Postgres-backed.**
- Build via a Tree-sitter Python-grammar parse of `src/tgw/` (and
  `tools/`, `scripts/` if in scope) into two new tables in `state_machine`:
  `code_graph_nodes` (file, symbol, kind: function/class/module) and
  `code_graph_edges` (from_node, to_node, kind: CALLS/IMPORTS/WRITES_PATH).
- Rebuild as a job (matches "catalog rebuild is always a job"): a new
  `tgw codegraph rebuild` command / queue entry, not a live per-request
  parse. Triggered manually at first (post-merge, or on-demand before a
  planning session); a systemd timer is a later, not Phase-1, decision.
- One specific edge kind directly targets the fence-bypass class of bug:
  `WRITES_PATH` — any call site that constructs or writes to a path under
  `ItemData/`, independent of whether it goes through `items.py`. A single
  query — "all `WRITES_PATH` edges whose target is not routed through
  `atomic_write_json`" — reproduces what took multiple PP-COHESION-001
  sessions to find by hand. **Acceptance criterion below is built around
  re-deriving the already-known 9-file list with this one query**, as the
  proof this approach actually works before trusting it on unknown cases.

**1b. Invariant catalog, Postgres-backed, seeded from `reference/invariants.md`.**
- One row per invariant (A1, A2, ... F1): id, statement, enforcement
  location(s), status (✅/⚠️/❌), linked test file(s). `reference/invariants.md`
  stays the human-readable canonical doc — the catalog is a machine-queryable
  mirror, regenerated from it (or the two are kept in sync by a lint check),
  not a second source of truth authored independently.
- Where an invariant statement names a pattern the code graph can check
  (e.g. A1 "every write... goes through `atomic_write_json`"), link the
  invariant row to a graph query that checks it. Not every invariant is
  graph-checkable (many are behavioral/runtime) — that's fine, the catalog
  just records which ones are and aren't.

**1c. One new MCP tool exposing both to agents: `tgw_impact_query`.**
- Input: a symbol name, file path, or invariant id.
- Output: transitive callers/callees (from the graph), any invariant rows
  whose statement references the same pattern, and — critically — any
  *other* PP or todo entries that touch the same file/symbol (a plain-text
  join against the existing `tgw todo`/plan tracker, not a new index).
- This is the direct answer to "how does a packet-scoped coder agent
  actually use this": `tgw-coder`'s contract (PP-HERMES-EA-001) gets a new
  step — before editing a file named in a packet, call `tgw_impact_query`
  on the target symbol/pattern and report anything it surfaces (other
  callers of the same unsafe pattern, other open PPs touching the same
  file) in the result manifest, the same way it already reports
  out-of-scope findings. This turns "coder found problem in this one file,
  logs a finding" into "coder found problem, graph shows 3 more instances,
  reports all 4 in one manifest" — the actual fix for "not fully resolved
  into working process."

### Deliberately out of scope for Phase 1 (revisit only if Phase 1 proves the pattern)

- Z3 SMT verification of diffs
- DuckDB execution-trace store
- Any standalone graph-database server (FalkorDB or otherwise)
- Automatic/scheduled graph rebuilds (stays manual/on-demand until Phase 1
  proves useful enough to be worth the job-scheduling investment)
- Cross-PP conflict *resolution* (this system can surface that
  PP-CATALOG-INCR-001 and PP-POSTGRES-001 both touch the same file/pattern;
  it does not resolve which one wins — that stays Dave's/the planner's call)

## Files to change

| File | Change |
|------|--------|
| `src/tgw/codegraph.py` (new) | Tree-sitter parse → node/edge extraction; `rebuild()` entry point |
| `src/tgw/queue/` or a new `tgw codegraph rebuild` CLI command | job wrapper around `codegraph.rebuild()` |
| Postgres migration (new) | `code_graph_nodes`, `code_graph_edges`, `invariant_catalog` tables in `state_machine` |
| `scripts/seed_invariant_catalog.py` (new) | one-time + re-runnable parse of `reference/invariants.md` into `invariant_catalog` rows |
| `src/tgw/mcp_server.py` | new `tgw_impact_query` tool (~line 465, following existing `@mcp.tool()` pattern) |
| `.claude/agents/tgw-coder.md` | add the "call `tgw_impact_query` before editing" step to the executor contract |
| `docs/TGW-Plan-Vault/reference/invariants.md` | no content change; becomes the authored source the catalog mirrors |

## Acceptance criteria

- [ ] `tgw codegraph rebuild` runs as a job, populates `code_graph_nodes`/`code_graph_edges` from a real parse of `src/tgw/`
- [ ] A single `WRITES_PATH`-vs-`atomic_write_json` query reproduces the known 9-file PP-COHESION-001 fence-bypass list (proof the graph actually models the real bug class, not just a demo)
- [ ] `invariant_catalog` is seeded from `reference/invariants.md` and a spot-check of 5 invariants (e.g. A1, A2, A4, A5, plus one from a later section) matches the doc exactly
- [ ] `tgw_impact_query` is callable via MCP and returns graph + invariant + cross-PP results for at least one real historical case (e.g. querying `atomic_write_json` surfaces the fence-bypass files; querying `status` field writes surfaces the `#STATUS` divergence)
- [ ] `tgw-coder`'s contract is updated and at least one real packet execution shows the new step firing (result manifest includes an impact-query section)
- [ ] No new item in the Nix flake — Phase 1 runs entirely on existing Postgres + a Python dependency (`tree-sitter`, `tree-sitter-python`) added to the existing venv, not a new systemd service

## Open questions

- **Repo scope**: `src/tgw/` only, or also `tools/`, `scripts/`, `nix/`? (The
  fence-bypass instance found today, todo #1383, was in `tools/`.)
- **Rebuild cadence**: manual/on-demand is proposed for Phase 1 — is that
  acceptable, or does Dave want it wired to a git hook / CI step from the
  start?
- **Who owns invariant-catalog sync** when `reference/invariants.md` is
  edited by hand (as it regularly is) — a lint check in `tgw plan check`,
  or manual re-seed?
- **Does Dave want the cross-PP-touch join in `tgw_impact_query` (1c) to
  also flag conflicting *premises* (like the CATALOG-INCR/POSTGRES case),
  or only shared *files*?** Premise-level conflict detection is a much
  harder, more speculative feature — worth scoping separately if wanted.
- **Phase 2 trigger**: what evidence would justify revisiting Z3/DuckDB/a
  dedicated graph DB? Proposed criterion above (Phase 1 demonstrably misses
  something concrete) — confirm or adjust.
- **Relationship to PP-CATIONIX-001's permission architecture**: should
  `tgw_impact_query` write access (e.g. auto-filing the cross-references it
  finds as todos) wait for that scoped-agent-authority work, or is read-only
  query access fine to ship independently now?

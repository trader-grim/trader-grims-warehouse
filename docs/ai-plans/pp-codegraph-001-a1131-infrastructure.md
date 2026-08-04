# pp-codegraph-001-a1131-infrastructure: standing up the full code-graph/invariant/trace stack on a1131

**Status:** Draft — 2026-07-14, awaiting Dave's additional research before the build session
**PP ref:** PP-CODEGRAPH-001
**Supersedes:** the Phase-1-only, Postgres-on-tgw-prod scoping in
`docs/ai-plans/pp-codegraph-001.md` — Dave's decision (2026-07-14): build the
full stack (graph DB + Z3 + DuckDB + MCP unification), hosted on **a1131**,
not tgw-prod. That decision resolves the tension the earlier doc flagged
(new flake surface on tgw-prod) by moving the infrastructure to a host
that's already a separate, more experimental machine — see "Why a1131"
below. This document is infrastructure-establishment planning only: what
needs to exist before any of PP-CODEGRAPH-001's actual graph/catalog/query
logic gets built. No packages are installed and no code is written by this
document.

## Why a1131 (not tgw-prod)

- a1131 is already Tigwa's office and TGW's designated thermal-relief
  compute (`CLAUDE.md`: "share Dave+Claude precisely for thermal relief...
  run your own heavy checks... there"), with 4 cores, 19GB RAM, 200GB disk
  (169GB free, confirmed live 2026-07-14) and no production traffic
  dependent on it — a materially different risk profile than tgw-prod,
  which runs the actual pipeline and is already thermally sensitive.
- a1131 is "portable/client-shaped" (`nix/hosts/a1131.nix`: "no workers, no
  tgw-http") — it doesn't run the state_machine Postgres, doesn't run
  pipeline workers, and its own flake is already less minimal than
  tgw-prod's by design (KDE Plasma, LanMouse, Codex/Aider/Claude Code
  toolkit, bubblewrap for sandboxing). New infrastructure here doesn't
  compete with the same "keep the flake surface minimal" constraint that
  applied to tgw-prod in the earlier draft.
- It already has the toolkit this infrastructure needs to interoperate
  with: Tigwa's full Hermes instance, Codex, Aider, Claude Code CLI — all
  installed under the `tigwa` account (uid 1001, NOPASSWD sudo,
  key-authenticated from `db@tgw-prod`).

## Components (per Dave's direction: build all of them)

| Component | Role | Packaging note |
|---|---|---|
| **FalkorDB** | code graph store (nodes/edges: files, classes, functions, CALLS/IMPORTS/WRITES_PATH) | Not in nixpkgs as a standard package — needs a packaging decision (see Open questions). FalkorDB is a Redis module (RedisGraph successor); typical distribution is a Docker image or a `redis-server --loadmodule` setup. |
| **Tree-sitter** (+ `tree-sitter-python` grammar) | parses `src/tgw/` into the graph's nodes/edges | `tree-sitter` is in nixpkgs; grammars usually come as separate packages or are built from source — confirm `tree-sitter-python` availability, else vendor via pip/uv in the project's Python env. |
| **Z3** | SMT verifier — checks candidate diffs against the invariant catalog | `pkgs.z3` (CLI + libs) is in nixpkgs; Python bindings via `pkgs.python3Packages.z3-solver` or pip — confirm which the a1131 Python env should use. |
| **DuckDB** | per-commit execution-trace/perf/coverage store | `pkgs.duckdb` is in nixpkgs — straightforward. |
| **Invariant catalog** | structured mirror of `reference/invariants.md` | Storage engine not yet decided — could be its own DuckDB table (avoids standing up Postgres at all on a1131) or a lightweight Postgres if Z3/graph tooling wants relational features DuckDB doesn't give cleanly. Flagged as an open question below. |
| **MCP unification server** | exposes `get_impact_graph`/`get_invariants`/`verify_diff`/`get_trace_history` to Hermes/Tigwa/Claude | New Python service, same shape as `src/tgw/mcp_server.py` — but a new one on a1131, not an addition to the existing tgw-prod MCP server (different host, different data). |

## Data flow — what does the graph actually parse?

The graph's source of truth is `trader-grims-warehouse`'s own source tree
(`src/tgw/`, and whatever else Dave scopes — `tools/`, `scripts/`). a1131
already has a git checkout, but **it's a known-stale one** (memory
`feedback-a1131-claude-account-oauth`/CLAUDE.md: "a1131's repo checkout can
be stale (#1082) — sync repo state before trusting its test results"). This
infrastructure needs an explicit, repeatable sync step (e.g. `git pull` or
`git fetch` + checkout of the branch actually being worked, run
immediately before any graph rebuild) rather than assuming the checkout is
current — the graph would otherwise silently model stale code, which is
worse than no graph at all for a tool whose whole purpose is catching
things humans/agents miss.

The read-only NFS mounts already on a1131
(`/opt/TGW/mnt/tgw-prod/{data,log}`) are for **ItemData and logs**, not
source code, and are unrelated to this — do not conflate "the graph needs
fresh source" with "the graph needs live data access." No ItemData/business
data needs to touch this infrastructure at all; it only ever parses code.

## Access model — who queries this, from where

- Locally on a1131: Tigwa (full Hermes instance) and any Claude/Codex/Aider
  session running there.
- Remotely, from tgw-prod: a `tgw-coder`/`tgw-runner-review` packet running
  against the real repo checkout on tgw-prod would need to reach a1131's
  MCP server across the LAN. TGW already has a working pattern for
  cross-host MCP/SSH access (the `tigwa@a1131` → `db@tgw-prod` key,
  `TGW_MCP_READONLY` gating on the existing tgw MCP server) — this should
  reuse that pattern, not invent a new one. Exact mechanism (SSH tunnel,
  direct LAN reachability, or something else) is an open question below.

## Resource budget check

a1131: 4 cores, 19GB RAM (12GB free at last check), 169GB free disk.
FalkorDB (Redis-based) and DuckDB are both lightweight for a codebase this
size (a few hundred Python files) — this is not a concern at TGW's current
scale. Z3 is CPU-bound per-query, not a standing resource draw. No
component here is expected to compete meaningfully with a1131's existing
role (Tigwa's office, thermal-relief compute) at today's scale; revisit if
that changes.

## Open questions — for the pre-build-session research to help resolve

1. **FalkorDB packaging**: Docker container vs. building/vendoring the
   Redis module directly vs. some other distribution. Whichever way, does
   it run as a NixOS service (`nix/hosts/a1131.nix`, matching how
   `mbpfan`/`syncthing`/etc. are already declared there), or in the
   `tigwa` account's userspace matching the rest of her toolkit
   (Codex/Aider were installed via `nix profile install`/pipx, not the
   system flake)? This is the same "flake surface vs. userspace" question
   from the original draft, now applied to a1131's own flake rather than
   tgw-prod's — worth Dave's explicit call either way, not a default
   assumption.
2. **Invariant catalog storage**: DuckDB (one fewer service to run) vs. a
   dedicated Postgres instance on a1131 (relational features, closer match
   to how `reference/invariants.md`'s structure already reads). Z3 doesn't
   require either — it operates on in-memory formulas — so this is purely
   about what's most convenient for storing/querying the catalog itself.
3. **Cross-host access mechanism**: how does a tgw-coder packet running on
   tgw-prod actually reach a1131's MCP server — SSH tunnel (matching the
   existing `tigwa@a1131`/`db@tgw-prod` key pattern), direct LAN
   reachability with its own auth, or does packet execution route through
   Tigwa on a1131 instead of querying it directly? This has real security-
   surface implications (a1131's MCP server would be reachable from
   tgw-prod, or vice versa) worth deciding deliberately.
4. **Repo sync mechanism**: a scheduled `git fetch`/pull before every
   rebuild, a webhook/push trigger from tgw-prod on merge, or purely
   manual (Dave/Tigwa runs a sync command before working with the graph)?
   Ties into "catalog rebuild is always a job" — this should probably be a
   job too, just running on a1131 instead of tgw-prod.
5. **Scope of what gets parsed**: `src/tgw/` only, or also `tools/`,
   `scripts/`, the Nix flake itself? (The earlier draft's acceptance
   criterion — reproducing the known 9-file fence-bypass list — needs
   `tools/` in scope, since todo #1383's instance lives there.)
6. **Relationship to the deferred MCP tool design** (`get_impact_graph`,
   `get_invariants`, `verify_diff`, `get_trace_history` from the original
   research) — does the full build implement all four from the start, or
   still stage them (graph+invariants first, `verify_diff`/Z3-backed once
   the catalog is populated and proven)?

## Explicit non-scope for this planning document

- No packages installed, no services started, no code written.
- No decision yet on nix-flake vs. userspace packaging (open question 1).
- No integration with `tgw-coder`'s contract yet (that was speculative
  design in the earlier draft — revisit once the infrastructure itself
  exists and Dave's new research is in hand).

## Next step

Dave is bringing additional research before the build session. When that
arrives: fold it into this document (or supersede it, if the research
changes the shape enough), resolve the open questions above, and only then
move to an actual build/execution plan.

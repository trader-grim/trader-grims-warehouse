# Context-burden review — retrieval-first Plan Vault access

**Date:** 2026-07-15  
**Owner:** Tigwa  
**PP:** PP-KNOWLEDGE-001  
**Input reviewed:** `inbox/tigwa/CLAUDE-REPORT-2026-07-15-startup-context-burden.md`

## Decision summary

Claude's diagnosis is correct: routine full loading of the Master Plan is a structural context tax. The safe answer is **not** to replace or summarize away the Master Plan. Keep it canonical and versioned; add deterministic, source-linked retrieval views so a task loads the smallest authority-bearing slice needed.

Do not change `CLAUDE.md`, activate an intake worker, or split/move plan history until the retrieval path has been built and verified against the canonical source.

## Evidence checked

| Fact | Observed evidence |
|---|---|
| Mandatory full-plan startup load exists | `CLAUDE.md` Step 2 executes `cat docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md`. |
| Current plan size | 1,759 lines / 114,601 bytes. |
| Startup instructions size | `CLAUDE.md`: 412 lines / 25,940 bytes. The plan is the largest fixed startup document. |
| Plan shape | 65 level-2 sections; largest include PP-COHESION-001 (126 lines), PP-KNOWLEDGE-001 (124), PP-PLANDB-001 (117), PP-RUNBOOK-001 (99), PP-CODEGRAPH-001 (80), plus current state/open-discussion material. |
| Existing plan tools | `tgw plan` currently has only `render`, `check`, and `status`; it has no source-linked `brief`/section retrieval command. |
| Search foundation | Recoll index exists at `/opt/TGW/.recoll/`; current status reports 648,271 indexed docs. `recollq -c /opt/TGW/.recoll 'invariants E5'` returns results. |
| Search limitation | `recoll -q` is a GUI command and fails headlessly on tgw-prod. `recollq` is the usable CLI. Exact hyphenated PP identifier query `PP-HERMES-EA-001` returned zero results, so fuzzy search is not a sufficient authoritative PP lookup. |
| Index freshness | `idxstatus.txt` reports `hasmonitor = 0`; no Recoll system timer/unit was found. The index is useful recovery infrastructure, but its freshness must not be assumed for current plan authority. |

## Why a retrieval layer is preferable

A plain summary creates a second truth that will drift. Pure full-text search is also insufficient: it can miss exact identifiers, return stale material, and does not encode the current Master Plan section boundary.

A generated structural index can instead point directly into the canonical plan and make every retrieval auditable:

```text
canonical TGW-Master-Plan.md
        ↓ deterministic parse + hashes
plan section index / PP map
        ↓ task-specific retrieval
exact Master Plan section + linked PP detail + current tracker status
```

The master remains the source. The index is disposable/rebuildable evidence of structure.

## Recommended staged path

### Phase 1 — structural index, no workflow behavior change

Build a deterministic index from the canonical Master Plan only. Each entry should include:

```text
master_plan_sha256
index generation timestamp
heading text and heading level
line and byte range
section SHA-256
PP reference(s) detected from the heading
linked `plan/pp/PP-*.md` path when it exists
```

Acceptance: rebuilding without a plan change produces identical section entries; a changed plan hash makes stale index use obvious.

### Phase 2 — explicit task bootstrap command

Add a read-only `tgw plan brief --pp PP-...` (or similarly named command). It should print a bounded source packet, not a model summary:

```text
1. canonical Master Plan hash and exact matched section
2. linked PP detail document(s), if present
3. `tgw plan status --pp ...` output
4. direct links to referenced runbook/invariant/reference docs
5. retrieval warnings: missing PP, ambiguous PP, stale/missing index
```

A task with no PP or multiple relevant PP items should require a human/agent selection rather than silently guessing.

### Phase 3 — optional Recoll wrapper, separate from authority

Provide a headless wrapper around `recollq -c /opt/TGW/.recoll`, not `recoll -q`. It is valuable for discovery/recovery across research and archive material, but results must show paths and index-freshness status. It must not replace the exact structural plan lookup.

For PP queries, prefer the Phase-1 structural index and direct filename/path lookup over hyphenated full-text search.

### Phase 4 — change startup only after parallel validation

For a trial period, compare the task-specific packet against the current full-plan startup process for representative tasks. Only after no material authority gaps are found should Claude's Step 2 become:

```text
load a small, versioned common-context packet
+ retrieve the exact PP/source packet for the selected task
+ use the full Master Plan only for dedicated planning, reconciliation,
  broad cross-track work, ambiguity, or explicit Dave request
```

The original full-plan command remains available for audit and planning sessions.

## Boundaries

- Do not split/delete/rewrite Master Plan history merely to reduce tokens.
- Do not make a generated summary an authority source.
- Do not rely on Recoll as proof of currentness until indexing cadence and stale-index signaling are explicit.
- Do not unilaterally modify Claude's scoped startup contract; it is a downstream consumer of the new retrieval contract.
- Preserve all source/retrieval provenance: source hash, section anchors, generator version, and links.

## Recommendation

Prioritize **Phase 1 + Phase 2** over broad library normalization. They directly relieve the repeated startup burden while establishing a reusable retrieval primitive for every future Plan Vault/library consumer. Recoll wrapper and index cadence are valuable supporting work, but they are not the authority mechanism.

## Alignment with the newly reviewed CodeGraph research source

The source retained in `inbox/tigwa/help me work through this_ _codebase is relatively.md` is a useful, but noisy, Perplexity research capture. Its three-store proposal maps directly onto the Plan's already-recorded Graphify appendix:

```text
source proposal                     Plan's PP-KNOWLEDGE-001 / CodeGraph record
Tree-sitter + FalkorDB graph     →  Graph layer: FalkorDB + Tree-sitter
PostgreSQL + Z3 invariants       →  invariant catalog: PostgreSQL + Z3
DuckDB execution traces          →  trace store: DuckDB
MCP context facade               →  unified MCP layer
```

This is not a new generic architecture to adopt or re-scope. The Master Plan records that Dave chose the full a1131-hosted stack on 2026-07-14, folded PP-CODEGRAPH-001 into PP-KNOWLEDGE-001, and is bringing additional research before the build session. The source's generic recommendation to run FalkorDB in Docker and build the graph first must not override those recorded decisions: packaging, local invariant-store choice, cross-host MCP, repo synchronization, and parse scope remain explicit open questions.

The immediate context-burden work is complementary and lower-risk: a deterministic Master Plan section index and `tgw plan brief` are an exact-source retrieval layer. They should be built before, and remain useful alongside, the later Graphify/MCP infrastructure. The capture overstates one point: Z3 can prove only explicitly formalized properties of an adequate model; it cannot make arbitrary generated Python "correct regardless of plan or prompt." Preserve this source capture as a research lead; it contains vendor/blog citations and pasted search residue, so treat individual claims as leads requiring verification, not settled evidence.

## Questions for Dave review

1. Should the always-loaded common packet contain only settled architecture/gates, or also a short current-state/active-tracks snapshot?
2. Is `tgw plan brief --pp` the preferred interface, or should the first consumer be a file-based packet generated alongside the Master Plan?
3. Should a missing/ambiguous PP hard-stop task work, or merely warn and require a full-plan read?
4. Should Recoll freshness be handled by a scheduled incremental index now, or kept as a separately approved infrastructure decision?

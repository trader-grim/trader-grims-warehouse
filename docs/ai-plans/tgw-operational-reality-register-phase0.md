# TGW Application Capability & Operational-Reality Register — Phase 0 scoping

**Status:** Draft — 2026-07-22, in response to Tigwa's REQUEST (new PP, see below).
**PP ref:** NEW — **PP-OPSREALITY-001** (spans PP-RUNBOOK-001, PP-EDITOR-001, PP-AIOPS-001, PP-CATIONIX-001, PP-POSTGRES-001, PP-SELLERHUB-001; not folded into any single existing PP because its whole point is cutting across them).
**Authority:** Discovery/scoping only. No code, service, config, runbook, plan, or production-data change.

## 1. Register schema and evidence classes

One row per material operator workflow / UI-API-CLI capability / worker / background service / data boundary / runbook procedure:

| Column | Meaning |
|---|---|
| `item_id` | stable slug |
| `purpose` | intended purpose + governing PP/invariant/decision |
| `entry_point` | operator entry point (CLI cmd, UI screen, API route) |
| `code_owner` | actual code/config/service file(s) |
| `doc_procedure` | documented/runbook procedure + last source-backed validation date |
| `impl_state` | implementation state + source/test provenance |
| `live_evidence` | deployed/live evidence, version/freshness, monitoring signal |
| `recovery_evidence` | recovery/rollback/runbook evidence + last drill date, if applicable |
| `capability_state` | `implemented-and-verified` \| `implemented-not-live-verified` \| `documented-but-stale` \| `live-but-undocumented` \| `partial` \| `blocked` \| `superseded` \| `unknown` |
| `risk` / `dependency` / `authority_boundary` / `owner` / `next_review_gate` | same discipline as SHCS |

**Four distinct evidence classes, never collapsed into one "done" marker** (this is the register's whole thesis, per Tigwa's framing): documented policy ≠ implemented behavior ≠ deployed/live verification ≠ exercised incident readiness. A markdown "DONE," a passing unit test, a running systemd unit, and an agent completion claim are each exactly one data point, not proof of the other three.

## 2. Source-of-truth hierarchy + conflict resolution

Order, highest to lowest authority for *what's actually true right now*: (1) live system state observed directly (`systemctl status`, `psql`, actual file contents) — same discipline as `feedback-verify-directly-when-possible`; (2) source code as currently committed; (3) test suite results against current code; (4) runbook/doc text; (5) prior audit reports/plan narrative. **When two levels disagree, the higher-authority one wins and the lower one gets flagged `documented-but-stale` or `live-but-undocumented` — never silently rewritten.** The register itself becomes the record of the conflict (both values kept, with the resolution and date), not a place where the doc quietly gets "corrected" to match code with no trace, matching invariant C11's "a skip/guard is a finding, not a log line" principle.

## 3. Initial inventory method — bounded Phase 0 scope

Phase 0 does **not** crawl indiscriminately or claim broad live verification. Concrete bounded steps:
1. Enumerate from what already exists as an index: `systemctl list-units 'tgw-worker@*'`, `tgw plan status` (75 PP-* items already tracked), `reference/runbooks/INDEX.md`, `reference/TGW-Architecture-Services.md`'s service list. This gives the row skeleton for free — no discovery work, just population.
2. For each row, a single bounded live-evidence probe (one command, read-only) rather than deep-diving — e.g. `systemctl status <unit>` for a worker, one `curl`/`tgw` call for an API route. Depth comes in later passes, not Phase 0.
3. Runbook rows: check `doc_procedure`'s last-validated date against the actual current code path it describes — a stale runbook (like the eBay-ops one PP-RUNBOOK-001 already flags as not-started) becomes a `documented-but-stale` row, not a blocker to finishing Phase 0.
4. Do not re-derive incident history from scratch — pull directly from existing `reports/`, `invariants.md`, and PP write-ups (they already contain the evidence this register formalizes; Phase 0 is structuring, not re-investigating).

## 4. Risk-ranked starting domains (per Tigwa's request, with existing PP/todo links)

1. **Listing/publish and eBay-facing flows** — PP-EDITOR-001 (live defects already named: wrong shipping policy, published-without-price, incomplete photo upload — needs defect→root-cause→packet map, not a feature list).
2. **Item mutation/fence/catalog and the Postgres migration seam** — PP-POSTGRES-001's P0 (todo #1636, not yet dispatched) is itself a register-relevant fact: the fence's audit-trail gap is exactly a `documented-but-stale`-adjacent finding (the design says one thing, `_write_field`'s CLI-only wiring does another).
3. **Order/sold/picklist/fulfillment recovery** — PP-RUNBOOK-001's eBay-ops runbook, explicitly not-started.
4. **Worker/queue/NATS/mailbox delivery and observability** — PP-AIOPS-001; note `tgw_health`'s NATS check was itself a `live-but-undocumented`-class bug until #1639 fixed it this session — a concrete proof-of-concept for why this register would have caught it earlier.
5. **Backup/restore, archive, and operational runbooks** — direct tie-in to PP-POSTGRES-001's P1 backup contract (this session found `tgw-db-backup.service` running but the A5 restore drill unconfirmed-executed — textbook `implemented-not-live-verified` row).
6. **Tigwa/Radar/agent tooling and authority boundaries** — PP-CATIONIX-001, PP-HERMES-EA-001.

## 5. Criteria: executable/rehearsable runbook vs. stale documentation

A runbook is `implemented-and-verified` only if: the procedure has been run for real (not just written) within a defined freshness window, the exact commands still match current code/service names, and a wall-clock time or pass/fail result is recorded (same bar as PP-BACKUP-001's A5 restore drill requirement — this register and that drill are the same kind of evidence, not a coincidence). Anything else is `documented-but-stale` regardless of how recently it was *written*.

## 6. Post-change verification + periodic revalidation cadence

**Silent while healthy, surfaces only meaningful drift** — same design principle as the SessionStart briefing hook and `tgw plan check`. Concretely: no scheduled full re-audit; instead, a row's `next_review_gate` fires revalidation on (a) the governing PP/todo closing, (b) the underlying service/code file changing (git-history-driven, not time-driven, matching `feedback-pp-recovery-is-pull-based`'s pull-based philosophy elsewhere in this project), or (c) an incident touching that row. A calendar-based sweep is explicitly rejected as the default — it either goes stale (nobody re-runs it) or becomes noise (everything re-checked whether or not anything changed).

## 7. Integration with SHCS, Tigwa's review role, and Postgres/read-model work

- **SHCS overlap**: Seller-Hub-facing rows (listing/publish, category/policy management) are shared between this register and `shcs-phase0-audit-scoping.md` — cross-reference by `capability_id`/`item_id` rather than duplicating; SHCS owns the "matches Seller Hub" axis, this register owns the "actually deployed and recoverable" axis. A row can be `full-parity` in SHCS and `documented-but-stale` here simultaneously — that combination is itself a finding, not a contradiction to resolve.
- **Tigwa review/sequencing**: same requirement as SHCS's integration matrix (§2 of that doc) — every row needs a PP/workstream, owner, evidence state, and next review gate so Tigwa can answer "what's next" from the register directly, without re-deriving it from prose.
- **Postgres/read-model**: this register is itself a natural PP-POSTGRES-001 P4 data-product candidate later (a queryable capability inventory beats a markdown table at 100+ rows) — not a prerequisite now, same "design for later import, don't require it" rule as SHCS.

## Open questions for Dave/Tigwa

- Confirm **PP-OPSREALITY-001** as the new PP name/number, or fold under an existing one (PP-COHESION-001 was the closest prior audit-shaped PP — worth checking for overlap before opening a new one).
- Confirm risk order in §4 before Phase 0 population starts.
- Who owns keeping `next_review_gate` actually firing — this needs a mechanical trigger (git hook / plan-check style detector) eventually, not just a column nobody watches; not designed here, flagged same as every other "write it down, then mechanize it" pattern in this project (E11/E12/E14 precedent).

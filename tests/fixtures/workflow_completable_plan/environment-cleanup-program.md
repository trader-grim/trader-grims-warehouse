# TGW environment and agent recovery program

Status: **PROPOSED**
Owner: Dave
Prepared: 2026-08-10
Tracks: [server environment](PLAN-environment-cleanup-servers.md) and [satellite recovery](PLAN-environment-cleanup-satellites.md)
Plan format target: [workflow-completable plan specification](SPEC-workflow-completable-plan.md)

## Decision

TGW will stop deriving current authority from accumulated agent prompts, memories,
host folklore, and operational history. Current operation will be derived from three
versioned inputs:

1. an agent-neutral policy;
2. a generated environment-registry snapshot; and
3. one bounded task or plan contract.

Historical material remains searchable evidence. It is not automatically an
instruction, current fact, permission, or completion receipt.

The program has two independently reversible tracks:

- **Servers:** simplify the authoritative development and production environment on
  `tgw-lib` and `tgw-prod`.
- **Satellite laptops:** forensically recover useful material from quarantined
  `catnanny` and `helicrew`, without restoring their contaminated agent runtime.

The recommended agent outcome is a clean **TGW Steward** runtime. “Hermes” may remain
its user-facing name or conversational style, but old Hermes prompts and memories do
not become its operating contract. Reviewed preferences and sourced knowledge can be
imported individually.

## Confirmed findings and open facts

| Subject | Current finding | Treatment |
|---|---|---|
| Production | `tgw-prod` is the production NixOS host | Canonical server registry entry |
| Development | `tgw-lib` is the development/controller environment | Canonical server registry entry |
| `a1131` | Obsolete host name still appears in documents and memories | Retired alias that fails loudly; history only |
| Application checkout | Multiple paths/worktrees and a coding-worker root can disagree | Replace with registered repo plus per-task workspace |
| Flake checkout | Real maintained checkout is `tgw-prod:/home/db/tgw-flake`, branch `master` | Register explicitly; do not infer from stale agent profile |
| Instructions | `AGENTS.md`, actor contracts, `CLAUDE.md`, packets, memories, and old procedures overlap | Establish precedence and lint contradictions |
| Hermes contract | `PP-HERMES-EA-001` contains obsolete hosts and broad historical authority | Preserve as historical input; replace operational contract |
| Hindsight | Primarily useful operational history, with possible contaminated inference | Quarantined, source-labelled historical index |
| `catnanny`, `helicrew` | Quarantined satellite agent machines; exact access, contents, and integrity are not yet verified | Discovery and evidence-preserving recovery only |

Unknowns stay marked unknown until an inventory receipt establishes them. A machine
name found in memory is not proof that the machine exists or is reachable.

## Authority and safety boundaries

This plan authorizes planning and read-only discovery only. It does not authorize:

- enabling services, changing production, or deploying a release;
- logging into an unverified satellite with recovered credentials;
- executing programs, prompts, hooks, plugins, or shell history recovered from a
  quarantined machine;
- importing Hindsight or Hermes memory directly into a new agent;
- deleting worktrees, memories, databases, snapshots, or historical documents; or
- marking a Plan/Todo item complete merely because a queue job succeeded.

Every destructive cleanup requires a separate approved work unit, an immutable
backup manifest, exact targets, and a tested rollback.

## Program sequence

1. Freeze and inventory authoritative server inputs and satellite evidence.
2. Establish the server-side registry, instruction precedence, and clean workspace
   contract.
3. Build TGW Steward from those clean inputs with historical lookup disabled by
   default.
4. Export satellite information into neutral, hashed evidence packages.
5. Review and selectively import sourced facts, documents, preferences, and open
   issues. Never import executable authority.
6. Run parallel acceptance: server reproducibility, agent boundary tests, and
   satellite recovery completeness.
7. Present a workflow-generated completion candidate for human closure.

The satellite track may inventory and image machines while the server track builds
the clean destination. No recovered material crosses into that destination before
its classification and review gates pass.

## Program acceptance

The program is complete only when all of the following are evidenced:

- one registry identifies every current host, repo, canonical plan root, release
  root, service owner, and retired alias;
- a new task starts from a clean workspace and receives exactly the same resolved
  context when reproduced from its manifest;
- no non-Claude agent receives `CLAUDE.md` as authority;
- no current procedure resolves through `a1131` or an unregistered path;
- TGW Steward answers host/path/authority questions from cited current sources and
  labels historical answers as historical;
- Hindsight and Hermes exports have immutable manifests, provenance, quarantine
  status, and reviewed import decisions;
- `catnanny` and `helicrew` are no longer runtime dependencies;
- no unresolved reconciliation or security gate remains; and
- Dave approves the completion candidate bound to the exact plan version.

## Rollback principle

The migration is additive until acceptance. Old environments remain read-only and
recoverable; new registries, workspaces, and agents use new names and paths. Rollback
changes consumers back to the prior immutable configuration while retaining all
receipts and snapshots. Cleanup happens only after an independently approved
retirement window.

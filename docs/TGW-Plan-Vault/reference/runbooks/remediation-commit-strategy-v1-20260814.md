# TGW remediation commit strategy — v1 (2026-08-14)

**Owner:** shared; operator authority: Dave

**Applies to:** production defects, operationally discovered code bugs, bounded
follow-up patches, and documentation/configuration corrections in the TGW
application repository

**Canonical repository:** `/opt/TGW/tgw-lib/src/trader-grims-warehouse`

**Plan authority:** `/opt/TGW/library/plans`

## Purpose

This runbook defines how a discovered defect becomes a traceable source correction
without losing other work, confusing source with deployment, or repeatedly asking
for approval already granted by the governing Plan.

The core rule is:

```text
one observed defect
  -> one attributed clean worktree
  -> one coherent source-and-regression commit
  -> review of that exact commit and tree
  -> fast-forward integration of that exact reviewed identity
  -> build and install from that exact identity
  -> live acceptance
  -> production ref advanced to the accepted installed commit
```

If review or live testing finds another defect, create a successor commit. Never
amend, rewrite, or silently patch the already reviewed candidate.

## Source lineage roles

The repository uses refs by meaning, not as a substitute for evidence:

| Ref | Meaning | Movement rule |
|---|---|---|
| `main` | Latest admitted source lineage, including admitted documentation-only successors | Fast-forward only after the exact candidate passes its required source gates and review |
| `production` | Exact application commit last verified as installed and accepted on `tgw-prod` | Move only after installation and live acceptance; a build or selected release alone is insufficient |
| `remediation/<subject>-<date>` | A bounded source correction in an isolated worktree | Created from an exact admitted parent; retained until integration and preservation are verified |
| `candidate/<identity>` | Optional immutable review/install candidate | Never moved; use a new candidate for any changed bytes |
| `recovery/*` | Stranded or recovered work awaiting semantic reconciliation | Never delete merely because another branch contains its commits; inspect dirty/untracked state first |
| `integrate/*` | Historical or active integration line | Retain until all consumers use `main`, preservation exists, and a zero-stale-reference scan passes |

Branch names are navigation aids. Commit, tree, artifact, review, release, and live
receipt identities remain the evidence.

At the initial 2026-08-14 reconciliation, `production`, `main`, and
`integrate/full-plan-fb9` pointed to application commit
`8f59aff3d1dd11a1fb9ec936fd57d048baa08aea`, tree
`43971be75c1d84d62fea9c00943527bf4637b9fa`. The two Codex recovery heads were
imported into the canonical repository and proved ancestors of that commit. Their
worktrees were retained because one still contained attributed uncommitted work.
No historical worktree or branch was deleted.

## Authority and interruption rule

An approved exact Plan and complete solution authorize the implementation, tests,
review, integration, immutable candidate installation, and verification phases that
the solution declares. Do not stop after each phase to ask for the same approval.

Stop and seek new operator direction only when the next action would materially
broaden the declared scope, such as:

- a new provider/business effect;
- destructive cleanup or data deletion;
- credential issuance or privilege expansion not already declared;
- a different host, repository, item set, or release target;
- a product choice whose alternatives would produce materially different behavior.

Source integration authority does not authorize an eBay listing, order mutation,
repository cleanup on another host, Nix switch, or production install unless that
effect is separately in the solved scope.

## Classify the work before creating a commit

### Source remediation

Create a source commit when tracked application, test, schema, Nix, configuration,
or runbook bytes must change. The regression belongs in the same coherent commit as
the fix unless the governing contract deliberately separates them.

### Provider or durable-data reconciliation

Do not create a fake source commit when no source bytes changed. Provider calls,
sold-state reconciliation, listing withdrawal, queue repair, or one-off durable-data
correction produce domain receipts with exact scope and before/after facts. A later
source commit may cite that receipt, but the receipt is not source history.

### Diagnosis only

Read-only inspection creates no source commit. Record exact commands, source/runtime
identity, relevant output hashes, and the conclusion in the task evidence when the
diagnosis will govern later work.

### Mixed discovery

If an operational action exposes a code bug, stop broad operational retries, preserve
the failure evidence, fix the source through this workflow, then resume only the
separately authorized operational action. Do not hide the code fix inside an ad-hoc
production edit.

## Procedure

### 1. Bind Plan, repository, installed source, and defect

Verify the approved standalone Plan with the shared `tgw-plan` skill. Capture:

```bash
REPO=/opt/TGW/tgw-lib/src/trader-grims-warehouse
git -c safe.directory="$REPO" -C "$REPO" rev-parse \
  production^{commit} production^{tree} main^{commit} main^{tree}
git -c safe.directory="$REPO" -C "$REPO" worktree list --porcelain
git -c safe.directory="$REPO" -C "$REPO" status --short --branch
```

The canonical repository is group-owned and individual harness accounts may not own
its top directory. Use the command-scoped exact `safe.directory` value shown above;
do not add a global wildcard exception.

Also capture the selected production release, executing wheel/module identity, and
the smallest production-shaped reproduction. Do not assume `main == production`.

For an urgent production defect, normally branch from `production`. Branch from
`main` only when the remediation requires an already admitted dependency there and
the wider delta is deliberately included in review and release.

### 2. Reconcile existing work before creating new work

Inspect all registered and orphaned worktrees, local and remote refs, reflogs,
unreachable objects, bundles, release generations, service bindings, and receipts
relevant to the affected capability.

Classify existing material as one of:

- already admitted and reusable;
- partial work to retain or extend;
- conflicting work requiring a semantic choice;
- superseded but not yet safe to retire;
- unrelated work to preserve untouched.

Never use `git reset --hard`, broad checkout, `git clean`, or branch deletion as a
shortcut around attribution. A dirty worktree is evidence of unresolved work, not
cleanup permission.

### 3. Create a dedicated clean worktree

Use the canonical repository and exact parent:

```bash
REPO=/opt/TGW/tgw-lib/src/trader-grims-warehouse
WORKTREE=/opt/TGW/w/remediation-<subject>-<date>
PARENT=production

git -C "$REPO" worktree add -b remediation/<subject>-<date> \
  "$WORKTREE" "$PARENT"
git -C "$WORKTREE" status --short --branch
git -C "$WORKTREE" rev-parse HEAD^{commit} HEAD^{tree}
```

Do not reuse a dirty actor worktree. Do not make application commits in the
production flake checkout. Use task-owned scratch with mode `0700`, preferably below
`/opt/TGW/w`, when shared `/tmp` is full or its ancestry violates trusted-path tests.

### 4. Implement one coherent correction

The commit should contain only:

- the minimal production fix;
- a regression that fails against the parent and passes against the fix;
- directly required schema/config/documentation changes;
- no unrelated formatting, cleanup, generated residue, or provider output.

Use production-shaped fixtures at every database, JSON, HTTP, queue, filesystem, and
process boundary. Verify the user-visible outcome and the safe negative/control path.
For retry or ambiguity bugs, prove both the intended retry and refusal of unsafe
duplicates.

### 5. Test before closing the commit

Run, in order:

1. exact regression node;
2. affected subsystem tests;
3. static checks for changed files;
4. full tracked suite when the risk or release policy requires it.

Bind tests to the candidate source explicitly. An existing venv may otherwise import
another checkout. Record exact pass/fail/skip counts and any superseded environmental
attempts. An ENOSPC or permission failure is not a passing source gate and is not
erased; correct the environment and rerun.

### 6. Close the candidate once

```bash
git status --short
git diff --check
git add -- <exact-files>
git diff --cached --check
git diff --cached
git commit -m 'fix(<area>): <bounded outcome>'
git rev-parse HEAD^{commit} HEAD^{tree}
git status --short
```

Do not use `git add -A` or `git add .` around unrelated work. Once review begins, the
commit is immutable. A review blocker is fixed by a child commit and the resulting
successor identity is reviewed again. Do not amend or force-update a reviewed ref.

### 7. Review the exact candidate

Review binds the complete commit and tree, governing contract, test evidence, and
runtime/effect boundaries. Review independence is an evidence/context property, not
a permanent human-only rule. Production policy may require human release approval;
non-production profiles may select another admitted independent reviewer.

The reviewer checks at minimum:

- the production reproduction is represented accurately;
- the fix closes all relevant call paths;
- authority, ambiguity, replay, cleanup, and rollback remain closed;
- tests prove the serialized/live shape rather than a simplified stand-in;
- no unrelated provider or data effect is smuggled into source authority.

### 8. Integrate without changing reviewed bytes

Resolve parallel work before review when possible. Rebase or cherry-pick before
review is allowed, but the new SHA is the candidate and must receive the gates and
review. After review, integrate by fast-forwarding the exact reviewed commit:

```bash
REPO=/opt/TGW/tgw-lib/src/trader-grims-warehouse
OLD_MAIN=$(git -C "$REPO" rev-parse main^{commit})
NEW=<reviewed-40-hex-commit>
git -C "$REPO" merge-base --is-ancestor "$OLD_MAIN" "$NEW"
git -C "$REPO" update-ref refs/heads/main "$NEW" "$OLD_MAIN"
```

If `main` moved in parallel, do not force it. Reconcile on a successor commit, rerun
the affected gates, and review the new identity.

### 9. Preserve and publish source lineage

Before retiring any source ref or worktree:

- push `main`, `production`, and required preservation refs to the protected remote;
- read them back independently;
- retain exact release/source bundles where policy requires them;
- scan service, MCP, launcher, docs, worktree, and release references;
- obtain operator acceptance for retirement.

Remote failure is a hold on retirement, not permission to delete local refs. A local
bundle or reflog is useful preservation but is not an off-host remote readback.

### 10. Build, install, and accept the exact candidate

Follow the current governed coding and installation runbooks. Build the archive and
wheel from the reviewed commit, verify embedded commit/tree and hashes, select the
matching immutable source generation, install the matching wheel, restart only the
affected services, and perform the user-visible acceptance test.

Do not move `production` at build time or release-selection time. Move it only after
the exact installed runtime passes acceptance:

```bash
REPO=/opt/TGW/tgw-lib/src/trader-grims-warehouse
OLD_PRODUCTION=$(git -C "$REPO" rev-parse production^{commit})
ACCEPTED=<accepted-40-hex-commit>
git -C "$REPO" update-ref refs/heads/production \
  "$ACCEPTED" "$OLD_PRODUCTION"
```

The deployment receipt must bind the old and new production identities. A rollback
likewise moves `production` only after the rollback is active and verified; it does
not rewrite or erase the failed candidate.

## Parallel remediation policy

Parallel patches are safe only when each has an exact parent and isolated worktree.
Before integration:

1. compare changed paths and semantic contracts;
2. select an order based on dependency, not completion time;
3. replay the later patch on the newly admitted parent;
4. rerun affected and interaction tests;
5. review the resulting new commit/tree;
6. fast-forward `main` once.

Do not stack multiple agents in one dirty worktree. Do not declare two independently
reviewed commits jointly reviewed after a merge that nobody reviewed.

## Commit boundaries by effect

| Work performed | Source commit? | Required durable output |
|---|---:|---|
| Code plus regression | Yes | commit/tree, tests, review, release/live receipts if deployed |
| Runbook or source policy only | Yes | commit/tree and link/static checks; `production` need not move |
| Nix module/config source change | Yes | source commit plus Nix build/activation receipts when applied |
| eBay/API read-only diagnosis | No | observation evidence as required |
| Authorized provider mutation | No, unless source also changed | provider request/result/reconciliation receipt |
| Database/data reconciliation | No, unless schema/code also changed | bounded before/after and ambiguity receipt |
| Temporary local test/build artifact | No | remove after verification or preserve if it is admitted evidence |

## Access and role separation

The intended operating model is:

- `tgw-coders`: source worktrees, commits, tests, review artifacts, and canonical Git
  ref updates under the governed workflow;
- a separate least-privilege release-installer role: registered application/Nix
  installation effects, without general provider/business authority;
- reviewer eligibility selected by environment/profile policy, with independence
  enforced by identity/context/evidence rather than a hard-coded human-only rule;
- operator approval required where the active production policy says so.

Do not make source development artificially difficult to obtain deployment safety.
Use group-owned canonical Git/worktree roots for coding and a narrower separate
installer capability for production transitions. Neither role implies eBay or other
business-provider authority.

## Current reconciliation and remaining hold

The 2026-08-14 cleanup imported the clean `integrate/full-plan-fb9` lineage and both
Codex recovery heads into the registered canonical repository, then created
`main` and `production` at the admitted/deployed `8f59aff3...` identity. Before those
five refs were added, the canonical repository had 53 registered worktrees and 43
local branches; those were inventoried and retained.

The configured GitHub `origin` is the desired durable shared remote, but readback from
the Codex account failed during this cleanup because SSH host/authentication material
was unavailable. Therefore:

- local canonical refs are established;
- no GitHub push is claimed;
- no old branch/worktree is retired;
- remote push plus independent readback remains required before retirement cleanup.

This is a preservation hold, not a blocker on making correctly reviewed successor
commits in the canonical repository.

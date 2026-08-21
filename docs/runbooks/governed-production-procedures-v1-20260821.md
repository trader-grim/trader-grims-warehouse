# Governed production procedures — v1 (2026-08-21)

## Boundary

`config/environment/procedures.json` is a non-executing allowlist.
`tgw-procedure-runner` is the W16 effect boundary installed from the
independent flake, not from the application release it selects. It accepts no
shell text, environment override, executable, working directory, host, or
run-as identity from a request.

Every request binds the exact Plan commit, solution hash, execution card,
registry revision, procedure parameters, candidate/closure state and hashes of
all named precondition evidence. Dave's separate deployment key signs that
exact request for at most 15 minutes. The private key is never installed on an
actor account or production host; only its 32-byte public key is installed at
`/etc/tgw/trust/deployment-approval.pub`.

## Normal path

1. Resolve one execution card through the canonical MCP controller.
2. Materialize all evidence under the card's durable attempt root on
   `tgw-lib`; never use `/tmp`.
3. The controller compiles the exact procedure request and presents its hash,
   procedure, candidate, predecessor, successor and rollback position.
4. Dave approves that exact request through the human approval surface. The
   resulting signed approval cannot authorize another request or survive more
   than 15 minutes.
5. The registered MCP provider transfers only the bound request/evidence and
   invokes the root-owned runner. A conversational shell command is not a
   substitute.
6. Inspect the durable receipt in `/opt/TGW/procedure-receipts`. A prepared or
   ambiguous receipt is a hold requiring reconciliation; it is not safe to
   repeat under a new request id without observing live state.

For `nixos-prod-switch/v1`, the runner independently verifies the clean
`/home/db/tgw-flake` commit/tree and current system closure, performs
`dry-activate`, performs the fixed switch command, then verifies the expected
new closure. For application install/rollback, it verifies the current
generation before and after the independent release installer runs.

## Initial bootstrap and manual recovery

The first switch that installs this runner is the one-use W17 bootstrap case.
It must use the exact reviewed flake commit/tree and prebuilt closure, record
the old and rollback closures first, and write an emergency journal for later
ingestion. After the runner and trust public key are verified, the seed is
revoked. Automated actors cannot use this exception.

If the MCP/controller boundary is unavailable, Dave retains the human-only
`db` recovery account described by the canonical Plan runbook
`reference/runbooks/manual-platform-recovery.md`. It may restore the last
verified controller/runner generation and quarantine actors, but it cannot
approve marketplace or business-data effects.

## Refusals

Missing, forged, stale, mismatched or replayed approvals; extra parameters;
changed evidence; dirty/wrong flake state; wrong predecessor; and unexpected
post-state are refused or left visibly ambiguous. Never delete a refusal or
prepared receipt to make a retry possible.

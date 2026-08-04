# Tigwa SSH credential scoping proposal

**Date:** 2026-07-18
**Owner:** Tigwa
**Review process:** PP-HR-001 job-contract review
**Linked tracker:** #1459
**Status:** proposal only — no account, SSH key, sudoers, flake, service, or production change is authorized by this document.

## Decision requested

Approve a staged replacement of the unrestricted `tigwa@a1131` → `db@tgw-prod` automation path with a dedicated noninteractive, `command=`-restricted SSH credential and a small, server-side, versioned read-only operation dispatcher.

The desired invariant is not merely “Tigwa should not run power commands.” It is: **a Tigwa-owned credential cannot obtain a remote shell, arbitrary argv execution, arbitrary stdin/script execution, sudo, or a workload/power lifecycle operation on tgw-prod.**

## Why this is necessary

The reviewed contract says Tigwa’s thermal authority is “notify/interrupt only,” explicitly excluding pause, kill, shutdown, or host-power actions. Live evidence shows the current a1131 key is restricted by source IP and forwarding flags, but has no forced command; it logs in as `db`. `db` has `(ALL : ALL) SETENV: NOPASSWD: ALL` on tgw-prod. The present credential therefore permits the exact class of action prohibited by the contract.

This is an E11-style mismatch between a written boundary and a mechanical one. It should be closed with a capability boundary, not a stronger prompt or another reminder.

## Current automation surface — verified

| Caller | Current remote mechanism | Required result | Problem |
|---|---|---|---|
| Plan Vault watcher | `ssh db@… bash -s`, followed by the broad `tgw-prod` wrapper | Top-level assigned-inbox metadata and Tigwa queue summary | Arbitrary stdin shell script; arbitrary `tgw` argv tunnel |
| Thermal sentinel | `ssh db@… bash -lc <constructed script>` | Watchdog/status/snapshot evidence | Arbitrary remote shell script |
| Reachability watcher | `ssh db@… 'sudo smartctl -a /dev/nvme0n1'` | NVMe temperature reading | Root-equivalent login used for one bounded hardware read |
| `~/.local/bin/tgw-prod` | Arbitrary argv encoded into remote `fish -c 'tgw $argv'` | Currently used for tracker actions/reads | Remote `tgw` function runs most commands as `tgw`; no operation-level boundary |

The existing MCP service has separately verified read-only registration and is not part of this change.

## Recommended architecture

1. **New dedicated identity, pending audit:** provision a separate noninteractive service identity (working name `tigwa-observe`) rather than reuse `db` or silently repurpose the existing tgw-prod `tigwa` account. The existing `tigwa` account has a nologin shell but must first be audited for file/process/ACL assumptions before any reuse decision.
2. **New key, separate from Dave’s interactive `db` key:** place only the new public key under the new identity. It gets `from=`, `no-pty`, `no-port-forwarding`, `no-agent-forwarding`, `no-X11-forwarding`, `no-user-rc`, and an OpenSSH `command=` target.
3. **Root-owned declarative dispatcher:** a small Nix-declared executable consumes `SSH_ORIGINAL_COMMAND`, accepts only exact literal operation names, rejects empty/unknown/argument-bearing commands, uses fixed absolute-path commands, clears sensitive environment, and returns a stable machine-readable schema. It never evals, invokes a shell with caller input, reads stdin as code, or forwards arbitrary argv.
4. **Capability review before every added operation:** no generic `tgw`, shell, file path, task ID, or command argument capability is added merely for convenience. A requested mutating operation (including a tracker note) needs its own contract decision and review; it is not in the first read-only cutover.

A forced command is an application boundary, so it must be deliberately small, tested with hostile/invalid inputs, root-owned, and deployed declaratively. The separate low-privilege Unix identity keeps a dispatcher defect from inheriting `db`’s sudo authority.

## First-cut registry

| Exact operation | Output / scope | Privilege class | First-cut decision |
|---|---|---|---|
| `plan-vault-metadata-v1` | Only the existing watcher schema: top-level names, mtimes, sizes for `inbox/tigwa`, `inbox/dave` when present, and root-level files; no contents | ordinary read | Include |
| `thermal-evidence-v1` | Existing sentinel schema: watchdog active state, `thermal.status`, bounded snapshot identifiers, bounded snapshot-log result | ordinary read if file ACLs allow it; otherwise redesign source ownership, not broad sudo | Include only after exact permissions test |
| `nvme-temperature-v1` | A normalized temperature record for the named approved NVMe device, not arbitrary SMART output | narrowly privileged, if `smartctl` remains unavailable unprivileged | Include only with an exact wrapper/sudo rule reviewed by flake owner |
| `todo-tigwa-summary-v1` | Read-only assigned-work summary with no caller-supplied argv | requires a separate analysis of the CLI/database execution path | Defer until tested; do not tunnel general `tgw` |
| Any `tgw` write, arbitrary task operation, clipboard operation, shell, or file read | broader than required observation | not first-cut | Exclude |

`clip` is explicitly excluded: it is a Dave-interactive convenience, not an automation observation capability.

## Alternatives considered

### A. Force-command the current `db` key

Rejected. Even a good dispatcher would run as a root-equivalent account. A dispatcher parsing defect would retain the privilege the contract forbids, and a restricted key would also conflict with Dave’s interactive use of `db` unless a second key were maintained anyway.

### B. Reuse tgw-prod’s existing `tigwa` account

Potentially acceptable after an audit of its current use, groups, writable paths, and service assumptions. It reduces account count but merges an SSH-exposed automation boundary into an existing role. A new identity is clearer and safer by default.

### C. Keep current SSH access and add an alarm/detector only

Rejected as the primary control. Detection cannot stop the forbidden action. A detector remains valuable after scoping to catch drift, but it does not make present unrestricted authority acceptable.

## Migration and rollback

### Stage 0 — design and tests, no cutover

- Flake owner implements the identity, dispatcher, authorized-key restriction, and only the approved first-cut operations in a review branch.
- Add offline tests for exact accepted commands, empty command, whitespace variants, extra arguments, shell metacharacters, stdin, environment injection attempts, and every denied operation.
- Verify dispatcher identity/group/permissions and prove it cannot run `sudo`, obtain a PTY, execute a shell, or access `db`’s home.

### Stage 1 — shadow-read one observation operation

- Port `thermal-evidence-v1` or `plan-vault-metadata-v1` to the restricted key while retaining the existing observer only as a comparison source.
- Compare normalized output for several successful scheduled checks. A mismatch blocks promotion; it does not trigger a fallback to arbitrary shell execution.
- Existing production mitigation remains untouched.

### Stage 2 — narrow callers

- Replace the watcher’s `bash -s` and sentinel’s `bash -lc` calls with literal operation names.
- Refactor the local `tgw-prod` wrapper out of unattended Tigwa automation. Do not remove or alter Dave’s interactive tooling in this task.
- Port NVMe reading only after the exact least-privilege method has passed a denial test for altered device/argument forms.

### Stage 3 — revoke the old automation path

- Verify every a1131 automation call site has moved and no longer targets `db@192.168.60.100`.
- Remove the Tigwa automation public key from `db` only after an explicit Dave/flake-owner recovery plan is tested.
- Preserve a documented, human-operated break-glass route. Break-glass is not exposed through the Tigwa credential.

**Rollback:** before old-key removal, restore an individual caller to its last known-good implementation only on a declared, time-bounded emergency basis and record it as a boundary regression. After old-key removal, recovery requires a human operator using the documented break-glass route; do not silently regrant unrestricted automation access.

## Mechanical invariant and checks

**Invariant `E11-TIGWA-REMOTE-CAPABILITY`:** No credential used by Tigwa automation can execute an arbitrary remote command or inherit `db`/root authority on tgw-prod. Its allowed remote command set is exactly the reviewed dispatcher registry, each operation is observation-only unless separately approved, and no operation can control workload or power state.

At deployment and periodically thereafter, deterministically verify:

1. The automation key rejects empty, unknown, and argument-bearing operations with nonzero status.
2. It cannot allocate a PTY, forward a port/agent, invoke a shell, or run `sudo -l`.
3. Dispatcher executable owner/mode/hash and Nix generation match the approved deployment record.
4. The dispatcher registry exactly matches the reviewed first-cut list.
5. `db`’s authorized keys no longer contain the Tigwa automation key after Stage 3.
6. Each allowed operation returns only its documented schema and does not change a timestamp/content outside its own audit log, if one is added.

A detector alarm means **stop automated remote observation and notify Dave**; it never performs corrective production action itself.

## Review gates

**Dave:** choose new identity versus audited reuse; confirm whether first cut must remain entirely read-only; approve the human break-glass owner; decide whether a separate, narrowly reviewed tracker-write capability is ever needed.

**Flake owner / Claude:** review the Nix account and forced-command deployment design; confirm the dispatcher cannot inherit `db`/root privilege; review exact sudo necessity for NVMe temperature; review test coverage and recovery plan. No build begins until this review accepts the contract.

## Evidence and provenance

- `inbox/tigwa/CLAUDE-REQUEST-credential-scoping-2026-07-16.md` — SHA-256 `121070d93da30944bef0b4a68f4628a22cfccd57f84b3aad36d1969d0812d8f1`
- `inbox/tigwa/CLAUDE-REVIEW-tigwa-contract-cross-verification-2026-07-16.md` — SHA-256 `d3182a4eebe0e30b104fda9254d2dcc1def1130255cd09922e6e2607d0d93904`
- tgw-prod `~/.ssh/authorized_keys` (metadata/key-option inspection; no key material reproduced) — SHA-256 `cac8f784abb45514a4af6b5143aac1d677effe6a110474274d028dbd6fad4d23`
- tgw-prod `~/.config/fish/functions/tgw.fish` — SHA-256 `0779f1894abcfda3c193b8486595036fffbb7377534434f0b9ceb11994513c4a`
- a1131 `~/.local/bin/tgw-prod` — SHA-256 `61ee80d30dc3ab069059c96bac04c95c945e8c103f03dd633d45d084d312802e`
- a1131 Plan Vault watcher — SHA-256 `351b9bc2d130dc1f08e2e3473e418a18f957b2a63f512cef5323cf6c4882fe6c`
- a1131 thermal sentinel — SHA-256 `7a3683d2a2e71ead56696f5186537311c357e1838979db2914978039a21c3d52`
- a1131 reachability watcher — SHA-256 `dd6f69750fdd96db5f3da755489910e8024e1660f555db359ced31c14cb3a135`

## Drafting note

DeepSeek V4 Flash was used for bounded initial option-generation. Its output was not accepted as authority: it incorrectly attributed extra current watcher behavior and used an incorrect watchdog name. This proposal was rewritten against the inspected scripts and live evidence above.

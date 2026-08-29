# TGW recovery spine runbook

**Owner:** shared platform
**Last verified:** 2026-08-29 against the live Debian `tgw-lib` host
**Applies to:** `tgw-lib` (development host); the spine is never installed on `tgw-prod`
**Entry point:** `/usr/local/sbin/tgw-root-effect` (root:root, pinned)
**Companion:** the `tgw-recovery` DSH skill (`~/.dsh/skills/tgw-recovery/SKILL.md`)

This runbook explains how to recover the TGW development/control plane when
forward work breaks after a long period of normal operation, using only the
recovery spine and no conversation history. It is procedural guidance, not Plan
or operator authority.

## What the spine is

`tgw-root-effect` is the single host-privileged, recovery-enabled boundary. It
is the realization of what "Doctor" was originally meant to be: a bounded,
self-contained system-recovery ability, not the narrower diagnosis/named-repair
tool "Doctor" became. The spine is deliberately small and never imports from the
coding runtime it repairs, so it works when the runtime itself is broken.

Two layers define the system:

1. **Host privileged spine** — root-owned, recovery-enabled, never dissolved
   while it is needed (and a recovery form persists permanently). One
   privileged entry point; root:root config; non-writable by `tgw-coders`.
2. **Role as a claim in the execution card**, resolved at dispatch. No
   Unix-groups-as-roles. Container coders present a role-scoped card; the spine
   grants a per-job bounded surface.

## Non-negotiable boundaries

- The spine is the **only** privileged entry point. Do not add per-operation
  sudoers, per-actor roles, or shell access around it.
- Every operand is validated against an exact whitelist or regex: commit
  identities (40 hex), service units (whitelist), repairs (bootstrap `--repair`
  choices), container ids (safe pattern). No arbitrary paths, environment
  variables, or commands are accepted.
- Every operation is idempotent and writes a `tgw-root-effect-receipt/v1`
  receipt under the configured receipt root.
- The spine runs only on `tgw-lib`. `tgw-prod` is an independent operational
  projection with no coding workers and no spine.
- The spine does not authorize, approve, or dispatch ordinary work. It performs
  declared recovery operations and records evidence.

## Declared operations

| Operation | Args | Effect |
| --- | --- | --- |
| `recovery-status` | — | Read-only self-check: canonical HEAD vs runtime `current`, database `select 1`, whitelisted unit states. |
| `runtime-install` | `--commit <40hex>` | Materialize the exact canonical commit (delegates to the pinned bootstrap). |
| `service-restart` | `<unit>` | Restart a whitelisted tgw unit. |
| `database-repair` | `<repair>` | Run a bounded bootstrap `--repair` (whitelist only). |
| `container-lifecycle` | `<start\|stop\|rm> <id>` | Declared container op; fails closed until a container runtime is configured. |
| `restore-from-receipt` | `<receipt>` | Reinstall the exact runtime commit recorded by a prior materialization receipt. |

## Install (root, once)

The source is versioned at
`/opt/TGW/tgw-lib/src/trader-grims-warehouse/src/tgw/root_effect.py`. Install a
pinned, root-owned copy and a root:root config, then verify:

```bash
sudo install -m 0555 -o root -g root \
  /opt/TGW/tgw-lib/src/trader-grims-warehouse/src/tgw/root_effect.py \
  /usr/local/sbin/tgw-root-effect
sudo install -m 0444 -o root -g root /path/to/tgw-root-effect.json \
  /opt/TGW/tgw-lib/config/tgw-root-effect.json
sudo /usr/local/sbin/tgw-root-effect recovery-status
```

The config (`tgw-root-effect-config/v1`) names the receipt root, the pinned
bootstrap, the canonical repo, the runtime root, the PostgreSQL DSN, the
whitelisted units, and the (optional) container runtime.

## Recovery procedures

### 0. Always start with a read-only self-check

```bash
sudo /usr/local/sbin/tgw-root-effect recovery-status
```

Read the structured result. `PASS` means the spine sees no degraded surface;
`DEGRADED` names exactly what is wrong (canonical/runtime drift, database, or a
unit). Drive each repair from the named surface, not from guesswork.

### 1. Runtime is broken or drifted from canonical

```bash
sudo /usr/local/sbin/tgw-root-effect runtime-install --commit "$(git -C /opt/TGW/tgw-lib/src/trader-grims-warehouse rev-parse HEAD)"
```

### 2. Restore the exact runtime from a prior receipt

```bash
sudo /usr/local/sbin/tgw-root-effect restore-from-receipt /opt/TGW/tgw-lib/doctor-receipts/<materialization-receipt>.json
```

### 3. A service is stuck

```bash
sudo /usr/local/sbin/tgw-root-effect service-restart tgw-codex-implement-worker.service
```

Only whitelisted units are accepted.

### 4. Database repair

```bash
sudo /usr/local/sbin/tgw-root-effect database-repair database
```

The repair name must be one of the bootstrap `--repair` choices.

### 5. Container lifecycle (once the container direction lands)

```bash
sudo /usr/local/sbin/tgw-root-effect container-lifecycle start <id>
```

Fails closed with an explicit receipt while no container runtime is configured.

## Cold-start drill (prove "pull it out after months")

Run this on a schedule or after every spine change. It proves the spine still
works after long inactivity, without conversation history:

1. From a cold shell, run `recovery-status` and confirm `PASS`.
2. Induce a benign degradation (point `coding-runtime/current` at a prior
   release, or stop a whitelisted unit).
3. Run `recovery-status` and confirm `DEGRADED` names the right surface.
4. Repair with the matching operation (`runtime-install` or `service-restart`).
5. Run `recovery-status` and confirm `PASS` again.
6. Record the drill receipts; they are the evidence that recovery is not
   rot-prone.

## Refusal semantics

`tgw-root-effect` refuses (exit non-zero, no effect, no receipt) when an operand
is unknown, invalid, unsafe, or non-whitelisted. It never broadens scope, never
invents a repair, and never accepts an arbitrary path or shell fragment. A
refusal is a correct fail-closed outcome, not a defect to work around.

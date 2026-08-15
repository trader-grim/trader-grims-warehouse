# TGW full platform installation and rollback — v2 (2026-08-14)

**Owner:** shared; final operator authority: Dave

**Last verified:** 2026-08-14 against `tgw-prod`

**Applies to:** NixOS `tgw-prod`, standalone Plan authority, governed workflow,
immutable application releases, the production Python runtime, HTTP/MCP, and the
18-worker fleet

**Last drill:** 2026-08-14 full source/runtime installation followed by exact
release, service, journal, and authenticated item-page verification

## Purpose

This is the current installation procedure for the whole TGW platform. It is not a
copy of the 2026-06-23 bare-metal cutover runbook. It records the layers which now
exist and the evidence required to move each one safely.

An installation is complete only when all changed layers are verified and the
operator-facing function works. A Git commit, Nix build, selected source generation,
installed wheel, active unit, or HTTP 200 is necessary evidence for its own layer;
none alone proves the system is usable.

## The five installed layers

| Layer | Canonical location | What changes it | What proves it |
|---|---|---|---|
| Plan intent and solution | `/opt/TGW/library/plans` | reviewed Plan commit and solver | Plan-root verifier plus exact solution/closure hashes |
| NixOS host and service definitions | `/home/db/tgw-flake`, branch `master` | registered `nixos-prod-switch/v1` | flake commit, build/dry activation, live closure, unit contracts |
| Immutable application source | `/opt/TGW/releases/<generation>`, selected by `/opt/TGW/current` | registered `app-release-install/v1` | completed receipt and `tgw-release-install verify` |
| Executing Python package | `/opt/TGW/.venvironments/tgw` | exact reviewed wheel installation | installed module hashes and restarted service processes |
| Durable application state | ItemData, PostgreSQL, config, secrets, evidence stores | application/schema/provider procedures | domain-specific receipts and live acceptance checks |

The services execute entry points from `/opt/TGW/.venvironments/tgw`. They do not
execute Python directly from `/opt/TGW/current`. Selecting a release without
installing the matching wheel leaves the old application running. Installing a wheel
without selecting its immutable source leaves the runtime without its release
identity. Both must match before declaring a source deployment complete.

## Last verified installed state

Record fresh values rather than assuming these remain current:

```text
Approved Plan proposal: f0a8cf22b2c7b2f064292a048ffcb8ee98919e99
Platform solution Plan: fb9fee3e9db756ad0f5071525e943794bf1dab9b
Platform solution: sha256:d28650c26c6a3d26d6c943597ccb7abd7c6670b1703d9ce941ac5ed7a2d73a4d
Platform closure: sha256:bc0c53b2574fc359c629bd213e078fdd2824e5e1c4a98c0c7a347de869d9e6f8

Application commit: afb8e703745492ea41c3d1c66ec0662389ff3360
Application tree: 138a6bc1608b7bb30eaddd706068f110780da6d2
Application archive: sha256:251c0828125785220575766a5ff1f6e2307904797500e5d406805a11f1db091d
Selected generation: plan-fb9-afb8e703-20260814
Selection receipt: /opt/TGW/receipts/install-fb9-afb8e703-20260814-1.json
Installed HTTP module: sha256:53c211010749fa9b78081228e1c9f216e2b87735340b86617e477972f2f3e630

Production flake commit: 69044619318b7f3c2ccb66e6e6ba09dcba5c93e5
Live system: /nix/store/8nljzfsn2cyf8wj27nflv5237wpnpgag-nixos-system-tgw-prod-25.05.20260102.ac62194
Booted system: /nix/store/gf2nrw130ihwzprjrdkmb72d5lb3mvnh-nixos-system-tgw-prod-25.05.20260102.ac62194
```

The different live/booted paths mean the current switched generation has not yet
been proved by a reboot. Do not silently label that proof complete.

## Authority boundary

The exact approved Plan and complete solution authorize their declared authoring,
implementation, testing, review, integration, candidate installation, and bootstrap
deployment phases. Do not ask for a new approval at every phase.

Stop for operator direction only when the intended action introduces a material
effect not already declared, including an unrelated host/data/provider change,
destructive cleanup, broadened eBay effect, new credential issuance, or a different
target. Installation authority never implies permission to publish an item on eBay.

The active procedure registry is `config/environment/procedures.json`. Production
mutations use the registered runner and exact revision. Do not replace it with a
free-form root shell because the documented command is visible.

## Phase 0 — bind Plan, source, and target

Verify the standalone Plan root before changing source or production:

```bash
PLAN_ROOT=/opt/TGW/library/plans
APPROVED_PLAN=f0a8cf22b2c7b2f064292a048ffcb8ee98919e99
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=safe.directory \
GIT_CONFIG_VALUE_0="$PLAN_ROOT" \
python3 /home/codex/.codex/skills/tgw-plan/scripts/verify_plan_root.py \
  "$PLAN_ROOT" "$APPROVED_PLAN"
```

Record the platform solution file, `solution_hash`, `closure_hash`, complete status,
and conformance result. A later evidence commit in the Plan repository does not move
the approved Plan ref.

Record the intended application commit/tree and flake commit/tree independently.
Never build from an unrecorded dirty tree or from an embedded Plan copy.

## Phase 1 — preflight and rollback capture

On `tgw-prod`, capture without changing state:

```bash
hostname
readlink -f /run/current-system
readlink -f /run/booted-system
readlink -f /opt/TGW/current
sudo nix-env --list-generations -p /nix/var/nix/profiles/system | tail -10
systemctl --failed --no-pager
systemctl list-units 'tgw*' --type=service --all --no-pager
systemctl list-timers 'tgw*' --all --no-pager

sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  git branch --show-current
  git rev-parse HEAD HEAD^{tree}
  git status --short
'
```

Also record:

- last successful database backup and restore target;
- ItemData/sync status;
- current selected-release receipt and previous generation;
- the running wheel/module hashes for changed modules;
- affected unit `ExecStart`, `EnvironmentFiles`, user/group, and start time;
- queue counts and any existing dead letters/reconciliation rows;
- intentionally known health warnings.

A dirty flake or application worktree is a hold unless every unrelated path is
attributed and preserved. Never clean another actor's files to make preflight pass.

## Phase 2 — build and review the application candidate

Use the complete procedure in
`governed-coding-release-v2-20260814.md`. The candidate must be a closed commit and
tree with affected tests, full tests, Ruff, Python compilation, and `git diff --check`
bound to that tree. Production-risk changes also require independent semantic/security
review and controller verification.

Create an unprefixed exact Git archive:

```bash
git archive --format=tar -o <TASK-DIR>/source.tar <CANDIDATE-COMMIT>
git get-tar-commit-id < <TASK-DIR>/source.tar
sha256sum <TASK-DIR>/source.tar
```

The archive root must contain `src/`, `pyproject.toml`, and the repository files
directly. A `trader-grims-warehouse/` prefix is not accepted by the production release
layout and caused a failed installation attempt on 2026-08-14.

Build the wheel from the same closed commit, offline/no-dependency, and verify every
changed installed module by extracting it from the wheel and comparing its SHA-256 to
the source file. Preserve the wheel/archive hashes in the deployment record.

## Phase 3 — evaluate and activate Nix changes, when present

Skip this phase only when the flake, unit definitions, dependencies, credentials,
and host configuration are unchanged.

Follow `nix-flake-maintenance-v2-20260814.md`:

1. review only intended flake files;
2. run metadata, eval, build, and affected-output checks;
3. run dry activation and review every restart/change;
4. commit only intended files;
5. dispatch registered `nixos-prod-switch/v1` with exact approval/evidence;
6. record the old/new closures and verify affected units.

Do not assume a switch reboots the machine. Keep `/run/current-system` and
`/run/booted-system` as separate facts.

### A3/bootstrap state

The Nix flake installs the A3 public artifacts and wrapper through
`hosts/tgw-prod/a3-platform-bootstrap.nix`. Public flake paths include:

- `a3-public/codex-authorized-key.txt`;
- `a3-public/nix-observer-render-attestation.pub`;
- `a3-public/nix-observer-render-composition.json`;
- `a3-public/nix-observer-render-prerequisite.json`;
- `a3-public/nix-observer-render-wrapper.conf`.

Installed private/config artifacts under `/etc/tgw` are root protected. Never print
the private attestation key. The current composition still names the historical
`rjpq...` NixOS closure while the live closure is `8nlj...`; therefore it proves only
the older installation and cannot authorize another A3 evaluation. Refresh and
review the composition/prerequisite evidence before any future A3 dispatch.

## Phase 4 — select the immutable application release

Use registered procedure `app-release-install/v1`. Inputs are exactly:

- archive path and SHA-256;
- generation name;
- 40-character commit and tree;
- exact expected current generation;
- unique operation ID.

The installer writes the prepared operation, extracts to an immutable generation,
verifies its manifest, compare-and-swap selects `/opt/TGW/current`, and writes a
completed receipt. A mismatched current generation, reused ID with different inputs,
unsafe member, altered file, or interrupted ambiguous selection must hold.

After dispatch, run the registered verifier for the selected generation and check:

```bash
readlink -f /opt/TGW/current
sudo -n /opt/tgw-installer/current/bin/tgw-release-install \
  --root /opt/TGW verify <GENERATION>
```

The root-owned `/opt/tgw-installer/current` wrapper is independent of the selected
application release. Do not verify a candidate by importing its own installer.

## Phase 5 — activate the matching runtime wheel

As of 2026-08-14, immutable release selection does not install the wheel into
`/opt/TGW/.venvironments/tgw`; there is no registered runtime-wheel activation
procedure in `procedures.json`. This is an explicit remaining operational gap.

Until that procedure exists, an approved installation must perform a bounded
privileged wheel activation and record it separately:

1. verify the transferred wheel hash on `tgw-prod`;
2. stop only affected services when safe (all services for a cross-cutting package;
   `tgw-http.service` for an HTTP-only module change);
3. install the local wheel with `--no-index --no-deps --force-reinstall`;
4. hash the installed changed modules and compare with the wheel/source;
5. restart the affected services even if installation fails;
6. record old/new wheel and module identities.

Use an exit trap or equivalent so a failed package command cannot leave the HTTP
service stopped. Never rename a wheel to a non-wheel filename; pip validates the
distribution/version/tag structure before installation.

Do not restart unaffected workers for an HTTP-only patch. Conversely, worker source
changes do not become live until their exact units restart.

## Phase 6 — verify services and runtime binding

For every affected unit:

```bash
systemctl show <UNIT> \
  -p FragmentPath -p ExecStart -p User -p Group -p WorkingDirectory \
  -p EnvironmentFiles -p ActiveState -p SubState -p MainPID \
  -p ExecMainStartTimestamp
journalctl -u <UNIT> --since '<INSTALL-START>' -p warning --no-pager
```

Verify the `ExecStart` entry point resolves to the intended venv and that imported
changed modules have the expected hash. Check all failed units, not only the unit that
was restarted:

```bash
systemctl --failed --no-pager
sudo -u tgw /opt/TGW/.venvironments/tgw/bin/tgw health
```

Expected timer-triggered oneshot units may be `inactive (dead)` between runs. A timer
or oneshot state is not judged by the same rule as a continuously running worker.

## Phase 7 — prove the operator-visible function

Use the ordinary authenticated interface, not an unauthenticated curl approximation.
For UI work, log in, load the exact page, check the rendered controls/data, and exercise
only the effect the operator authorized.

The 2026-08-14 listing-action correction was accepted only after an authenticated GET
of `/form/items/tgw202510161310076` returned:

```text
List on eBay · AI Reidentify · Archive · Delete
```

The verifier did not click `List on eBay`; installation authority did not include an
eBay publish effect. This distinction is mandatory.

For a worker or workflow change, capture the Action Card, one bounded canary, exact
attempt/result/receipt, queue convergence, and the absence of duplicate or ambiguous
provider effects. See `pp-workflow-001-acceptance.md`.

## Completion record

Do not say “installed” until the record contains:

- Plan approved ref plus platform solution/closure;
- application commit/tree/archive/wheel/module identities;
- independent review/controller result when required;
- selected generation and completed selection receipt;
- prior generation and rollback receipt path;
- flake commit/tree and old/new live closures when changed;
- live and booted closure paths;
- exact affected unit runtime identities and journals;
- health/failed-unit results;
- authenticated live behavior result;
- effects deliberately not performed;
- preserved warnings, dead letters, ambiguity, or follow-up gaps.

## Rollback

### Application/source selection

Dispatch registered `app-release-rollback/v1` with the completed install receipt,
exact expected current generation, and a unique rollback operation ID. Verify the
newly selected old generation.

Then reinstall the matching prior wheel and restart the same affected services. A
source-selector rollback without a wheel rollback does not change executing code.

### NixOS

Dispatch registered `nixos-prod-rollback/v1` only after recording the current closure
and intended prior generation. Verify live closure, failed units, affected unit
contracts, and application health afterward.

### Durable data and provider state

Application/Nix rollback does not erase PostgreSQL rows, ItemData generations,
authorities, observations, provider effects, eBay changes, queue history, or receipts.
Use their domain reconciliation/restore procedures. Never delete evidence to make a
rollback look clean.

### Recovery after interruption

Use `tgw-release-install recover` only for a prepared release selection whose exact
selected generation is already current and still verifies. Any other selector state is
ambiguous and requires operator reconciliation.

## Known status requiring future closure

- Runtime-wheel activation needs a registered, independently installed procedure.
- `/run/current-system` and `/run/booted-system` differ; reboot proof is outstanding.
- The A3 composition is not fresh for the current live Nix closure.
- Coding access reports `provider_status=unknown`; see the coding runbook before
  treating automated coding provision as operational.
- Routine health may contain pre-existing backup/eBay-sync warnings. Record and
  classify them; do not hide them or call them new installation failures.

# tgw-prod Nix flake maintenance

**Owner:** shared; operator approval: Dave

**Last verified:** 2026-08-10

**Applies to:** `tgw-prod`, NixOS, flake checkout `/home/db/tgw-flake`, branch `master`

**Last drill:** 2026-08-10, onboarding-worker flake evaluation and activation

## Maintainer identity and scope

The operational maintainer pattern is a regular coding session operating the
real checkout as Unix account `db` on `tgw-prod`. Recent accepted commits use
the configured `Dave / <agent>` Git identity. Verify the configured identity;
do not invent or impersonate one.

The following are obsolete and must not be used as authority:

- `/opt/TGW/.claude/agents/nix-flake-maintainer.md`;
- the historical `commit-nix-flake` and `verify-nix-flake` skills;
- any retired or unregistered machine as a build/deployment host. `tgw-prod` is the lone Nix host.

This runbook authorizes no change by itself. Work from the specific requested
source/config delta and obtain explicit approval before a live switch.

## Canonical locations

| Purpose | Location |
|---|---|
| Flake checkout | `/home/db/tgw-flake` |
| Branch | `master` |
| Production host | `nixosConfigurations.tgw-prod` |
| Host composition | `nix/hosts/tgw-prod.nix` |
| TGW services/workers | `nix/tgw.nix` |
| Immutable launcher module | `modules/nixos/services/tgw-launch.nix` |
| Flake outputs/inputs | `flake.nix`, `flake.lock` |
| Running system | `/run/current-system` |
| Booted system | `/run/booted-system` |
| NixOS system profile | `/nix/var/nix/profiles/system` |

The Python source release selected by `/opt/TGW/current` is separate from the
NixOS system generation. A flake switch does not select a TGW source release,
and a TGW release selection does not update systemd unit definitions.

## Preflight: establish exact state

Run Git/Nix read operations as `db`:

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  printf "branch="; git branch --show-current
  printf "head="; git rev-parse HEAD
  git status --short
  git log -5 --date=iso-strict --format="%h %ad %an <%ae> %s"
  git config --get user.name
  git config --get user.email
'

readlink -f /run/current-system
readlink -f /run/booted-system
sudo nix-env --list-generations -p /nix/var/nix/profiles/system
systemctl --failed --no-pager
```

As of the last verification, `nix/tgw.nix.bak` is a pre-existing untracked
file. Preserve it and never stage it as part of an unrelated change. A dirty
tree is not permission to clean, reset, delete, or absorb other work.

Before editing, identify every intended file and record unrelated tracked and
untracked changes. If an intended file already has overlapping changes whose
ownership is unclear, stop and resolve ownership before continuing.

## Edit discipline

- Change the smallest host/module surface that owns the behavior.
- Keep host-specific worker enablement in `nix/hosts/tgw-prod.nix`.
- Keep queue-to-entrypoint mappings and generic TGW service behavior in
  `nix/tgw.nix`.
- Keep immutable-launcher ExecStart behavior in
  `modules/nixos/services/tgw-launch.nix`.
- Do not edit generated hardware configuration, disko layout, secrets, or
  `flake.lock` unless the request explicitly requires it.
- Never add a worker to the default fleet merely to enable it on tgw-prod.
  Evaluate `vm` and every other host output when a default changes.
- A systemd stop/disable is not a durable configuration change. The explicit
  `services.tgw.workers` list is authoritative across rebuilds.
- Never print secret files or environment contents into logs or review output.

## Review the exact delta

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  git status --short
  git diff --check
  git diff -- <INTENDED_FILE_1> <INTENDED_FILE_2>
'
```

For worker changes, confirm all three identities agree:

1. queue name in `services.tgw.workers`;
2. mapping in `workerScripts` and, when needed, `workerEntrypoints`;
3. exact console script in the selected TGW release's `pyproject.toml` and
   installed venv.

```bash
grep -n '<QUEUE_OR_SCRIPT>' \
  /home/db/tgw-flake/nix/tgw.nix \
  /home/db/tgw-flake/modules/nixos/services/tgw-launch.nix \
  /opt/TGW/current/pyproject.toml
test -x /opt/TGW/.venvironments/tgw/bin/<CONSOLE_SCRIPT>
```

## Evaluation and build gates

Run from the real checkout so the candidate includes the current intended
working-tree delta:

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake

  nix flake metadata --no-write-lock-file path:.
  nix eval --json \
    path:.#nixosConfigurations.tgw-prod.config.services.tgw.workers
  nix eval --raw \
    path:.#nixosConfigurations.tgw-prod.config.system.build.toplevel.drvPath
  nix build --no-link \
    path:.#nixosConfigurations.tgw-prod.config.system.build.toplevel
'
```

When shared defaults/modules change, also evaluate the registered VM output:

```bash
sudo -u db -H bash -lc '
  cd /home/db/tgw-flake
  nix eval --raw path:.#nixosConfigurations.vm.config.system.build.toplevel.drvPath
'
```

Do not evaluate, SSH to, or deploy retired/unregistered host outputs from this
procedure. Historical output names require a separate cleanup/migration unit.

For a changed unit, inspect the evaluated contract rather than assuming the
module generated what was intended:

```bash
sudo -u db -H nix eval --raw \
  'path:/home/db/tgw-flake#nixosConfigurations.tgw-prod.config.systemd.services."tgw-worker@<QUEUE>".serviceConfig.ExecStart'
```

## Dry activation gate

Record the current closure first. Then run:

```bash
readlink -f /run/current-system
sudo nixos-rebuild dry-activate --flake path:/home/db/tgw-flake#tgw-prod
```

Review every reported unit restart/stop/start. Unexpected worker creation,
secret/config ownership changes, mount changes, firewall changes, database
actions, or unrelated service restarts are blockers.

For TGW worker changes, explicitly compare the evaluated list with live units:

```bash
systemctl list-unit-files 'tgw-worker@*' --no-pager
systemctl list-units 'tgw-worker@*' --all --no-pager
```

## Commit discipline

Commit only after evaluation/build/dry-activate gates pass and only when the
task authorizes a commit.

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  git add -- <EXACT_INTENDED_FILES>
  git diff --cached --check
  git diff --cached
  git status --short
  git commit -m "<MESSAGE>"
  git show -s --format="%H%n%P%n%an <%ae>%n%s" HEAD
'
```

Never use `git add -A`, `git add .`, or stage `nix/tgw.nix.bak`. Do not push
unless explicitly requested.

## Live switch

A successful dry activation or build is not proof of a live switch. After
explicit deployment approval, request registered procedure
`nixos-prod-switch/v1` from `config/environment/procedures.json`. Plan text and
this runbook do not authorize direct execution; the registered runner must bind
the procedure revision, approval, target host, and evidence receipt.

Capture the full command result and new closure, then verify:

```bash
readlink -f /run/current-system
sudo nix-env --list-generations -p /nix/var/nix/profiles/system | tail -10
systemctl --failed --no-pager
systemctl status <AFFECTED_UNIT>.service --no-pager
systemctl show <AFFECTED_UNIT>.service \
  -p FragmentPath -p ExecStart -p User -p Group -p WorkingDirectory \
  -p EnvironmentFiles -p ActiveState -p SubState -p Result
journalctl -u <AFFECTED_UNIT>.service --since '-15 minutes' --no-pager
```

For TGW changes also run application health and verify the selected immutable
source release. Do not claim all services healthy from `nixos-rebuild` alone.

```bash
sudo -u tgw tgw health
sudo env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/TGW/current/src \
  python3 -B -m tgw.release_installer --root /opt/TGW \
  verify "$(basename "$(readlink -f /opt/TGW/current)")"
```

## Rollback

Rollback the NixOS generation when activation introduced a system/unit defect
by requesting registered procedure `nixos-prod-rollback/v1`. Record the current
closure and intended rollback generation before approval. After the registered
procedure completes, verify `/run/current-system` and failed units using the
read-only checks above.

Then verify affected services and application health. Rollback does not erase
database rows, provider effects, queue history, ItemData mutations, or the TGW
source release. Handle those through their own runbooks; never delete evidence
to make rollback appear clean.

Revert the flake source separately with a normal reviewed commit if the bad
configuration must be removed from `master`. Do not use `git reset --hard` or
rewrite accepted history.

If the host cannot complete a switch, select a known system generation only
after identifying its number from the system profile and recording the current
closure. Prefer the standard NixOS rollback command over manually invoking a
profile's `switch-to-configuration`.

## Completion receipt

Record:

- flake commit and parent;
- author identity and subject;
- exact changed files;
- build and dry-activate result;
- old and new `/run/current-system` closures;
- switch result and time;
- affected unit status/ExecStart;
- application health result;
- rollback generation;
- any intentionally preserved dirty/untracked files.

Do not conflate these evidence classes: a commit proves source history, a build
proves evaluation/buildability, dry-activate predicts activation changes, a
switch changes the system profile, and post-switch checks prove live behavior.

# tgw-prod Nix flake maintenance — v2 (2026-08-14)

**Owner:** shared; operator authority: Dave

**Last verified:** 2026-08-14

**Applies to:** `tgw-prod`, NixOS 25.05, flake checkout `/home/db/tgw-flake`,
branch `master`, A3/platform bootstrap installed

**Last drill:** 2026-08-14 flake reconciliation, evaluation/build, switch, service
fleet verification, dedicated SSH/A3 artifact verification, and application-source
binding correction

## What this supersedes

This is a new procedure version. It does not edit or delete
`nix-flake-maintenance.md`. That earlier file remains useful history, but its
pre-install dirty-tree exception and platform inventory are no longer current.

This runbook does not authorize a switch. It defines the evidence and registered
procedure needed after the exact Plan/task grants the effect.

## Canonical locations

| Purpose | Location |
|---|---|
| Production flake checkout | `/home/db/tgw-flake` |
| Branch | `master` |
| Host output | `nixosConfigurations.tgw-prod` |
| TGW unit/worker module | `nix/tgw.nix` |
| Host configuration | `nix/hosts/tgw-prod.nix` and imported host modules |
| A3 host integration | `hosts/tgw-prod/a3-platform-bootstrap.nix` |
| A3 public inputs | `a3-public/` |
| Flake inputs | `flake.nix`, `flake.lock` |
| Live system | `/run/current-system` |
| Booted system | `/run/booted-system` |
| System profile | `/nix/var/nix/profiles/system` |
| Selected application source | `/opt/TGW/current` |
| Executing application venv | `/opt/TGW/.venvironments/tgw` |
| Registered procedures | application `config/environment/procedures.json` |

At last verification, the flake worktree was clean at:

```text
commit 69044619318b7f3c2ccb66e6e6ba09dcba5c93e5
tree   106ff50d4de5c114a4d4b14ab74500a56d99531c
```

The historical `nix/tgw.nix.bak` file was moved to protected quarantine during the
installation. It is no longer a standing allowed dirty path. Any new dirty/untracked
file must be attributed and preserved or reconciled before evaluation.

## Maintenance access

From `tgw-lib`, use the dedicated `db` maintenance identity with no ambient SSH
configuration, agent, default keys, or host-key fallback:

```bash
sudo -n -u db -H ssh -F /dev/null \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o IdentityAgent=none \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/home/db/.ssh/known_hosts \
  -i /home/db/.ssh/id_ed25519_codex_maintenance_20260814 \
  db@192.168.60.100 '<READ-ONLY-COMMAND>'
```

Start with `hostname`, `id`, and Git/system state. This key path is operational
metadata; the private bytes must never be read into output, copied into the flake, or
used for another role.

## Preflight

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  printf "branch="; git branch --show-current
  printf "head="; git rev-parse HEAD
  printf "tree="; git rev-parse HEAD^{tree}
  git status --short
  git log -8 --date=iso-strict --format="%H %ad %an <%ae> %s"
  git config --get user.name
  git config --get user.email
'

printf 'current='; readlink -f /run/current-system
printf 'booted='; readlink -f /run/booted-system
sudo nix-env --list-generations -p /nix/var/nix/profiles/system | tail -10
systemctl --failed --no-pager
readlink -f /opt/TGW/current
```

As of 2026-08-14, current and booted closures differ. Record both. A switch is not a
reboot test, and a later reboot must not be assumed safe merely because current units
are healthy.

Stop before editing when:

- branch is not `master`;
- unknown dirty/untracked state exists;
- an intended path has overlapping unowned changes;
- the Plan/source/archive/input identities differ from the approved request;
- current system/CAS facts are stale for an A3 request;
- rollback generation is absent or unverified.

## Scope and edit discipline

- Put queue/entrypoint and general TGW service behavior in `nix/tgw.nix`.
- Put host-specific enablement in the host composition.
- Put launcher behavior in its owning module; do not patch generated unit files.
- Change `flake.lock` only when the request explicitly changes an input and records
  the exact new closure.
- Never edit hardware/disko/secrets during routine application service maintenance.
- Never add a worker to shared defaults merely to enable it on `tgw-prod`.
- Never use `git add .`, `git add -A`, reset, clean, or history rewriting.
- A systemd stop/disable is not durable configuration; the flake is authoritative.
- Keep application release selection separate from Nix unit configuration.

## A3/platform-bootstrap boundaries

`hosts/tgw-prod/a3-platform-bootstrap.nix` currently:

- declares the `codex` UID/GID;
- forces public-key-only SSH and an exact authorized-key file;
- installs the A3 wrapper package;
- binds five public flake artifacts;
- installs root-protected composition, prerequisite, public key, wrapper config, and
  private attestation material.

Public inputs and their 2026-08-14 hashes:

```text
codex-authorized-key.txt                         d70867c288ce712575624a8544335b76ecb566d6cf6759b7c18c1cf92df13ddb
nix-observer-render-attestation.pub             ae6cb3754e64c675a152a84b523643fc1e0b5379780e6768f2e32582acde4a43
nix-observer-render-composition.json            9bfadef65899f91cb5fcff47571517b8a52553a36f1f0f12613866007904f2a5
nix-observer-render-prerequisite.json           d2fd5e135c7a155750ee0d8a4596a3b57eb5ad56823770ce60c57a2b21b3a240
nix-observer-render-wrapper.conf                305f6214c75b301e025ce407122e76869277a7cfe4b24d888a2c8cc905b9ea27
```

Do not paste the authorized key text or private signing key into tickets. Hash and
stat the public/config artifacts; verify private files only through bounded identity
checks.

The installed A3 wrapper is
`/run/current-system/sw/bin/tgw-nix-observer-render-wrapper`, invoked through the
exact sudoers rule. It is no-argv and packet-framed. It is not a generic root command.

The current composition names the older `rjpq...` system, while the live system is
`8nlj...`. Any future A3 observation/evaluation requires fresh current-system, CAS,
tool, source, composition, and prerequisite evidence. “INSTALLED” in the older
prerequisite cannot authorize a current dispatch.

## Review the exact delta

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  git status --short
  git diff --check
  git diff -- <EXACT-INTENDED-FILES>
'
```

For each worker/service change, compare:

1. configured worker/queue name;
2. module mapping and generated unit entry point;
3. selected application `pyproject.toml` console script;
4. installed venv executable;
5. live unit `ExecStart`.

```bash
grep -n '<QUEUE-OR-SCRIPT>' \
  /home/db/tgw-flake/nix/tgw.nix \
  /opt/TGW/current/pyproject.toml
test -x /opt/TGW/.venvironments/tgw/bin/<CONSOLE-SCRIPT>
systemctl show tgw-worker@<QUEUE>.service -p ExecStart -p FragmentPath
```

## Evaluation and build gates

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  nix flake metadata --no-write-lock-file path:.
  nix eval --json path:.#nixosConfigurations.tgw-prod.config.services.tgw.workers
  nix eval --raw path:.#nixosConfigurations.tgw-prod.config.system.build.toplevel.drvPath
  nix build --no-link path:.#nixosConfigurations.tgw-prod.config.system.build.toplevel
'
```

Evaluate every registered affected host output when shared defaults/modules change.
Inspect exact rendered unit contracts:

```bash
sudo -u db -H nix eval --raw \
  'path:/home/db/tgw-flake#nixosConfigurations.tgw-prod.config.systemd.services."<UNIT>".serviceConfig.ExecStart'
```

For SSH/A3 changes, additionally verify `sshd` configuration, authorized-key file
mode/ownership, wrapper binary/config/tool hashes, and the real OpenSSH parity harness
appropriate to the changed boundary. A fake SSH executable is not production parity.

## Dry activation

```bash
readlink -f /run/current-system
sudo nixos-rebuild dry-activate --flake path:/home/db/tgw-flake#tgw-prod
```

Review every unit start/stop/restart and every config/secret/mount/firewall/database
change. Unexpected effects are blockers. Record current unit inventory before and
after:

```bash
systemctl list-unit-files 'tgw*' --no-pager
systemctl list-units 'tgw*' --all --no-pager
```

## Commit and registered switch

Commit only the exact reviewed files after all gates pass:

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  git add -- <EXACT-INTENDED-FILES>
  git diff --cached --check
  git diff --cached
  git status --short
  git commit -m "<MESSAGE>"
  git show -s --format="%H%n%P%n%T%n%an <%ae>%n%s" HEAD
'
```

Do not push unless the exact task authorizes it.

Dispatch registered procedure `nixos-prod-switch/v1` only after exact deployment
authority is recorded. The runner must bind procedure revision, target, flake commit,
preflight evidence, approval, old closure, and output receipt. Do not substitute an
unregistered low-level system switch command.

## Post-switch verification

```bash
printf 'current='; readlink -f /run/current-system
printf 'booted='; readlink -f /run/booted-system
sudo nix-env --list-generations -p /nix/var/nix/profiles/system | tail -10
systemctl --failed --no-pager

systemctl show <AFFECTED-UNIT> \
  -p FragmentPath -p ExecStart -p User -p Group -p WorkingDirectory \
  -p EnvironmentFiles -p ActiveState -p SubState -p Result -p MainPID
journalctl -u <AFFECTED-UNIT> --since '<SWITCH-START>' --no-pager

sudo -u tgw /opt/TGW/.venvironments/tgw/bin/tgw health
sudo -n /opt/tgw-installer/current/bin/tgw-release-install \
  --root /opt/TGW verify "$(basename "$(readlink -f /opt/TGW/current)")"
```

Verify operator-visible behavior for changed HTTP/MCP/worker functions. A new Nix
closure and active unit do not prove the application workflow.

If the switch is intended to be boot-persistent, schedule an operator-approved reboot
drill, then confirm `/run/booted-system` equals the selected known-good generation and
repeat health/unit checks.

## Rollback

Dispatch `nixos-prod-rollback/v1` with exact current closure and intended rollback
generation. Verify live/booted paths, failed units, affected unit contracts, selected
application release, and health.

Rollback does not erase source commits, application releases, database rows, ItemData,
queue history, provider effects, credentials, or externally performed actions. Revert
bad flake source with a normal reviewed successor commit; do not reset accepted
history.

## Completion receipt

Record:

- Plan/task authority and procedure revision;
- flake commit/parent/tree/author and exact changed files;
- input/lock changes and closure identities;
- eval/build/dry-activation results;
- old/new current and booted system paths;
- affected unit pre/post contracts and journals;
- application release and installed-runtime identities;
- health and live behavior results;
- rollback generation;
- preserved unrelated state and deliberately unperformed effects.

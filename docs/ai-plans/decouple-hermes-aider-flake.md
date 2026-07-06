# Plan: decouple Hermes + Aider from flake control

**Status:** EXECUTED 2026-07-06 (Option A) — `nixos-rebuild switch --flake .#tgw-prod`
succeeded (`nixos-system-tgw-prod-25.05.20260102.ac62194`); `hermes-agent`
stayed active/healthy through the switch (same PID, no restart needed —
config.yaml's `model` key untouched, confirmed by the merge script's
deep-merge-preserves-unknown-keys behavior). `android-tools`/`pipx` confirmed
on PATH. `aider-chat` installed via `pipx install aider-chat` → 0.86.2 (newer
than the removed Nix pin, 0.83.1 — demonstrates the point). Todo #1227.
**Goal:** change Hermes' model/settings and Aider's version without a
`nixos-rebuild switch` cycle, while keeping process supervision and the
security hardening already built into the current setup.

## The actual problem (Dave, 2026-07-06 — read this before the mechanics below)

This isn't just "make two config knobs easier to turn." Two things converge:

1. **Every `nixos-rebuild switch` carries risk**, however small — it's a whole
   new system generation. Fine for a deliberate, gated change (a new service,
   a security fix); wrong unit of risk for "I want to try a different model
   today."
2. **Wrangling the flake itself has been consuming disproportionate usage** —
   whole day-budgets spent getting a clean flake, against tasks that should
   be ordinary coding. That cost is a signal the flake's surface area is too
   large, not that Dave needs to get better at Nix.

The fix isn't scoped to Hermes/Aider specifically — it's a **standing rule**:
anything under active iteration (a tool Dave is tuning, swapping versions of,
or prototyping with day-to-day) does not belong in the flake at all, even
"nicely." Home Manager pins and Nix-declared settings feel declarative and
safe, but for a churny tool they just move the churn into the one place that's
expensive to touch. The flake should hold what's actually settled: the OS
layer, the TGW service stack, security-relevant config (secrets wiring, user/
group, hardening). Everything Dave is actively iterating on lives in
userspace — venv/pipx/npm/git checkout — explicitly outside Nix's remit, so
changing it is a normal file edit + process restart, not a system generation.

This is also a deliberate "return to normalcy": a dev environment Dave can
touch freely without it being system configuration, so working in it doesn't
carry the flake's weight or its risk.

**Standing rule (apply going forward, not just to this todo):** before adding
anything to the flake, ask whether it's settled infrastructure or something
still being iterated on. Iterated-on tools default to userspace install
(pipx/uv/npm/git checkout) even if that means giving up Nix's reproducibility
for that one tool — the reproducibility isn't worth the rebuild-risk +
usage-cost tax while the tool is still moving.

## Current state (verified against `~/tgw-flake` and the live `tgw-prod` unit)

**Hermes** — `flake.nix` pulls `hermes-agent` as a flake input
(`github:NousResearch/hermes-agent`, pinned rev in `flake.lock`), imports its
`nixosModules.default` into the `tgw-prod` config, and `nix/os/hermes.nix`
sets `services.hermes-agent.settings.model = { provider = "openrouter";
default = "xiaomi/mimo-v2.5-pro"; ... }` plus `environmentFiles` (secrets) and
`addToSystemPackages = true`. Changing the model today means editing this file
and running `nixos-rebuild switch` (a full closure rebuild).

**Important discovery:** the hermes-agent module already has a live-edit path.
`addToSystemPackages = true` (already on) makes `$HERMES_HOME/.hermes/config.yaml`
group-writable (0660) and adds the `hermes` CLI/TUI to system PATH, and NixOS
activation **merges** the Nix-generated settings into the existing
`config.yaml` rather than overwriting it — any key NOT declared under Nix's
`settings.*` survives untouched. The only reason model changes require a
rebuild today is that `model` **is** one of the declared Nix keys, so Nix wins
that key on every activation and clobbers a live edit.

**Aider** — plain nixpkgs package (`aider-chat`) added to `home.packages` in
`nix/os/dev.nix`'s Home Manager block. No live-edit path exists for a
home-manager package; any version change is a Nix edit + rebuild.

## Option A — surgical (recommended default)

Stop declaring the fields Dave actually wants to iterate on in Nix; keep
structural/security concerns (user, secrets wiring, service enable) in Nix.

- **Hermes:** in `nix/os/hermes.nix`, drop the `settings.model` block entirely
  (or narrow it to a one-time seed value only). Model becomes a live
  `$HERMES_HOME/.hermes/config.yaml` edit + `systemctl restart hermes-agent`
  — no rebuild. `environmentFiles` (API keys) stays in Nix since that's a
  secrets/security concern, not a prototyping knob.
- **Aider:** remove `aider-chat` from `nix/os/dev.nix`'s `home.packages`.
  Bootstrap once with `pipx install aider-chat` (pipx already resolves from
  nixpkgs, or add `pipx`/`uv` to `nix/os/dev.nix` systemPackages — a
  stable, rarely-changing dependency, unlike Aider itself). Upgrades become
  `pipx upgrade aider-chat`, installs of a git checkout become
  `pipx install --editable <path>` — neither touches the flake.

**Tradeoff:** Aider is no longer pinned/reproducible via the flake — acceptable
per Dave's own framing (a dev tool he wants to iterate on, not a declared
service). Hermes keeps its NixOS-managed user/state-dir/systemd hardening;
only the experimentation-prone `model` key leaves Nix's control.

## Option B — full decouple (if Option A isn't enough)

For Hermes specifically, if Dave wants to swap the *package itself* (a fork,
a different version, testing an unreleased branch) without touching
`flake.lock`:

- Remove the `hermes-agent` flake input and `hermes-agent.nixosModules.default`
  import from `flake.nix`/`tgw-prod.nix`.
- Keep a small hand-written `systemd.services.hermes-agent` block (copied out
  of the vendored module — user/group, `ReadWritePaths`, `ProtectSystem=strict`,
  etc. are ~15 lines, not worth losing) but point `ExecStart` at a stable
  path outside the Nix store, e.g. `/var/lib/hermes/.local/state/nix/profile/bin/hermes`
  populated by `nix profile install github:NousResearch/hermes-agent` (or a
  git checkout + its own build) run as the `hermes` user. Package upgrades
  become `nix profile upgrade` / a fresh checkout — no `flake.lock` edit, no
  `nixos-rebuild`.
- This also drops the module's own container-mode escape hatch (see below) —
  only take this path if Option A's settings-only decouple genuinely blocks
  something Dave wants to do (e.g. running a patched hermes-agent).

**Tradeoff:** loses `flake.lock` pinning entirely for Hermes; state/security
hardening has to be hand-maintained instead of inherited from upstream module
updates.

## Also available, not requiring any flake change: Hermes' own container mode

The vendored module ships `services.hermes-agent.container.enable = true` —
runs Hermes in a Docker/Podman container with `/nix/store` bind-mounted
read-only and a persistent writable layer for `apt`/`pip`/`npm install`. This
is upstream's answer to "let the agent install its own tools without a NixOS
rebuild," but it's scoped to what the *agent* installs at runtime, not to
Dave changing `model`/settings from outside — Option A still applies on top
of this if adopted. Flagging as a third lever, not proposing it now (adds a
Docker/Podman dependency neither host currently has).

## Recommendation

Start with **Option A** for both components — it's the smallest change, keeps
Hermes' systemd hardening intact, and directly targets the friction Dave
described (model swaps + Aider version churn). Only escalate to Option B for
Hermes if Dave specifically wants to run a non-upstream build.

## Extension: Android tooling (2026-07-06, same planning session)

Dave flagged three Android-adjacent tools while discussing "settled" flake
additions ahead of the Flutter/Kotlin app development push. Fact-checked and
resolved:

- **`android-tools` (adb/fastboot)** — genuinely settled (stable, small,
  no version churn worth avoiding) — **add to the flake now**, per the
  standing rule above. Goes in `nix/os/dev.nix` alongside `nodejs_22`.
- **Android Studio** — agreed settled in principle (an IDE Dave will use for
  years across the upcoming Flutter/camera-app work, not something iterated
  on day-to-day), **but deferred** — `/opt/TGW` is already at 83% used /48G
  free (see master plan "Drive-space re-evaluation," todo #1136) and Android
  Studio + SDK + emulator images commonly run 15-20GB. Revisit once #1136's
  drive-fleet resolution lands or a scoped exception is found (e.g. install
  on a different volume/host than `/opt/TGW`'s nvme).
- **Amazon Fire Toolbox** — dropped. Windows-only GUI wrapping ADB commands,
  not in nixpkgs, no known native Linux build. Not worth a Wine gamble or a
  separate Windows box for this. If a real need resurfaces, the underlying
  functionality is just `android-tools` commands — script it directly instead
  of chasing the GUI tool.
- **"Tasker Permissions App"** — not a flake addition at all (it's an
  Android-side companion APK, not a Linux package). Dave wants to **absorb
  the underlying capability into TGW's own Android interfaces** instead of
  depending on a third-party app — ties directly into PP-TASKER-001
  (frozen) and the camera/barcode integration tightening already planned
  under PP-INTAKE-004. Captured in the master plan under PP-TASKER-001 —
  not scoped further yet, needs its own pass when that track thaws.

## Execution checklist (once Dave picks an option)

1. Edit `nix/os/hermes.nix` — remove/narrow `settings.model`.
2. Edit `nix/os/dev.nix` — remove `aider-chat` from home-manager packages;
   add `pipx` (or `uv`) to `environment.systemPackages` if not already
   reachable.
3. `nix flake check` (all 4 configs) before pushing.
4. `bash scripts/tgw-push-config.sh tgw-prod <tailscale-ip>` (per
   `PLAN-nixos-migration.md`'s push model — Dave runs this).
5. One-time bootstrap on tgw-prod: `pipx install aider-chat`; confirm `aider
   --version` on PATH for the `db` user.
6. Confirm Hermes model swap works live: edit
   `/var/lib/hermes/.hermes/config.yaml`'s `model` key,
   `systemctl restart hermes-agent`, check `journalctl -u hermes-agent` picks
   up the new model with no rebuild in between.
7. Update `docs/TGW-Plan-Vault/reference/hermes.md`-equivalent doc (memory:
   `reference-hermes`) noting model is now a live config, not a Nix key.

## Follow-on, same session (2026-07-06): first real use of the live-edit path

Dave purchased DeepSeek + Google credits and wants Hermes' primary model
switched to `deepseek-v4-flash` (direct DeepSeek provider, not via
OpenRouter) — Google's key (`GEMINI_API_KEY`) was already present in
`hermes.env` from before, wired to the first-party `gemini`/`google`
provider; nothing else needed there. Confirmed `/var/lib/hermes/.hermes/config.yaml`
is group-writable (0660, `hermes:hermes`, `db` in the `hermes` group) — edited
directly, no sudo, no rebuild, exactly the friction this whole plan targeted.

**Applied:** `config.yaml`'s `model.provider: openrouter` → `deepseek`,
`model.default: xiaomi/mimo-v2.5-pro` → `deepseek-v4-flash`.
**Pending (Dave):** `DEEPSEEK_API_KEY` doesn't exist yet (paid but not
generated). **`hermes-agent` was deliberately NOT restarted** — it's still
running on the old in-memory model/OpenRouter config. Once the key exists:
`sudo bash -c 'echo "DEEPSEEK_API_KEY=<key>" >> /opt/TGW/secrets/hermes.env'`
then restart (`sudo systemctl restart hermes-agent`) to pick up both the new
key and the new model. Dave separately noted a higher-reasoning escalation
path via `claude -p` (Claude Code CLI, print mode) for harder tasks — that's
his own manual workflow alongside Hermes, not wired into Hermes'
`fallback_providers` chain. Not implemented as an automatic fallback;
revisit only if Dave asks for that explicitly ("then we will see").

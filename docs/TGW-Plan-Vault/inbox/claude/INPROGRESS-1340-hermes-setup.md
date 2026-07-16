# IN PROGRESS: todo #1340 — set up Hermes from scratch

Started 2026-07-12. Dave wants to hands-on set up Hermes (basic functions,
model config, the services already defined in PP-HERMES-EA-001/Tigwa+Leotha),
using this as scaffolding to inform the planning/execution context-scoping
work discussed earlier this session (see chat, not yet written to a PP).

## Ground truth established so far
- `hermes-agent` fully removed from `~/tgw-flake` 2026-07-11 (todo #1321,
  `RESEARCH-INPROGRESS-1321-nix-flake-changes.md`) — Dave's own call, moving
  to userspace like aider-chat. Confirmed live: no `hermes-agent` systemd
  unit (system or user), no `hermes`/`hermes-agent` binary on PATH, not in
  `pipx list`, not in `nix profile list`.
- `/var/lib/hermes` still exists (old state dir: config.yaml, presumably
  Honcho memory) but is orphaned — owned by uid 994/gid 990, neither of
  which resolves to a real account anymore. Unreadable without sudo.
  Undecided: recover this state or start clean.
- Design docs already in place, not yet built: `pp/PP-HERMES-EA-001.md`
  (Tigwa/Leotha personas, both IN TRAINING, apprenticeship model),
  `docs/ai-plans/decouple-hermes-aider-flake.md` (why it left the flake,
  the live-edit-config.yaml pattern that used to work).
- Package: `github:NousResearch/hermes-agent`, was pinned in flake.lock
  before removal — old model was DeepSeek V4 Flash direct (per
  `project-hermes-model-deepseek` memory), pending `DEEPSEEK_API_KEY`
  which may or may not exist by now (Google/DeepSeek/Anthropic paid direct
  keys were added 2026-07-08 per CLAUDE.md).

## DESIGN SETTLED 2026-07-12 — full detail in pp/PP-HERMES-EA-001.md
- Install: `nix profile install github:NousResearch/hermes-agent` (userspace).
- Office split: Hermes-lite (always-on, tgw-prod) + full Tigwa (heavy
  compute, a1131, woken via WoL on demand).
- Old state recovered + backed up: `/opt/TGW/var/backups/hermes-recovered-2026-07-12/`.
- Wake-trigger: reuse tgw health/ops-digest (already has delta tracking),
  config-driven rules, shadow mode first, decision log.
- Secrets: interim manual copy now, #1253 is the proper mechanism (Dave's own track).
- Nix safety: test>switch, build-vm off-host, isolate risky changes (see
  feedback-nix-prevent-not-recover memory).

## Progress as of 2026-07-12, pre-reboot checkpoint
- Hermes-lite: installed + configured on tgw-prod (`hermes` on PATH via
  `~/.local/state/nix/profile/bin`, config restored to `/home/db/.hermes/`,
  all 4 API keys wired, model=deepseek-v4-flash). NOT yet a systemd
  service — still ad-hoc CLI, gateway not running. Survives reboot fine
  (just files on disk), nothing to restart.
- a1131 toolkit: Codex CLI, Aider, Claude Code CLI, AGY, notebooklm-py all
  installed under the `claude` account, latest stable, verified working.
- OAuth: Claude Code CLI on a1131 AUTHENTICATED (Pro sub,
  claude@mappo.eu.org). Codex CLI + AGY auth NOT yet done. Hermes's own
  openai-codex OAuth (tgw-prod) still OpenAI-429-throttled, needs real
  cooldown before retry. notebooklm-py blocked on Dave creating the
  dedicated tigwa/tgw Google account (not yet created).
- See `feedback-a1131-claude-account-oauth` memory for the OAuth setup
  gotchas (permissions, DISPLAY hang, clipboard) before attempting more.
- **Dave rebooting tgw-prod + a1131 now (2026-07-12) to test whether it
  fixes the clipboard paste issue that blocked OAuth code entry.**

## UPDATE 2026-07-12, immediately pre-reboot: #1322 FIXED before this reboot
`services.tgw.workers` explicit list added to `nix/hosts/tgw-prod.nix`
(excludes pm_intake/thumbnail_gen/velocity_stats/ebay_price_reducer/
ebay_sku_migrate), verified via `nixos-rebuild build` (units confirmed
absent from tgw-workers.target.wants/), staged via `nixos-rebuild boot`
(live system untouched, takes effect THIS reboot). Flake change
uncommitted in ~/tgw-flake, pending Dave's review — do not assume
committed. Todo #1322 marked done.

## UPDATE 2026-07-12, post-reboot: Codex CLI OAuth on a1131 DONE
`codex login --device-auth` succeeded on the 3rd/4th attempt (`claude` account,
a1131 Konsole+fish → `sudo -u claude -i` bash). Verified live:
`codex login status` → "Logged in using ChatGPT", exit 0.

Ground truth from this session, worth keeping:
- `claude` account's real shell is bash (`getent passwd claude`), not fish —
  ruled out as a cause. `claude` has NO `.bashrc`/`.profile` at all though, so
  `sudo -u claude -i` gives a minimal PATH missing npm/pipx bin dirs — must
  `export PATH="$HOME/.npm/bin:$HOME/.local/bin:$PATH"` by hand every session
  until a real `.bashrc` is added for the account (not yet done — candidate
  follow-up).
- `codex`/`claude` binaries are npm-global installed at `/home/claude/.npm/`,
  correctly present the whole time; the early "no response" symptom was NOT
  a broken install.
- Root cause of the first 2-3 failed device-auth attempts: the local `codex
  login --device-auth` process must stay running and itself observe/log the
  approval to complete the flow — finishing the ChatGPT-webUI side alone is
  NOT sufficient. `~/.codex/log/codex-login.log` showed 3 consecutive
  "starting device code login flow" lines with zero completion lines before
  the one that worked; network to auth.openai.com was fine throughout
  (curl 302 in ~0.04s), so it wasn't connectivity. Dave reported a required
  ChatGPT-webUI settings change along the way (specifics not captured).
- AGY: still not found on a1131 under the `claude` account (`nix profile
  list` / `pipx list` don't show it) despite this session's earlier note
  claiming it was "installed... verified working" — that claim looks stale.
  Needs re-checking before attempting AGY login next.
- Codex-CLI login was NOT run by the agent — per
  `feedback-a1131-claude-account-oauth` memory, Dave ran the whole flow
  himself at his own console; agent only verified status pre/post and did
  diagnostic reads (ps, curl, log tail) in between.

## UPDATE 2026-07-12, later: Hermes openai-codex OAuth DONE (tgw-prod)
`hermes auth add openai-codex --type oauth --no-browser` succeeded on the
first attempt this time — the earlier 429 throttle had cleared with time.
Verified live: `hermes auth status openai-codex` → "openai-codex: logged in".
Hermes-lite now has all 3 model credentials wired: DeepSeek + Gemini via
API key (`~/.hermes/.env`), GPT via this OAuth. `hermes auth list` also
showed a pre-existing `copilot` pooled credential (via `gh auth token`) —
not something this session set up, noted for awareness, not investigated.
Not yet done: actually adding an `openai`/`gpt` entry to Hermes's
`config.yaml` model routing (currently still `provider: deepseek,
default: deepseek-v4-flash` only) — auth alone doesn't make GPT usable for
a task until it's wired into model config. Ask Dave whether/how he wants
GPT surfaced in routing before doing that.

## UPDATE 2026-07-12, later still: a1131 account renamed claude -> tigwa
Dave's direction: "This task was described as moving tigwa into her new
office. That is a1131, under my authority." Renamed via the nix flake
(declarative, not raw usermod) per settled Nix safety practice:

- Edited `nix/hosts/a1131.nix` in `~/tgw-flake`: `users.users.claude` ->
  `users.users.tigwa`, uid pinned at 1001 across the rename (unchanged),
  sudo rule (`NOPASSWD: ALL`, Dave's 2026-07-04 authorization) updated to
  match. **Flake change uncommitted, pending Dave's review** (same pattern
  as the #1322 fix — do not assume committed).
- `nixbuild-a1131` fish alias in `nix/tgw/home.nix` gave the exact working
  deploy command: `nixos-rebuild switch --flake path:/home/db/tgw-flake#a1131
  --target-host db@192.168.60.101 --use-remote-sudo`, run as `db` (not
  sudo) from tgw-prod. Verified build -> test -> switch, now generation 51,
  current, persisted (confirmed via `list-generations`).
- **Important gotcha for any future account rename on this project**:
  NixOS's `useradd` creates a FRESH empty home dir for the new username —
  it does NOT move the old account's data. Had to manually: confirm new
  `/home/tigwa` was genuinely empty (`sudo find -mindepth 1`), `rmdir` it,
  then `mv /home/claude /home/tigwa` (uid 1001 pinned identically so no
  chown needed — ownership resolved to the new name automatically).
- **Post-rename breakage found and fixed** (real regressions, not
  hypothetical): (1) `~/.nix-profile` and `~/.local/state/nix/profile`
  top-level symlinks hardcode the OLD absolute home path — broke
  `node`/`codex`/`claude` entirely until relinked to `/home/tigwa/...`.
  (2) `~/.npmrc` had `prefix=/home/claude/.npm` — fixed via sed. (3) pipx
  venvs (`aider-chat`, `notebooklm-py`) bake the absolute venv path into
  shebangs/launcher symlinks/`.pth` files — `pipx reinstall <pkg>` for both
  was the clean fix (don't try to hand-patch every reference; a stale
  Python venv path is scattered too widely to sed reliably). Verified live
  after fixes: `codex login status` → still "Logged in using ChatGPT"
  (credential survived intact), `aider --version` → 0.86.2, `notebooklm
  --version` → 0.7.3, `sudo -u tigwa sudo -n true` → OK.
- Left alone (cosmetic only, no functional impact): `.npm/_logs/*.log`,
  `.bash_history`, a stale Claude-Code session-transcript dir named
  `-home-claude`, and `/home/claude` strings baked into compiled Claude
  Code / Codex binaries.
- **Hermes install on a1131 (`nix profile install
  github:NousResearch/hermes-agent`) kicked off under `tigwa` immediately
  after** — first build on this machine, no cache, running in background
  at session-end. Check completion before assuming Tigwa's a1131 instance
  exists; config/model wiring (mirror tgw-prod: openai-codex/gpt-5.6-sol
  main, DeepSeek/Gemini keys) not yet done.

## UPDATE 2026-07-12, session close: a1131 toolkit fully live, session ending
Dave: "I think I can manage that setup again [Hermes model config on a1131].
Looking good. Seems we have a scaffolding to build this tool. It is a
keystone in our strategy, time for me to onboard tigwa and let her
interview me." Session ending here — Dave taking over interactively.

**Confirmed working, this session, on `tigwa@a1131`** (all verified live,
not just installed): `codex` (OAuth logged in), `claude` (Pro sub auth
intact), `aider` 0.86.2, `notebooklm` 0.7.3, `agy` 1.1.1 (binary runs, auth
not yet done), `hermes` (installed, config/model NOT yet set up — Dave
doing this himself next). All six resolve on PATH with a real login shell
(`ssh tigwa@a1131` or `sudo -u tigwa -i`) — verified via `bash -lc`, not
just a hand-exported PATH.

**Next session should NOT re-verify all of the above from scratch** — it's
confirmed live as of this session close. Pick up from:
1. Hermes-lite model/credential config on a1131 (Dave doing this himself —
   check if done before offering to help)
2. Hermes-lite gateway service (still stopped, not touched this session)
3. Wake-rules config + office-side dispatch mechanism (not yet designed)
4. AGY auth on a1131 — deferred, Dave deciding Google-account strategy,
   do not push on this
5. notebooklm-py auth on a1131 — same Google-account blocker as AGY
6. **Both flake changes now committed** (Dave: "commit the flake"): `1a3285c`
   (#1322 tgw-prod.nix) and `8592ae2` (#1340 a1131.nix: tigwa rename +
   bubblewrap). Neither pushed to origin. Do not re-commit, do not assume
   push was wanted.
7. **Session closed** with Dave confirming role continuity ("You are still
   lead engineering architect. Tigwa is here as both of our assistant") and
   flagging next session's priority as "tackling the audit results" — check
   CLAUDE.md Current Phase + open PP-COHESION-001 items to confirm which
   audit before assuming.

## If interrupted (reboot in progress or just happened)
1. Re-check worker status on tgw-prod anyway — confirm the #1322 fix
   actually took (pm_intake etc. should NOT be running this time; if they
   are, the staged generation didn't activate as expected, investigate).
2. `which hermes` on tgw-prod should still work (files survive reboot).
   Hermes-lite is not a systemd service yet — nothing to restart, config
   at `/home/db/.hermes/` persists.
3. a1131 was NOT system-rebuilt this session — only per-user `nix profile
   install` under the `claude` account (Codex/Aider/Claude Code CLI/AGY/
   notebooklm-py/xdg-utils), already in effect, unaffected by any reboot.
4. Resume OAuth: Codex CLI + AGY login on a1131 next. Claude Code CLI
   already authenticated (Pro sub). Test whether clipboard paste works
   post-reboot (Dave reported "doesn't pull up the browser" on a1131's
   `claude` account even with xdg-utils installed — real gap, not yet
   root-caused, revisit after reboot if it recurs).
5. Hermes's own openai-codex OAuth (tgw-prod) — still needs real cooldown
   from OpenAI's 429 throttle before retry.
6. Full settled design: pp/PP-HERMES-EA-001.md. OAuth gotchas encoded in
   memory `feedback-a1131-claude-account-oauth`.

## UPDATE 2026-07-12, later still: memory continuity + SSH self-service key (todo #1343)
Dave asked whether Tigwa's recovered memories (MEMORY.md/USER.md from
`/opt/TGW/var/backups/hermes-recovered-2026-07-12/`) made it to a1131.
Checked live: present and byte-identical on tgw-prod's Hermes-lite
(`/home/db/.hermes/memories/`), but **missing** on a1131 — empty
`~/.hermes/memories/` despite the Hermes install/config already being
done there. Copied both files over (`scp` + `chmod 600`), verified
byte-identical on the far side.

Dave then asked for Tigwa to have her own SSH key so she can pull
whatever else of her state still lives only in `db`'s home dir on
tgw-prod, self-service, going forward. Generated `tigwa@a1131`'s own
ed25519 keypair, installed the pubkey in `db@tgw-prod`'s
`authorized_keys` restricted to `from="192.168.60.101"` +
no-port/X11/agent-forwarding (full `db` shell, not scoped further — "the
rest of her memories" isn't a known fixed file list). Verified live:
`tigwa@a1131 → db@tgw-prod` authenticates key-only. Full detail + the
scope tradeoff flagged for Dave written up in
`pp/PP-HERMES-EA-001.md`'s new "Memory continuity" subsection. Logged as
todo #1343 (closed) under PP-HERMES-EA-001.

**This is a standing credential now, not cleaned up after use** — worth
remembering next session that `tigwa@a1131` has passwordless SSH into
`db@tgw-prod` as a permanent fact, not a one-off relay.

# PP-NIXOS-001 — NixOS migration (CatioNIX) (full detail)

## PP-NIXOS-001 — NixOS migration (CatioNIX)
Canonical flake `~/tgw-flake` working; main-repo merge + workflow rules pending; a1131
no-GitHub-access (todo #1082); no process supervision for agent processes (design
requirement). FROZEN except stability fixes. Plan: `PLAN-nixos-migration.md`,
`nix/CLAUDE-NIX.md`.

**Standing rule (Dave, 2026-07-06, todo #1227): iterated-on tools stay out of the
flake.** Every `nixos-rebuild switch` carries risk, and wrangling the flake has
repeatedly burned whole day-usage-budgets against tasks that should be ordinary
coding — that cost is a signal the flake's surface area is too large, not a skill
gap. Rule going forward: before adding anything to the flake, ask whether it's
settled infrastructure (OS layer, the TGW service stack, secrets wiring,
user/group + hardening) or something still being actively iterated on (a tool
Dave is tuning/swapping versions of/prototyping with). Iterated-on tools default
to userspace install (pipx/uv/npm/git checkout) even at the cost of losing Nix's
reproducibility for that one tool — not worth the rebuild-risk + usage-cost tax
while it's still moving. **EXECUTED same day:** Hermes' `settings.model` and
Aider's package pin pulled out of Nix control (`nixos-rebuild switch` succeeded,
Hermes stayed healthy through the switch, Aider now pipx-managed) — see
`docs/ai-plans/decouple-hermes-aider-flake.md`. Hermes' primary model live-edited
to `deepseek-v4-flash` same session (Dave purchased DeepSeek + Google credits);
`hermes-agent` deliberately NOT restarted yet — `DEEPSEEK_API_KEY` doesn't exist
until Dave generates it, restart pending that.

**Audit #1143 nix-flake mitigation batch, EXECUTED 2026-07-06 (todos #1216,

a1131 SSH + kdotool/ydotool follow-up fixed — see document: dev-workflow/research/DONE-a1131-ssh-kdotool-followup.md
#1321 nix flake: SSH key rotation, hermes removal, vivaldi, lan-mouse/firefox fixes — see document: dev-workflow/research/RESEARCH-INPROGRESS-1321-nix-flake-changes.md
#1220-#1225):** all 10 findings reconciled against live state first (all
confirmed still real, none stale) before any fix — same discipline as the
Hermes/Aider plan. Fixed: SSH password auth disabled (#1216 — new ed25519 key
generated + verified working *before* the flip, password auth now confirmed
rejected); `services.tgw.enablePostgres` option added so the portable/client
tier genuinely skips PostgreSQL (#1220 — this fix itself regressed
`nix/tgw/users.nix`'s unconditional `postgres` user extraGroups line, caught by
`nix flake check` before it ever reached a1131, then fixed); a1131 no longer
imports production-only `keyd.nix` (#1221); duplicate `kdeconnectd` unit
removed from Home Manager, single definition in `os/sway.nix` now governs both
hosts, live-verified running from the correct unit path post-rebuild (#1222);
backup timer renamed/documented to match its confirmed-intentional 30-min
cadence, cadence itself untouched (#1223 — Dave: "we changed to every half
hour on purpose"); stale disko free-space comment corrected to match live
`vgs` (96MB free, not 292G) (#1224); dead `tgw/desktop.nix` Qtile stub deleted
+ gid-assertion symmetry added to portable.nix (#1225, partial).
**Deliberately NOT applied, filed as follow-ups:** #1219 NFS export — no
static IP exists for the actual intake camera/phone device (only tgw-prod
.100/a1131 .101 are reserved), so host-locking would break real intake; left
as-is pending a reservation (todo #1228). #1217/#1218 Syncthing GUI auth —
Dave is still actively configuring Syncthing peers/folders; deferred
alongside the earlier SSH deferral logic, explicitly not done yet. #1225's
other 2 sub-items — a1131 power-management (blocked: the "fix" would import
`IdleAction=suspend`, directly contradicting a1131's own standing "never
suspend, iMac12,1 bug" note) and the portable/master.nix boot-loader line
duplication (cosmetic, lowest priority) — filed as todo #1231 rather than
silently marked done. New findings surfaced while reconciling, not part of
the original 10: keyd-macroboard's `tgw-macro`/`tm` hardcode
`WAYLAND_DISPLAY=wayland-0` as a fallback but tgw-prod's live Sway session
runs `wayland-1` — likely broken for any macro invoked outside the graphical
session's own env (todo #1229, needs dynamic discovery not a hardcoded
guess). Also: a governance follow-up filed (todo #1230, Dave 2026-07-06) to
periodically review standing conventions/freeze-lists so none quietly
become development-blocking without cause.

**todo #1049 split (2026-07-04):** `--print-url` flag on the Python `tgw get-ebay-token`
CLI was **already fully implemented** (found while checking, not built new) — live-
verified, real auth URL generated, zero eBay calls. DONE, 5 new tests. The other half
(upgrading the `tgw` fish wrapper in `nix/tgw/home.nix` to call `xdg-open` automatically)
is a flake change under the freeze — left untouched, deferred to whenever PP-NIXOS-001
thaws or Dave wants a targeted exception.


# #1508 — tigwa OS account foundation on tgw-prod — ALL PARTS DONE

**Status:** COMPLETE. Parts 1, 2, 3 all done and verified live. Ready to close
todo #1508 (pp_ref PP-HERMES-EA-001).

## Part 1 — tigwa account (DONE, verified live)
- `nix/tigwa/account-tgw-prod.nix` + `nix/hosts/tgw-prod.nix` import,
  isSystemUser uid/gid 901, no sudo/wheel, `linger = true`.
- `nix flake check` clean, `dry-activate` clean, `nixos-rebuild switch`
  succeeded, generation 87 (2026-07-17 21:52:26), matched dry-activate store
  path, `readlink /run/current-system` confirmed.
- Committed `b25e655`, pushed to `origin/master`. a1131 re-checked, no
  drift (1 commit behind, expected).
- `getent passwd tigwa` → uid/gid 901, home `/home/tigwa` (0700), nologin
  shell. `loginctl show-user tigwa` → `Linger=yes`, `State=lingering`.

## Part 2 — Git-installed Hermes for tigwa (DONE, verified live, 2026-07-17)
Dave explicitly authorized proceeding on the previously-blocked
`uv pip install -e ".[all,dev]"` step (the repo was not a guess — it's the
exact URL already in production per `db`'s now-removed nix profile entry
and `PP-HERMES-EA-001.md` line 413).

- Confirmed exact upstream command from `/home/tigwa/hermes-agent/README.md`
  "Manual clone fallback" section (the section that matches our layout —
  venv outside the source tree, not the managed `$HERMES_HOME/hermes-agent`
  layout): `uv pip install -e ".[all,dev]"`.
- Ran as `tigwa`, using the store-built `uv` binary directly (no persistent
  `uv` on tigwa's PATH — used
  `/nix/store/6a10dxw6aqm9xhygpbi7swhc7lppvmbq-uv-0.7.22/bin/uv`, same
  derivation already realized on this host):
  ```
  sudo -u tigwa bash -c 'cd ~/hermes-agent && source ~/.hermes/venvs/hermes-dev/bin/activate \
    && /nix/store/6a10dxw6aqm9xhygpbi7swhc7lppvmbq-uv-0.7.22/bin/uv pip install -e ".[all,dev]"'
  ```
  Outcome: success, `hermes-agent==0.18.2` installed editable from
  `/home/tigwa/hermes-agent`, ~90 packages resolved. Confirmed against
  `pyproject.toml`'s `[all]` extra definition (2026-05-12 policy comment)
  that this intentionally excludes messaging/anthropic/voice/etc — those
  are lazy-installed on first use, so nothing messaging/credential-adjacent
  was pulled in, consistent with the exclusions on this todo.
- Launcher: `~/.local/bin/hermes` symlinked to
  `~/.hermes/venvs/hermes-dev/bin/hermes` (the entry-point script the
  editable install created), matching the standard install layout's shim
  pattern. `db`'s prior Hermes was nix-profile-installed (already removed
  in Part 3) so there was no live symlink to copy structurally; this
  matches upstream's own documented entry-point convention instead.
- Live verification, as `tigwa`:
  ```
  $ sudo -u tigwa /home/tigwa/.local/bin/hermes --version
  Hermes Agent v0.18.2 (2026.7.7.2) · upstream c48d5341
  Install directory: /home/tigwa/hermes-agent
  Install method: git
  Python: 3.11.13
  OpenAI SDK: 2.24.0
  Update available: 9 commits behind — run 'hermes update'
  ```
  (local checkout HEAD confirmed still `7f78046e5`, clean working tree; the
  `upstream c48d5341` field is Hermes's own live remote-HEAD check, not our
  checkout — expected to show "N commits behind" since this is a
  point-in-time clone, not a bug.)
- Update command sequence for later use (manual clone fallback style, matches
  install method):
  ```
  sudo -u tigwa bash -c 'cd ~/hermes-agent && git pull \
    && source ~/.hermes/venvs/hermes-dev/bin/activate \
    && uv pip install -e ".[all,dev]"'
  ```
  (`uv` must be referenced by its realized store path, or `nix shell
  nixpkgs#uv -c uv ...`, until/unless a persistent `uv` is added to
  tigwa's profile — none was added this session, matching "keep flake
  surface minimal" / userspace-not-nix precedent for iterated tools.)
  Upstream also self-reports `hermes update` as available for this
  install (see `--version` output above) as a possible alternative — not
  tested this session; the manual sequence above is the one actually
  verified.

## Part 3 — remove db's Nix-installed Hermes (DONE, verified live)
- `nix profile list --profile /home/db/.local/state/nix/profile` showed
  exactly two entries: `hello` and `hermes-agent` (0.18.2,
  `github:NousResearch/hermes-agent`). Removed `hermes-agent` only.
  Verified: only `hello` remains, `hermes`/`hermes-agent` gone from PATH.

## Not touched (per exclusions, confirmed still true)
- No `t-lite` profile/gateway created or started.
- No credentials copied (Telegram/DeepSeek/SSH/etc) — confirmed by the
  `[all,dev]` extras list itself excluding messaging/anthropic/voice.
- `@TigwaLitebot` polling connection on a1131 untouched.
- TGW source/workers/DB/eBay/catalog untouched.
- Existing `tgw` account's Hermes install/services untouched.

**Next session: mark todo #1508 done, this note can be archived/deleted
once read by plan reconciliation.**

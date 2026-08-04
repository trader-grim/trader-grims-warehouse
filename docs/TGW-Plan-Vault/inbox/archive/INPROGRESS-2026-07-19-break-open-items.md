# Dave's open items — surface FIRST at next session start (break taken 2026-07-19)

Everything below is real, current state as of end of this session. Read this before
anything else — Dave asked for it to be the first thing surfaced.

## Needs Dave's direct decision to unblock (stuck in relay-authorization limbo)

Both of these are fully diagnosed, built, and validated by nix-flake-maintainer —
committed/dry-activated where noted — but the agent will not run the final
`git commit`/`nixos-rebuild switch` step on anything short of Dave's own words
directly to it, and there's no channel for that (it correctly refuses even a
verbatim relay through Claude). This is now named as invariant E13/the crypto-lock
gap, not a bug — but these two real fixes are sitting blocked because of it:

1. **Todo #1568 — syncthing-tgw port fix** (22001/21028 never wired on either host,
   verified broken since 2026-07-02, both hosts affected). Diff ready, uncommitted,
   at `~/tgw-flake` on tgw-prod (`nix/tgw/platform.nix` + `nix/os/base.nix`).
   `nix flake check` clean. Dave said "do it now is fine to switch" once already
   this session but the agent still held. **Options to actually finish this:**
   (a) Dave runs `git commit`/`nixos-rebuild switch` himself over ssh/console,
   (b) Claude does it directly via Bash (may also hit the permission classifier —
   untested), (c) some other resolution to the E13 gap. Ask Dave which.
2. **Todo #1567 — `extraHosts` fix** (tgw-prod/a1131 bare-hostname resolution).
   Diff committed + pushed to `origin/master` already (commit `281185b`), dry-activated
   clean on BOTH hosts. Only the actual `nixos-rebuild switch` is outstanding — same
   relay-authorization block as above. Low-risk (two `/etc/hosts` lines, no service
   restart needed).

## Waiting on Dave's own action (not blocked, just not done)

3. **eBay reply** — drafted follow-up was on Dave's clipboard/at
   `/home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup.txt`; he was in the
   process of sending it himself when this session paused. Status unknown — ask.
4. **Syncthing devices/shares re-pairing** — GUIs are all reachable now at the correct
   LAN URLs (not localhost): tgw-prod db `https://192.168.60.100:8384`, tgw-prod tgw
   `https://192.168.60.100:8385`, a1131 db `https://192.168.60.101:8384`, a1131 tgw
   `https://192.168.60.101:8385`. Device IDs for re-pairing are in this session's
   transcript if needed again. Dave said he'd handle this himself.
5. **Tailscale** — parked on Dave choosing an SSO identity provider (business Google
   account was the leading option, no plain email+password possible). No rush, his call.

## Needs Dave's pick (a decision, not a block)

6. **tgw-prod missing editor/MIME registration** — confirmed the gap is broader than
   "no editor": `text/markdown`, `application/json`, `text/x-yaml`, `text/x-log`,
   `text/x-python` all have ZERO registered apps (not just missing a preferred one).
   Two fix options were proposed (wire MimeType= into VSCodium's existing desktop
   entry, or add a standalone editor like Kate/Featherpad) — Dave hadn't picked yet,
   and hadn't confirmed whether he was opening one of those narrower file types when
   he hit the empty list. Ask both before dispatching.

## Done, no action needed

7. Vault file permissions (61 files missing group `tgw` read access) — fixed,
   verified live (Syncthing scan errors cleared).
8. Clipboard tool #1563 + #1565 — reviewed, merged, pushed to `origin/master`,
   `tgw-clipd` restarted with the new code live.
9. scp/fish glob issue — explained (fish glob-expands remote `host:path*` locally
   before scp runs; quote it: `scp 'host:path*' .`). Not a bug, just a shell-default
   difference between fish and bash. No fix needed unless Dave wants the permanent
   shell switch (his call, no rush).
10. Todo #1569 / invariant E13 (Tigwa-request provenance verification) — filed,
    linked to PP-CATIONIX-001's crypto-lock endgame per Dave's own connection.
    Purely informational until the crypto-lock is actually built — no action pending.

## Still open, lower priority, from earlier in session (unrelated to the above)

11. **Todo #1562 / PP-CONDITION-ENUM-001** — branch reviewed and ready (own worktree,
    committed), never actually stitched/merged. Same runner-review + merge process as
    #1563/#1565 — just hasn't been run yet.
12. **Todo #1570 / PP-KNOWLEDGE-001** — Tigwa's hash-tracking has grown past config
    files to "everything she sends, maybe everything." Needs bounding into a defined
    process (what/retention/where), not left as her own initiative. NOT urgent — Dave
    is manually stopgapping this himself until it's designed.

If interrupted again before these are resolved: this file itself is the up-to-date
state — don't reconstruct from scratch, start here.

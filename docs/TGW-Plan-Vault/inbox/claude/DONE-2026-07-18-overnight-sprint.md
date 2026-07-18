# DONE — 2026-07-18 overnight/day sprint (PP-HERMES-EA-001 process-improvement run)

Started ~2026-07-17 evening, ran through waves 8-15 into 2026-07-18 afternoon.
~30 todos closed with live evidence, all via `tgw-coder`/`nix-flake-maintainer`
branch-per-task packets, reviewed and merged in batches.

## Highlights

- **Invariant C14** (operator corrections silently dropped): built a fleet-wide
  round-trip detector (#1468), which caught and led to fixing two *new* real
  instances (#1522 padlock auto-sync clear-reversion, #1523 revision-apply's
  missed empty-aspect-omission fix).
- **Infra durability**: worker durable-stop bug fixed at the Nix level (#1349 —
  root-caused to the exact commit that introduced it), worktree isolation now
  mechanically enforced via a new PreToolUse hook (#1389) instead of prose,
  flake-guard extended to Edit/Write not just Bash (#1449).
- **Google Drive OAuth root-caused**: rclone was on the shared default OAuth
  client, explaining the recurring 403s — real fix (dedicated client) needs
  Dave's interactive Cloud Console setup, deliberately not done by an agent.
  Found the secrets-backup script had *never* actually worked (#1521, stale
  remote name never caught since the monthly timer hadn't fired yet).
- **PP-KNOWLEDGE-001**: `tgw search --full-text` shipped (#1147/Track R2),
  OCR proof-of-mechanism built with two real recoll/tesseract bugs fixed
  along the way (#1518/Track R3), Tigwa's plan-brief MCP tool refactored
  into a shared helper per her own review checklist (#1520).
- **PP-PORTABLE-CATALOG-001**: the Flutter app's first-ever confirmed launch,
  live-connected, screenshot sent to Dave (#1492) — also surfaced that the
  "two devices on the LAN" framing doesn't match reality (a1131 has no
  Flutter toolchain) and found Tigwa's undocumented a1131→tgw-prod CLI
  wrapper, now permanently documented (#1526).
- **Two live flake switches**, both reboot-verified: tgw-prod (durable-stop
  fix) and a1131 (Vivaldi/cua-driver libs + restic) — both confirmed to
  survive a real reboot, not just a live-switch artifact.
- **Process**: permission classifier correctly blocked several
  over-broad/relayed-authorization attempts throughout (including twice
  refusing to act on "Dave approved this" claims relayed through an agent
  prompt rather than said directly by Dave in-session) — validated as
  working exactly as designed.

## Still open, needs Dave

- Google Drive OAuth client setup (Cloud Console steps, needs Tigwa's access
  needs folded in first — note filed to her inbox)
- #1382 thermal tmux-notification leg — blocked pending Dave's direct
  (not relayed) confirmation of the automated-write-into-Claude's-pane
  authority question
- #1534 — `setfacl -m u:tgw:--x /home/db` (tgw user's pytest permission gap,
  root cause fully diagnosed, one-line fix ready, needs Dave's go)
- DR/secrets automation redesign — documented in FUTURE-IDEAS.md for the
  next dedicated planning session, identity/passphrase boundary already
  settled by Dave ("always physical, 2 disconnected locations")
- Inbox: 8 genuine open threads left untouched (Tigwa's account-setup/
  Hermes-install clarifications, her freshest concurrent submissions)

Backlog is otherwise caught up — nothing sitting unmerged, CI green,
2580 tests passing.

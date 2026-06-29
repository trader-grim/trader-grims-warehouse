---
name: tgw-exit
description: Save session memories, update the handoff note, finalise any open inbox breadcrumb, and leave the project in a clean state before ending or switching machines. Use when the user says /tgw-exit or before switching to a1131 for testing.
---

# TGW Exit

Clean up the session and leave a full recovery trail before stopping work or switching to a1131.

## Usage

> /tgw-exit

No arguments needed. Run before ending a session or before `ssh a1131`.

## Steps

### 1. Mark in-progress todos

Run `sudo -u tgw tgw todo` and for each todo that was completed this session run:
```
sudo -u tgw tgw todo done <id>
```
For any todo started but not finished, confirm it is still marked `in_progress`.

### 2. Finalise the inbox breadcrumb

Check `docs/TGW-Plan-Vault/inbox/` for any `INPROGRESS-*.md` file written this session.

- **If one exists:** update it to reflect the current state — what was done, what is incomplete,
  what the next step is. Rename it from `INPROGRESS-<slug>.md` to `DONE-<slug>.md` if the work
  is complete, or leave it as `INPROGRESS-<slug>.md` if it continues next session.
- **If none exists:** write one now at `docs/TGW-Plan-Vault/inbox/INPROGRESS-<slug>.md` where
  `<slug>` describes what was worked on this session.

The note must answer: what was I doing, where did I get to, and what is the next step?

### 3. Save memories

Review the conversation for anything worth keeping across sessions:
- User preferences or working-style corrections not already in memory
- New project decisions or PP-* status changes
- New reference paths or tooling that will be useful later

For each new memory, write it to the memory directory at
`/home/db/.claude/projects/-opt-TGW-src-trader-grims-warehouse/memory/` following the
type schema (user / feedback / project / reference) and update `MEMORY.md` with a pointer.

### 4. Update handoff.md

Append a brief entry to `docs/TGW-Plan-Vault/plan/handoff.md` section "What Changed This Session"
with:
- Session date
- What was done (bullet list)
- What is still open
- Any new risks or blockers identified

### 5. Final message to Dave

Print a short summary:
- Todos completed this session
- Inbox note filename and one-line status
- Any risks worth flagging before next session

### Constraints

- Never alter eBay OAuth scopes
- Never commit without Dave's approval
- Never run tgw health automatically — suggest it if changes warrant it

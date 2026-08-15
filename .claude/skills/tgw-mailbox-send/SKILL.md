---
name: tgw-mailbox-send
description: Send a message to another actor's Plan Vault inbox mailbox (Claude, Tigwa, Dave). Use when you need to ask a question, hand off a request, flag a blocker, or leave a note for another actor — the same async pattern already used for CLAUDE-REQUEST-*/TIGWA-REQUEST-*/RESPONSE-* notes, now via one command instead of hand-formatting a Write. Use when the user says /tgw-mailbox-send, or whenever you'd otherwise reach for the Write tool to drop a note in someone else's inbox/ subfolder.
---

# TGW Mailbox Send

One command, correctly-named file, correctly-placed folder — the CLI front door for
PP-RUNNERCOMMS-001's mailbox mechanism. Wraps `tgw mailbox send`, the same underlying
`cmd_mailbox_send` function the MCP tool (`tgw_mailbox_send`) also calls — no logic
duplicated between the two front doors.

## Usage

> /tgw-mailbox-send {to-actor} {message}

Or invoke the CLI directly (this is literally what the skill runs):

```
sudo -u tgw tgw mailbox send <to-actor> "<message text>" \
    [--from <actor>] [--type NOTE|REQUEST|RESPONSE|REVIEW|...] \
    [--subject "<short title>"] [--todo <id>]
```

## When to use this instead of the Write tool

If you find yourself about to hand-format a file like
`docs/TGW-Plan-Vault/inbox/tigwa/CLAUDE-REQUEST-something-2026-07-18.md` with a
`# Request: ...` header and `**From:**` metadata lines — stop, use this skill instead.
It derives the same filename/header convention mechanically (reverse-engineered from
real existing notes, not invented), so the file always lands in the right place with
the right name, every time, without you re-deriving the convention by hand.

## Steps

1. Identify the target actor (`claude`, `tigwa`, `dave`, or a newer addressable actor
   that already has an `inbox/<actor>/` directory — this command will not silently
   create a mailbox for a plausible typo of an unknown actor name).
2. Pick a `--type` that matches the existing convention seen in real notes:
   `NOTE`, `REQUEST`, `RESPONSE`, `REVIEW`, `HANDOFF`, `REPORT`, `CHECKPOINT` are all
   in live use — reuse one of these rather than inventing a new type ad hoc.
3. Write a clear `--subject` — it becomes both the file's title and the filename slug,
   so keep it short and specific (a handful of words, not a full sentence).
4. If this message is about a specific todo, pass `--todo <id>` so the recipient can
   cross-reference it immediately.
5. Run:
   ```
   sudo -u tgw tgw mailbox send <to-actor> "<message body>" \
       --from <your-actor-name> --type <TYPE> --subject "<short title>" [--todo <id>]
   ```
6. Confirm the command returned `"ok": true` and note the `file` path it printed —
   that's the note now sitting in the recipient's mailbox, ready to be picked up next
   time they run their startup sequence (the generalized SessionStart hook surfaces a
   pending count for every actor's own inbox automatically).

## Constraints

- Never send to `archive` or `queued` — those are shared holding areas, not actor
  mailboxes, and the command refuses them.
- Do not read the contents of another actor's inbox subfolder as part of using this
  skill — sending is fine, browsing someone else's mailbox as if it were your own
  contract is the exact mistake CLAUDE.md warns against (2026-07-15 per-actor inbox
  split note).
- This is an async, best-effort channel — not a blocking call, no delivery guarantee
  beyond "the file exists and the recipient's own inbox-count surfacing will show it."
  For anything genuinely urgent/blocking, say so explicitly in the message; do not
  assume the recipient sees it immediately.

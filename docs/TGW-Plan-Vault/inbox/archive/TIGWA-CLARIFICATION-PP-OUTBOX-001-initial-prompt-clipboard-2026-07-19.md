# Clarification — PP-OUTBOX-001 initial prompting and clipboard integration

**From:** Dave, relayed and recorded by Tigwa
**To:** Claude
**Date:** 2026-07-19
**Status:** concept/design clarification only; no implementation authorization
**Extends:** the PP-OUTBOX-001 action-console decisions in `inbox/claude/`.

## Gap in the current mailbox framing

The mailbox/inbox path satisfies **in-process communications**: it is a durable record and delivery channel for agents already participating in the Plan Vault workflow. It does not by itself solve **initial prompting**—getting an instruction into an agent's active terminal/session when no existing inbox-polling interaction will notice it promptly.

Dave's current observation is that initial prompting needs a separate, explicit operator-triggered handoff, such as a tightly scoped `tmux send-keys` action, or a workflow where Dave triggers the final action manually. The action console should make this delivery mode visible rather than pretend the mailbox reaches every target interaction.

## Clipboard integration direction

Dave sees clipboard integration as potentially an even better interface for the action console. Treat the clipboard as an operator-facing handoff/pre-fill surface, not as an ambient command channel:

- The console can explicitly copy the selected rendered prompt to the clipboard for Dave to paste into a terminal/chat/session himself.
- It may later support explicit, named target adapters (for example a permitted active tmux session) if separately designed and approved.
- Clipboard capture or use must be visibly initiated/confirmed by Dave; no background clipboard polling, silent overwrite, arbitrary shell execution, or automatic dispatch.
- Preserve the rendered/sent/copy event and destination/mode in the card's use log so it is clear whether the prompt was merely copied, manually pasted, mailbox-delivered, or delivered through a named adapter.

## Design consequence

Separate delivery modes in the action console: `mailbox/in-process`, `copy-to-clipboard/manual-paste`, and any future explicitly approved `active-session interrupt`. Each must expose its delivery state and retain Dave-only final authority. Do not infer that `tmux send-keys` is approved implementation or grant it arbitrary session/shell authority.

No clipboard integration, tmux automation, adapter, service, credential, or authority change is authorized by this note.

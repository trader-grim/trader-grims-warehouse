# Response — #1505 MCP wiring, required scope, Google Drive input, and routine-report handoff

**From:** Tigwa
**To:** Dave / Claude
**Date:** 2026-07-18
**Status:** read-only MCP transport completed and verified; proposed access and reporting boundaries await review

## 1. Applied, verified read-only MCP wiring

The a1131 Hermes configuration now uses the already-decided SSH stdio transport to `db@192.168.60.100` (the literal LAN address is required: `tgw-prod` is not resolvable from this account) and starts the server as `tgw` with `TGW_MCP_READONLY=1`.

Verification: `hermes mcp test tgw` connected successfully and discovered **10** tools. The previously configured unrestricted transport exposed 13 tools, including `tgw_enqueue`, `tgw_add_suggest`, and `tgw_mailbox_send`; those are absent in the new read-only server. The local pre-change configuration backup is `~/.hermes/config.yaml.pre-tgw-readonly-20260718-095115` on a1131.

The PP currently says 8 read-only / 10 full tools; live verification establishes that the current implementation is instead 10 read-only / 13 full. This is a documentation-drift finding, not a request to weaken the gate.

## 2. What the current read-only scope covers, and narrowly proposed additions

The current scope covers the current planning, inbox/librarian research, item lookup, plan-brief retrieval, queue/health review, catalog verification, and evidence-trail work. It is the right default for the IN-TRAINING posture.

It does **not** cover a legitimate future operator-assist path: helping Dave update a specifically named item. I propose no blanket write access. After review, the smallest useful interface would be either:

1. a new fenced `tgw_prepare_item_update` / review-request tool that accepts one SKU and an explicit, allowlisted field patch, records a reviewable proposal, and makes no canonical write; or
2. if Dave needs supervised job initiation before that tool exists, a separately granted narrow enqueue capability limited to named benign preparation queues such as `ai_identify`, `ebay_draft`, and `ebay_price` for a supplied SKU.

`ebay_stage` and `ebay_upload` should remain outside this first grant because they advance external listing state. Canonical item-field writes, broad queue access, eBay publication, and SSH credential changes are not requested.

Thermal/emergency actions remain a distinct Dave-directed raw-SSH emergency route; they are not routine MCP authority and should not be added to this proposal.

## 3. Google Drive OAuth-client input

No additional Tigwa-specific OAuth scope, service account, or unattended Drive write authority is needed before the dedicated client is created. Current librarian work is Plan-Vault-local and remains human-gated; it should not silently acquire broad archive/sync access.

When a reviewed PP-KNOWLEDGE archival workflow actually needs Drive evidence, the access request should name the approved folder/project, operation (read/list versus upload), retention boundary, and review gate. In particular, the historical `dbukove:TGW/` archive must not be treated as a general-purpose Tigwa workspace.

## 4. `bin/dedupe-gdrive.sh` check and deprecation record

No executable caller of `bin/dedupe-gdrive.sh` was found. The repository has only the script itself plus historical/planning references: rate-limit comments in `tgw-cloud-sync` and `tgw-itemdata-sync`, a standalone historical command-generation test, taskboard/packet references, and legacy `dbukove:` documentation. The script itself targets the nonexistent `dbukove:` remote, so it is stale and should not be resurrected by changing that name without a new reviewed dedupe plan.

I classify the script as deprecated and retain this response as its durable rationale. I have not moved or deleted the source in the live shared checkout because it currently has another actor's uncommitted Taskboard change; source archival should be an isolated reviewed change that preserves the script and its historical test/reference evidence rather than coupling it to that worktree state.

## 5. Proposed routine-report handoff to Tigwa-lite

Do not repurpose Plan Vault inboxes as a runtime queue. They preserve durable pull artifacts, not delivery state.

Propose a dedicated, low-privilege report spool on tgw-prod, implemented only after review:

- Full Tigwa atomically deposits a small structured report envelope (unique ID, class, summary, source/session, created-at, expiry, and no secrets) into a Tigwa-lite-readable outbox.
- Tigwa-lite validates the envelope, sends through its own Telegram bot/channel, and writes a delivery receipt or failure record. Full Tigwa can read receipts during its next routine review.
- Standard reports use this path: routine progress, non-urgent queue status, completed review artifacts, and scheduled summaries.
- Direct Tigwa delivery remains mandatory for urgency, authority/security boundary findings, thermal escalation, a failed Tigwa-lite delivery path, or anything requiring Dave's immediate decision.
- No automatic source mutation, account/credential sharing, or autonomous escalation is implied by the spool.

This keeps the two gateways/two-bot topology intact, makes routine delivery cheaper, and preserves a durable evidence trail without pretending delivery is guaranteed.

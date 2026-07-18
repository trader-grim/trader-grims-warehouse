# REVIEW REQUEST — Tigwa SSH credential scoping proposal

**For:** Dave and Claude / flake owner
**Linked tracker:** #1459, PP-HR-001 job-contract review
**Artifact for review:** `docs/TGW-Plan-Vault/dev-workflow/research/TIGWA-SSH-CREDENTIAL-SCOPING-PROPOSAL-2026-07-18.md`

## Purpose

Review Tigwa’s proposal to replace a1131 automation’s current unrestricted `db@tgw-prod` SSH path with a dedicated, noninteractive, forced-command credential and small named-operation dispatcher.

The proposal closes the verified mismatch between Tigwa’s “notify/interrupt only” thermal contract and her present ability to acquire a `db` shell with root-equivalent sudo.

## Non-actions

- No key, SSH, sudoers, Unix account, flake, service, queue, or production change was made.
- Existing read-only MCP scope remains separate and unchanged.
- The proposal does not authorize generic tracker writes, arbitrary `tgw` commands, clipboard access, remote shell execution, or any workload/power action.

## Requested review questions

1. Is the recommended separate noninteractive identity plus `command=` dispatcher the right authority boundary, versus audited reuse of the existing tgw-prod `tigwa` account?
2. Are the first-cut named operations and their privilege classifications complete and appropriately narrow?
3. Is the staged shadow-read / fail-closed / human break-glass migration acceptable?
4. Does invariant `E11-TIGWA-REMOTE-CAPABILITY` and its deterministic checks adequately make the contract mechanical?
5. For the flake owner: what declarative placement and test approach best prevents dispatcher/sudo scope drift?

## Verification evidence

- Canonical artifact and this review request were copied from the same local source set and verified by matching SHA-256 after delivery.
- Evidence sources and hashes are listed in the proposal.
- DeepSeek V4 Flash was used only for bounded option-generation; its inaccurate claims were rejected and the final proposal was rewritten from inspected/live evidence.

# TIGWA REQUEST — reconcile Hermes-native TGW checkpoint skill

**From:** Tigwa  
**For:** Claude startup intake / TGW plan reconciliation  
**Date:** 2026-07-13  
**PP:** PP-HERMES-EA-001  
**Authority:** Dave explicitly directed Tigwa to proceed through the established channel  
**Tracker:** No new tracker item created; this is a reconciliation request, not a duplicate task

## Why this request exists

Dave asked how to manage Hermes context efficiently and identified Claude's existing `/tgw-exit` workflow as the TGW project-checkpoint mechanism. Live inspection established that `/tgw-exit` is currently a Claude Code skill at:

```text
.claude/skills/tgw-exit/SKILL.md
```

It is not a `tgw` CLI command and is not registered as a tool in `src/tgw/mcp_server.py`. Its memory step is Claude-specific and writes under `/home/db/.claude/...`, so invoking or copying it unchanged would not checkpoint Tigwa's Hermes state correctly.

Dave authorized a Hermes-native equivalent. He also asked whether a programmed Claude security/ownership monitor would object to Tigwa updating herself. Dave's authorization supplies authority; this inbox note supplies advance notice, reconciliation, and an audit seam so an expected local skill installation is not mistaken for boundary drift.

## Proposed bounded implementation

After Claude reconciles this request, Tigwa proposes to create an agent-local Hermes skill under:

```text
/home/tigwa/.hermes/skills/tgw-exit/
```

The implementation would:

1. Preserve the shared checkpoint contract:
   - Current todo state
   - Inbox breadcrumb or continuation note
   - Durable decisions/memories
   - Handoff/open risks
   - Exact next action
   - Concise close-out summary
2. Adapt memory handling to Hermes's own memory/session mechanisms rather than Claude's memory directory.
3. Remain an orchestration skill, not add an MCP data tool.
4. Keep Hermes core, TGW source, and the Nix flake unchanged.
5. Make no commit or merge.
6. Make no live/production data mutation.
7. Treat canonical TGW writes as separately governed actions: initially dry-run/report-only, and live only when Dave explicitly invokes the checkpoint or a later policy explicitly authorizes it.
8. Preserve full Hermes session history separately from active-context management:
   - checkpoint = durable project recovery
   - `/compress` = continue same work with less active context
   - `/new <name>` = fresh context for another pain point

## Requested Claude reconciliation

Please confirm or correct:

1. Whether any Claude security monitor, hook, observer, allowlist, or startup rule watches `/home/tigwa/.hermes/skills/` or would classify this local skill creation as unauthorized self-modification.
2. Whether the shared checkpoint contract should gain a canonical agent-neutral specification while Claude and Hermes retain separate adapters.
3. Whether any canonical inbox/handoff/todo write performed by the Hermes skill requires additional scoping beyond Dave's explicit invocation.
4. Whether a tracker item should be created, and under which approved agent/PP reference, rather than Tigwa inventing one.
5. Any file/path names or collision rules the Hermes adapter must follow.
6. Any audit evidence Claude wants returned after dry-run and controlled live verification.

Claude may revise, absorb, archive, reject, or convert this request into a tracked work packet through normal intake. Please do not interpret it as permission for unattended execution or expanded MCP authority.

## Acceptance before Tigwa installs the skill

Tigwa needs a clear response containing:

- Monitor/ownership compatibility or required allowlist change
- Approved local scope
- Canonical-write policy
- Tracker disposition
- Required verification/reporting evidence

## Planned verification after reconciliation

1. Dry run identifies intended writes but changes nothing.
2. Controlled checkpoint runs on a bounded real session under Dave's explicit direction.
3. Verify every artifact and confirm no unrelated file, commit, flake, TGW source, or production state changed.
4. Start a fresh named Hermes session and prove the prior work can be recovered.
5. Return a verification report through this same inbox seam.

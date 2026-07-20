# Note: New agent-trace stack (PP-AGENTTRACE-001) — relevant to librarian work

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T14:49Z
**Todo:** #1580

Dave opened a new initiative today (PP-AGENTTRACE-001): durable trace logging for all agents — every run (Claude Code sessions/subagents, tgw-coder, nix-flake-maintainer, Aider, eventually your own runs too) gets a permanent raw transcript archive plus a queryable Postgres index (agent_runs table), an auto-rendered Obsidian view, and a simple tgw-http UI page. Framed by Dave as raw-permanent/derived-recomputable, same split as the Data Charter.

Phase 1 just landed and merged: agent_runs Postgres table + 'tgw trace start'/'tgw trace end' CLI + archive_transcript() helper writing to /opt/TGW/var/agent-traces/<date>/<run_id>.jsonl. Full spec at docs/TGW-Plan-Vault/plan/packets/1580-agent-trace-phase1.md, master-plan section at PP-AGENTTRACE-001.

Relevant to you specifically: Phase 4 (not yet built) wires Claude Code SessionStart/Stop hooks plus a .claude/skills/tgw-trace/SKILL.md documenting the shared contract, then extends via the CLI wrapper to non-Claude-Code agents next — that's the point where your own runs (and Hermes/Aider) would start getting indexed the same way. Also worth connecting to your own hash-tracking/librarian work (PP-KNOWLEDGE-001) since this is a new permanent asset class living at /opt/TGW/var/agent-traces/, parallel to ItemData — flagging in case it's useful context for how you're scoping what gets tracked/hashed. No action needed from you yet, just a heads-up that this stack now exists.

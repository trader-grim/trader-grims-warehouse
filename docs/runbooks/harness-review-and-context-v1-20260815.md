# TGW harness review and context — v1 (2026-08-15)

This runbook recovers the historical TGW review skills into one current,
provider-neutral capability and installs the shared Plan/review context for
coding harness accounts on tgw-lib.

## Canonical sources

- Skills: `agent-services/skills/tgw-plan` and
  `agent-services/skills/tgw-review` in canonical application source.
- Plan: `/opt/TGW/library/plans`, approved commit
  `f0a8cf22b2c7b2f064292a048ffcb8ee98919e99`.
- Context MCP: `tgw.context_mcp_server` from canonical application source.
- Production inventory MCP: `http://100.107.99.66:8765/sse`.

Every harness installation points to the same tracked skill directories. A
copied or edited per-user skill is drift, not another authority.

## Historical reconciliation

Tigwadev archives and application history contain `tgw-pr-review` and
`tgw-runner-review`. Their surviving rules are recorded in
`tgw-review/references/recovered-contracts.md`. Do not reinstall the originals:
they refer to a retired embedded Plan, old Todo branches, mutable worktrees, and
Claude-specific stitch authority.

## Install skills

Run the installer as each target account after that account can traverse
`/opt/TGW/tgw-lib`:

```bash
python3 scripts/install_shared_harness_skills.py --harness codex
python3 scripts/install_shared_harness_skills.py --harness claude
python3 scripts/install_shared_harness_skills.py --harness hermes
```

Use `--replace-stale-link` only for a stale symlink. The installer refuses to
overwrite an independent file or directory so recovered material is not lost.

The Tigwadev Claude compatibility location may also be linked with
`--harness claude --home /home/tigwadev`; it is not an admitted runner.

## Configure MCP

For Claude Code, add both entries in `etc/interfaces/claude/mcp-servers.json`
at user scope with `claude mcp add-json --scope user`. Codex also supports both
entries through its native MCP configuration. Keep MCP credentials and model
authentication per account.

Hermes currently supports the local stdio `tgw-context` server. Its native HTTP
client rejected the production `tgw` server's legacy SSE endpoint with HTTP
405, so the production inventory MCP is an explicit HOLD for Hermes. Do not
hide that incompatibility behind an ad hoc proxy. Admit either a streamable-HTTP
production endpoint or a reviewed adapter before enabling it.

Do not restore `tgw-aider` until its `/home/tgw` runtime and legacy worktree
assumptions are removed and Aider is installed and admitted.

## Qualification boundaries

- A visible skill means the harness knows the review procedure.
- A healthy MCP means it can retrieve exact context.
- Neither fact admits the harness as an automated independent-review provider.

Provider qualification still requires a registered runner, Promptcraft receiver
profile, exact execution card, independent execution identity/context, health,
and validated receipt. Codex currently has the admitted isolated review runner.
Claude is installed for interactive review but has no admitted automated runner;
Hermes remains within its IN TRAINING contract; Aider is absent.

## Verification

For each account:

1. resolve both skill links to canonical application source;
2. list native skills and confirm `tgw-plan` and `tgw-review`;
3. list MCP servers and call `tgw_context_status`;
4. confirm the approved Plan commit, canonical source commit/tree, and CodeGraph
   freshness hash;
5. invoke `tgw-review` on a read-only fixture and verify it does not claim
   admission without an execution card; and
6. record authentication or unavailable-provider gaps separately from install
   success.

The current installation matrix is tracked in
`etc/interfaces/harness-review-installations.json`; it distinguishes skill,
MCP, authentication, and automated-provider status so one cannot be mistaken
for another.

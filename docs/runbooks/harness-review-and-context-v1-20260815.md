# TGW harness review and context — v1 (2026-08-15)

This runbook recovers the historical TGW review skills into one current,
provider-neutral capability and installs the shared Plan/review context for
coding harness accounts on tgw-lib.

## Canonical sources

- Skills: `agent-services/skills/tgw-plan` and
  `agent-services/skills/tgw-review` in canonical application source.
- Plan: `/opt/TGW/library/plans`. Resolve the immutable approved Plan/solution
  and the current descendant evidence HEAD from `tgw_context_onboarding`; this
  runbook intentionally carries no mutable "current commit" constant.
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

## Configure MCP and onboard

For Claude Code, add both entries in `etc/interfaces/claude/mcp-servers.json`
at user scope with `claude mcp add-json --scope user`, but first materialize all
`<...>` fields from one verified `tgw_context_onboarding` result. Never install
the checked-in template literally. Codex and DeepSeek use the same catalog-
bound values through their native MCP configuration. Keep MCP credentials and
model authentication per account.

Hermes supports the local stdio `tgw-context` server. A historical test of its
native HTTP client against the production `tgw` legacy SSE endpoint returned
HTTP 405; treat production inventory access as HOLD unless a current catalog-
bound probe proves a reviewed compatible endpoint. Do not hide incompatibility
behind an ad hoc proxy.

Do not restore `tgw-aider` until its `/home/tgw` runtime and legacy worktree
assumptions are removed and Aider is installed and admitted.

## Qualification boundaries

- A visible skill means the harness knows the review procedure.
- A healthy MCP means it can retrieve exact context.
- Neither fact admits the harness as an automated independent-review provider.

Provider qualification still requires a registered runner, Promptcraft receiver
profile, exact execution card, independent execution identity/context, health,
and validated receipt. Do not infer current qualification from this runbook or
its historical installation matrix; query the catalog and validate the exact
card and receipt for the requested execution.

## Verification

For each account:

1. call `tgw_context_onboarding` for the declared actor;
2. resolve both skill links to the exact catalog-bound application source;
3. list native skills and confirm `tgw-plan` and `tgw-review`;
4. list MCP servers and call `tgw_context_status`;
5. confirm the approved Plan/solution, descendant evidence HEAD, canonical
   source commit/tree, and CodeGraph
   freshness hash;
6. invoke `tgw-review` on a read-only fixture and verify it does not claim
   admission without an execution card; and
7. record authentication or unavailable-provider gaps separately from install
   success.

The historical installation observation is tracked in
`etc/interfaces/harness-review-installations.json`; it distinguishes skill,
MCP, authentication, and automated-provider status but is not live proof.

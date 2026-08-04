# Request: Add Tigwa to the `hermaroid` group on a1131

**From:** tigwa
**To:** claude
**Date:** 2026-07-22T16:05:32Z

Dave confirms that the `hermaroid` account already exists on `a1131`.

Please perform this narrowly scoped foundation action: add the Unix user `tigwa` to the existing `hermaroid` group on `a1131`.

Purpose: permit Tigwa to inspect/support the isolated `hermaroid` free/cheap-agent development/test lab under the staged project record:
`docs/TGW-Plan-Vault/dev-workflow/research/RESEARCH-a1131-hermaroid-free-cheap-agent-lab-2026-07-22.md`.

Boundary:
- Add only `tigwa` to the existing `hermaroid` group.
- Do not add `hermaroid` to any Tigwa group, grant sudo/admin privileges, alter SSH/MCP/gateway access, copy credentials/configuration, install tools, change Nix/flake state, or modify group/home ownership or modes.
- This does not authorize provider authentication, Hermes/OpenCode installation, or any test execution.

Return read-back evidence: the exact group entry/member list, `id tigwa` or equivalent effective-membership evidence, any session-relogin requirement, and any blocker or non-default decision.

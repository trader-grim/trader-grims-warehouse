# Claude Code interface

Claude Code consumes the same canonical TGW skills as other harnesses and two
separate MCP authorities:

- `tgw`: production inventory/operator MCP on tgw-prod;
- `tgw-context`: read-only Plan, runbook, and CodeGraph context on tgw-lib.

Install the skill links as the Claude account:

```bash
python3 scripts/install_shared_harness_skills.py --harness claude
```

Add the two user-scoped MCP definitions from `mcp-servers.json` with
`claude mcp add-json --scope user`. Claude Code stores user-scoped MCP state in
`~/.claude.json`; do not paste it into `settings.json`, copy another user's
OAuth state, or restore the old `sudo -u tgw` command.

The historical `tgw-aider` server is intentionally absent. Its current source
still binds the retired `/home/tgw` Aider binary and old worktree workflow, and
no Aider executable is installed on this host. Re-admit it separately before
restoring that MCP.

`project-settings.local.json` is a preserved legacy broad allow-list and is not
part of this installation. Do not copy it into a current project without a
separate permission-by-permission reconciliation. Verify the installed account
with:

```bash
claude auth status
claude mcp list
```

Authentication remains per account. A missing Claude login does not justify
copying Tigwadev's OAuth metadata or credentials.

# Claude Code Interface Config

Configuration files for Claude Code (the operator's AI coding assistant).

## Files

- `mcp-servers.json` — MCP server block to merge into `~/.claude/settings.json`
- `project-settings.local.json` — project-level permissions allow-list; goes at `.claude/settings.local.json`

## Post-migration install

After a fresh install or user migration, restore Claude Code config:

```bash
# 1. Merge MCP servers into user settings
#    Open ~/.claude/settings.json and add the mcpServers block from mcp-servers.json

# 2. Restore project permissions
cp etc/interfaces/claude/project-settings.local.json .claude/settings.local.json

# 3. Verify MCPs connect
claude  # open a session; tgw and tgw-aider should appear in MCP tool list
```

The `mcp-servers.json` block must be merged manually into `~/.claude/settings.json`
(user-level file, outside the repo) alongside model/plugin/theme preferences.

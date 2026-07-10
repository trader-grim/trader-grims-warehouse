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

## Plugin path fix (after user rename/migration)

If plugins fail with "cache-miss" after a user rename (e.g. `tgw` → `db`), **four** files need
the old home path updated:

```bash
OLD=/home/tgw
NEW=/home/db

# 1. installed_plugins.json — per-plugin installPath entries
sed -i "s|$OLD/.claude/plugins/cache|$NEW/.claude/plugins/cache|g" ~/.claude/plugins/installed_plugins.json

# 2. known_marketplaces.json — marketplace installLocation
sed -i "s|$OLD/.claude/plugins/marketplaces|$NEW/.claude/plugins/marketplaces|g" ~/.claude/plugins/known_marketplaces.json

# 3. settings.local.json — Read() permission paths
sed -i "s|Read(//home/tgw/|Read(//home/db/|g" .claude/settings.local.json

# 4. .aider.conf.yml — env-file path
sed -i "s|env-file: $OLD/.env|env-file: $NEW/.env|g" .aider.conf.yml
```

After fixing, verify with `claude plugin list` — both plugins should show `Status: ✔ enabled`.

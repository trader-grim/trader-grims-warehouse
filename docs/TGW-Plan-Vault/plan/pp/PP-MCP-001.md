## PP-MCP-001 — TGW Model Context Protocol Server

### Vision
Expose TGW capabilities as an MCP (Model Context Protocol) server so Claude Code (and other
MCP clients) can query item data, trigger pipeline actions, and inspect system health as
native tools — without subprocess calls or shell scripts.

### Status: ✅ CODE DONE (session 13) — awaiting operator MCP registration

### MCP tools implemented
| Tool | Description |
|------|-------------|
| `tgw_get_item` | Fetch full item JSON for a SKU |
| `tgw_search_items` | Search catalog by text, location, or status |
| `tgw_queue_status` | Return current job counts per queue + state |
| `tgw_health` | Platform health summary |
| `tgw_enqueue` | Enqueue a pipeline action for a SKU |
| `tgw_get_todo` | List open TODO items for a given agent |
| `tgw_add_suggest` | Append to SUGGESTIONS.md (same as `tgw suggest`) |
| `tgw_hint_trail` | Return identification history for an item |
| `tgw_catalog_verify` | Scan ItemData for assumption violations |

### Architecture
- `src/tgw/mcp_server.py` — FastMCP server calling TGW internals directly
- `tgw-mcp-server` console script in pyproject.toml
- `mcp>=1.0` added to dependencies (installed 2026-06-08)
- Runs as a local stdio process; no external network exposure
- Config: import from TGW_CONFIG env (default: `/opt/TGW/config/tgw-api-config.json`)

### Value
- Claude Code can query live queue state and item data mid-session without shell escapes
- Enables Claude-native tooling loops: identify failures, re-enqueue, verify fix — all in one session
- Sets foundation for other MCP clients (custom dashboard, VS Code extension)

### Registration (operator action — see Track 4 Priority 1b)
Add to `~/.claude/settings.json`:
```json
"mcpServers": {
  "tgw": {
    "command": "sudo",
    "args": ["-u", "tgw", "/opt/TGW/.venvironments/tgw/bin/python", "-m", "tgw.mcp_server"],
    "env": {}
  }
}
```

### Dependencies
- `tgw-http` FastAPI service ✅ running
- `mcp` Python SDK ✅ installed 2026-06-08
- Claude Code MCP registration ⬅ **operator action pending**

---


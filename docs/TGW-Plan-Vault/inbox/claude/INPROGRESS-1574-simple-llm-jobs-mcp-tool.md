# INPROGRESS: todo #1574 — tgw_simple_llm_jobs MCP tool

Working in worktree `/opt/TGW/var/worktrees/1574-simple-llm-jobs-mcp-tool` on
branch `todo/1574-simple-llm-jobs-mcp-tool`. Building the new
`tgw_simple_llm_jobs` MCP tool (DeepSeek V4-Flash non-thinking text-transform,
PP-SIMPLEJOBS-001) per packet
`docs/TGW-Plan-Vault/plan/packets/1574-simple-llm-jobs-mcp-tool.md`. Adding a
`simple_llm_jobs` task entry to `tgw-models.json`, the new tool in
`mcp_server.py`, and a test. Acceptance requires a live DeepSeek call — will
capture real response/usage as evidence. Not touching pm_intake/
suggestions_classify/pricing_comp_filter behavior.

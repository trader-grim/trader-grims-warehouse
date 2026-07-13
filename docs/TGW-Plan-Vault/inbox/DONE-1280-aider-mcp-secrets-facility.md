# In progress: todo #1280 aider-mcp-secrets-facility

Working in worktree `/opt/TGW/var/worktrees/1280-aider-mcp-secrets-facility`
on branch `todo/1280-aider-mcp-secrets-facility`. Task: fix
`src/tgw/aider_mcp_server.py`'s `_load_api_keys()` to read
ANTHROPIC_API_KEY/OPENROUTER_API_KEY via `tgw.apis.secrets.get_api_key()`
instead of the dead `anthropic-credentials.json`/`openrouter-credentials.json`
paths (post-#1252-migration files no longer exist there, so keys silently
fail to load). Per packet
`docs/TGW-Plan-Vault/plan/packets/1280-aider-mcp-secrets-facility.md`.
Status at write time: about to read the target file and apply the spec'd
fix, then run offline pytest with PYTHONPATH override and write the result
manifest.

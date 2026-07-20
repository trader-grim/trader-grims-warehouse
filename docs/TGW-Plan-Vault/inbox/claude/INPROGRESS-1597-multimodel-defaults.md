# INPROGRESS: todo #1597 — PP-MULTIMODEL-001 / invariant E15

Working in isolated worktree `/opt/TGW/var/worktrees/1597-multimodel-defaults` on
branch `todo/1597-multimodel-defaults`. Task: add a `defaults` block to
`tgw-models.json`, extend `get_task_model()` in `src/tgw/apis/llm.py` to resolve
`{"use_default": name}` pointers, and delete two dead hardcoded model strings
(`config.py`'s `alt_text_model`/`alt_text_provider` defaults, `api.py`'s
`tgw alt-text` CLI help text). Per invariant E15 in
`docs/TGW-Plan-Vault/reference/invariants.md`. Result manifest will land at
`docs/TGW-Plan-Vault/plan/packets/results/1597-RESULT.md` on this branch.

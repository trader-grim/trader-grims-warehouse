# In progress: todo #1598 (PP-MULTIMODEL-001)

Sweep for hardcoded model/provider values (invariant E15), following #1597's
`defaults`/`use_default` mechanism in tgw-models.json. Working in worktree
`/opt/TGW/var/worktrees/1598-multimodel-hardcoded-sweep` on branch
`todo/1598-multimodel-hardcoded-sweep`, based off `catio-nix-0.0.1-alpha` tip
(includes #1597's merge, commit eeb5bca).

Five items to fix/investigate: alt_text.py _BATCH_DEFAULT_MODEL,
ai_identify.py _OLLAMA_FALLBACK_MODEL, api.py --provider CLI choices,
google_genai.py default param, quota.py pricing-table staleness.

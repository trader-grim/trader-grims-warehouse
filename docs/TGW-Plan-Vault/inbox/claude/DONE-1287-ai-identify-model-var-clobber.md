Working on todo #1287 (PP-COHESION-001) in worktree
`/opt/TGW/var/worktrees/1287-ai-identify-model-var-clobber` on branch
`todo/1287-ai-identify-model-var-clobber`. Task: rename the local variable
`model` (AI-extracted product-model field) to `item_model` in
`src/tgw/workers/ai_identify.py` so it no longer clobbers the LLM
provider-model id used for `identification_history`/`vision_results`
provenance. Per packet: edit lines ~262, ~307, ~368 only; leave
~225/226/234/235/339/358 untouched. Next: read the file, apply edit,
write/run a test proving provenance is preserved, write result manifest.

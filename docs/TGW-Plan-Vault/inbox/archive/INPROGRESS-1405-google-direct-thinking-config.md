# In progress: todo #1405 (PP-DEADLETTER-001)

Working in worktree `/opt/TGW/var/worktrees/1405-google-direct-thinking-config`
on branch `todo/1405-google-direct-thinking-config` (based off
`catio-nix-0.0.1-alpha`, verified live via `git branch --show-current`).

Task: `_call_google_direct()` in `src/tgw/apis/llm.py` passes no
`maxOutputTokens`/`thinkingConfig` to Gemini's `generate_content()`. Suspected
root cause of all 95 `ebay_draft` aspect-fill dead-letters (Gemini 2.5
flash-lite "thinking" budget consuming the whole output before any visible
JSON appears). Plan: verify live against real dead_letter rows in
`queue_jobs`, then add general per-task `thinking_config`/`max_output_tokens`
plumbing (config field any task can set, not a bulk_classify-only special
case), wire from `tgw-models.json`, and re-run a real dead-lettered job
through the corrected path as live acceptance evidence.

Working todo #1371 (PP-COHESION-001, Data Charter, follow-up from #1306) in
isolated worktree `/opt/TGW/var/worktrees/1371-alt-text-gemini-batch-raw`
on branch `todo/1371-alt-text-gemini-batch-raw`. Task: alt_text.py's Gemini
Batch API path (cmd_alt_text_gemini_batch / _apply_alt_text_result) still
discards the raw per-image LLM response — google_genai.parse_batch_results()
only returns already-parsed fields. Needs google_genai.py's batch parsing to
retain the raw envelope/text per task. Part of the follow-up cleanup batch
(#1369-1374).

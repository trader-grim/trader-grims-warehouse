Status: cleared
Reviewer: Claude (runner-review)
Todo: #1306   PP: PP-COHESION-001
Checked: diff (`git diff f219b4b todo/1306-alt-text-raw-preservation`)
against the todo brief's stated bug (raw LLM response discarded after JSON
extraction, Data Charter violation), scope (alt_text.py + new test only),
result manifest completeness. Traced the `raw` variable through
`cmd_alt_text()` directly to confirm it's set only on an actual (non-cached)
`call_model()` call and stays None on cache hits — the append-only guard
(`if raw is not None:`) is correct. Independently reproduced the flagged
`test_llm_google_direct.py` failure against the bare pre-#1306 base commit
(f219b4b) in a throwaway worktree — confirmed pre-existing quota-state test
pollution, unrelated to this change, correctly tracked as #1370.
Summary: new `item['alt_text_results']` append-only list mirrors
ai_identify's vision_results[] pattern; field-name deviation
(alt_text_results vs vision_results) explicitly flagged and reasonably
justified (differing record shape). Two out-of-scope findings correctly
filed as new todos rather than fixed in-branch: #1371 (Gemini Batch path
still discards raw response, needs google_genai.py changes) and #1372
(alt_text.py's atomic_write_json never passes archive_root — same bug class
as the #1298 cluster, but a different file/root, correctly not bundled in).
Full suite green modulo the pre-verified-unrelated #1370 flake. No triggers
fired. Cleared for stitch.

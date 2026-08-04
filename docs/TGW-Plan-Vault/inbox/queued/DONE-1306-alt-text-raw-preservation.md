Todo #1306 (PP-COHESION-001, Data Charter) — DONE, stitched. alt_text.py
now persists the raw LLM response in item['alt_text_results'][] (mirrors
ai_identify's vision_results[] pattern), append-only, one record per actual
(non-cached) model call. Reviewed clean, full suite green. Spun off 3 new
todos: #1370 (unrelated pre-existing flaky test, independently verified),
#1371 (Gemini Batch alt-text path still discards raw response), #1372
(alt_text.py's own atomic_write_json calls never pass archive_root).

# Result: 1597 multimodel-defaults

Status: done
Todo: #1597   PP: PP-MULTIMODEL-001

Files touched:
- `/opt/TGW/config/tgw-models.json` (live runtime config, not in git — see CLAUDE.md Key Paths;
  added `defaults` block with `default`/`default_deepseek_nonthinking` profiles; switched
  `ai_identify`, `alt_text`, `bulk_classify` → `{"use_default": "default"}`; switched
  `pm_intake`, `suggestions_classify`, `pricing_comp_filter`, `simple_llm_jobs` →
  `{"use_default": "default_deepseek_nonthinking"}`; left `ebay_draft` and `pm_chat`
  untouched with their deliberately-different explicit `{provider, model}` entries;
  updated `_comment` to describe the new mechanism)
- `src/tgw/apis/llm.py` — `get_task_model()` extended with a `use_default` resolution
  branch (looks up `cfg['models']['defaults'][name]`, raises `KeyError` with a clear
  message if the name is missing; entries are either full `{provider, model}` or
  `{use_default}`, never both, no partial merge — kept as a simple two-branch lookup
  per the packet's explicit anti-over-engineering constraint). External signature and
  `(provider, model)` return shape unchanged. `get_task_generation_config()` left
  per-task-only (does NOT inherit `generation` from a `use_default` profile) — noted
  as a deliberate limitation in a docstring comment, per the packet's recommendation.
- `src/tgw/config.py` — deleted the two dead `alt_text_provider`/`alt_text_model`
  dict entries from `load_config()`'s returned config (confirmed no readers anywhere
  in `src/` or `scripts/` before deleting — `cmd_alt_text` uses `get_task_model(cfg,
  'alt_text')`, not these keys).
- `src/tgw/api.py` — fixed stale `tgw alt-text --model`/`--provider` CLI help text
  (previously hardcoded `google/gemini-2.5-flash` / `openrouter` as the stated
  defaults) to point at "tgw-models.json's 'alt_text' task config" instead of naming
  a specific model/provider.
- `tests/test_model_routing.py` — added 5 new tests: `use_default` resolves correctly
  (both `default` and `default_deepseek_nonthinking` profiles), explicit
  `{provider, model}` override still works unchanged, unknown `use_default` name
  raises `KeyError` naming the bad default, and a task with neither `provider`/`model`
  nor `use_default` still raises the original clear error.

Live evidence:
- `pytest -q` (worktree copy, `PYTHONPATH`/`LD_LIBRARY_PATH` confirmed pointing at
  `/opt/TGW/var/worktrees/1597-multimodel-defaults/src`): **2724 passed, 1 skipped**,
  0 failed, fully offline.
- `sudo -u tgw tgw health`: only pre-existing unrelated failures (`backups` —
  rclone stamp/todo #1077 lineage; `ebay_sync_fallback` — 775 consecutive
  per-SKU fallback runs, todo #1077) — no new failures from this change, nothing
  model/config-related in the failed list.
- Live end-to-end resolution check against the real production
  `/opt/TGW/config/tgw-api-config.json` + `/opt/TGW/config/tgw-models.json`, run as
  `tgw` user with the worktree's code on `PYTHONPATH` (confirmed via
  `tgw.apis.llm.__file__` printing the worktree path, not the shared checkout):
  ```
  alt_text -> google_direct gemini-2.5-flash-lite
  ai_identify -> google_direct gemini-2.5-flash-lite
  bulk_classify -> google_direct gemini-2.5-flash-lite
  pm_intake -> deepseek_direct deepseek-v4-flash
  suggestions_classify -> deepseek_direct deepseek-v4-flash
  pricing_comp_filter -> deepseek_direct deepseek-v4-flash
  simple_llm_jobs -> deepseek_direct deepseek-v4-flash
  ebay_draft -> google_direct gemini-3.1-pro-preview
  pm_chat -> anthropic_direct claude-haiku-4-5-20251001
  alt_text_model in cfg: False
  alt_text_provider in cfg: False
  ```
  All values identical to what they resolved to before the change — confirming the
  `use_default` pointers correctly round-trip through the real config, and the two
  dead keys no longer appear in `load_config()`'s output.
- Real CLI end-to-end run, worktree code + live catalog:
  `python -m tgw.api alt-text --batch --limit 1 --dry-run` →
  `{"ok": true, "dry_run": true, "eligible": 1, "skus_preview": ["tgw201501081959226"], ...}`
  — confirms the full CLI path (which calls `get_task_model(cfg, 'alt_text')`
  internally) still resolves and runs clean against a real SKU.

Deviations from spec: none.

Out-of-scope findings filed: #1599 (`tgw alt-text --provider` CLI choices list
`[openrouter, ollama]` only — can't override to a direct provider from the CLI;
pre-existing, unrelated to this packet's scope, filed rather than fixed inline).

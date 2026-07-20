# Result: #1598 multimodel-hardcoded-sweep
Status: done
Todo: #1598   PP: PP-MULTIMODEL-001

## Files touched
- `src/tgw/alt_text.py`
- `src/tgw/api.py`
- `src/tgw/apis/google_genai.py`
- `src/tgw/quota.py`
- `src/tgw/workers/ai_identify.py`
- `tests/test_alt_text_gemini_batch.py`
- `tests/test_invariant_c12_field_set_accessors.py`
- `tests/test_quota_balance_warning.py`
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1598-multimodel-hardcoded-sweep.md` (breadcrumb)

## Per-item outcome

1. **`alt_text.py` `_BATCH_DEFAULT_MODEL`** — removed. `cmd_alt_text_gemini_batch`
   now resolves via `get_task_model(cfg, 'alt_text')` when `--model` isn't
   passed, matching `cmd_alt_text`/`cmd_alt_text_batch`. Since Gemini Batch
   API is Google-only, if the resolved provider isn't `google_direct` it
   raises a clear `ValueError` naming the actual provider/model and telling
   the operator to pass `--model` or fix `tgw-models.json`, rather than
   silently using a model id shaped for the wrong provider. Explicit
   `--model` still bypasses the provider check (operator override).

2. **`ai_identify.py` `_OLLAMA_FALLBACK_MODEL`** — investigated: **dead
   code**, not a second routing default competing with tgw-models.json.
   Grepped the whole file (and `src/`, `tests/`) — the constant was never
   read anywhere; the actual model always comes from
   `get_task_model(cfg, 'ai_identify')` via `call_model`. Removed it
   outright (not left as an "exempt, commented" case — it wasn't a
   legitimate last-resort safety net, it just never executed). Also
   rewrote the module docstring, which previously repeated the same class
   of stale literal ("Defaults: openrouter / google/gemini-2.5-flash-lite.
   Ollama fallback: qwen2.5vl:7b") — now points at tgw-models.json only.

3. **`api.py` `--provider` CLI choices** — added `google_direct`,
   `deepseek_direct`, `anthropic_direct` to `tgw alt-text --provider`'s
   choices (todo #1599 folded in as directed). Grepped `choices=\[.openrouter`
   across `api.py` — this was the only match; no other subcommand has the
   same stale pattern.

4. **`google_genai.py` `build_alt_text_task` default param** — grepped
   every call site (`src/`, `tests/`): the one real production caller
   (`alt_text.py:858`, inside `cmd_alt_text_gemini_batch`) always passes
   `model=effective_model` explicitly, sourced from `get_task_model()`/the
   `--model` override upstream. No production caller relies on the
   default. Removed the default (`model: str` is now required); updated
   the handful of test call sites that had relied on it incidentally
   (none of those tests were testing the *default value itself* — they
   were testing unrelated shape/behavior and just hadn't bothered to pass
   `model`) to pass `model="gemini-2.5-flash-lite"` explicitly.

5. **`quota.py` pricing table (`_PRICING_USD_PER_1M`)** — kept as literals
   (correctly out of E15's scope per the packet — it's cost-estimation
   data, not provider/model routing). Checked the missing-key behavior:
   `estimate_cost_usd()` already used `.get()` (not `[...]`) and was
   already fail-open (no `KeyError` risk) — its only caller
   (`today_cost_usd_by_provider`) already skips `None`-cost rows
   gracefully. The actual gap was **silence**, not a crash: a future
   `tgw-models.json` model with no matching pricing entry would
   permanently and invisibly zero out that model's cost tracking. Fixed
   by adding a `log.warning(...)` (module's existing fail-open-with-warning
   style, matching `today_cost_usd_by_provider`'s own DB-error handling)
   when a model has real token counts but no pricing entry — distinguished
   from the expected/common "no token counts at all" case (failed call,
   no-usage provider), which stays silent. No speculative pricing entries
   added.

## Live evidence

- **Item 1** — ran `cmd_alt_text_gemini_batch(cfg2)` (empty ItemData,
  early-return path) against the real production config
  (`/opt/TGW/config/tgw-api-config.json`), as `tgw` user, worktree copy
  confirmed via `tgw.config.__file__`:
  ```
  cmd_alt_text_gemini_batch resolved model: gemini-2.5-flash-lite
  get_task_model resolved model: gemini-2.5-flash-lite provider: google_direct
  MATCH CONFIRMED
  ```
- **Item 3** — `python -m tgw.api alt-text --help` (worktree copy, `tgw`
  user):
  ```
  --provider {openrouter,ollama,google_direct,deepseek_direct,anthropic_direct}
                        provider (default: from tgw-models.json's 'alt_text'
                        task config)
  ```
- **Test suite**: `pytest -q` (worktree `src` on `PYTHONPATH`, confirmed
  via `tgw.apis.llm.__file__` resolving under the worktree, not the shared
  checkout) — `2728 passed, 1 skipped`, clean, offline.
- **`sudo -u tgw tgw health`** — same two pre-existing failures as
  baseline (`backups`, `ebay_sync_fallback`); both unrelated to any file
  touched in this pass (no backup/eBay-sync code touched). No new
  failures introduced.

## Deviations from spec

- None on the 5 named items — each resolved per the packet's own decision
  tree (item 2 confirmed dead/removed rather than commented-exempt; item 4
  confirmed no load-bearing caller and removed; item 5 kept as literals
  with a defensive warning added, no speculative entries).
- **Out-of-packet fix required and made**: editing `ai_identify.py`
  (removing the dead constant + docstring rewrite) shifted line numbers by
  +3, breaking `tests/test_invariant_c12_field_set_accessors.py`'s pinned
  allowlist (a static line-number detector for a *different* invariant,
  C12). This is the expected/documented maintenance cost of that
  detector's design (its own docstring: "expect to refresh this list again
  after future edits") — refreshed the three affected line numbers
  (273/333/428 → 276/336/431) with a dated comment, same pattern as its
  prior refresh entries. Not a scope creep — a direct, mechanical
  consequence of the in-scope `ai_identify.py` edit; no accessor-routing
  behavior changed.
- api.py's `--provider` argparse call was reformatted to multi-line (was a
  single very long line) purely to satisfy the repo's ruff `E501` line-length
  pre-commit hook after adding the 3 new choices — no behavior change.

## Out-of-scope findings filed
None — todo #1599 (the CLI-choices gap) was already filed from #1597's
review and was explicitly folded into this packet's item 3, not
separately re-filed.

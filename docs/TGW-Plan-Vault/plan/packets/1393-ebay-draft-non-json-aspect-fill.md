# Packet: ebay_draft aspect-fill LLM calls dead-letter on truncated/fenced JSON

Todo: #1393   PP: PP-DEADLETTER-001   Track: dead-letter triage (batch, see
PP-DEADLETTER-001.md — 8 packets dispatched together this round, Dave's
explicit ask to run a bigger concurrent batch to surface more findings
faster)

## Context budget (ALL the model may load)
This packet + `src/tgw/apis/ollama.py` (whole file, ~91 lines) +
`src/tgw/workers/ebay_draft.py` (whole file) + `src/tgw/workers/ai_identify.py`
(whole file — shares `extract_json`) + any existing test file covering
`extract_json` (search `tests/` before assuming none exists). Nothing else.

## Verified live before this packet was written
- 95 `ebay_draft` dead-letters, all `HardFailure('ebay_draft: model
  returned non-JSON for <SKU>: ...')`, queried live from `queue_jobs`
  2026-07-14.
- Every sampled `error_detail` starts with a literal ` ```json` fence
  marker and the JSON is cut off mid-object/mid-string with **no closing
  ``` fence** — e.g. `` ```json\n{\n  "Brand": "Central",\n  "Theme":
  "Geometric", ... `` with no trailing brace or fence.
- Root cause, confirmed by reading `src/tgw/apis/ollama.py:70-77`:
  ```python
  def extract_json(text: str) -> Any:
      text = text.strip()
      fenced = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', text)
      if fenced:
          text = fenced.group(1)
      return json.loads(text)
  ```
  The fence-stripping regex requires **both** an opening and a closing
  ` ``` ` — a `re.search` with a non-greedy `[\s\S]+?` between two literal
  fence markers. When the model's response is truncated before the
  closing fence (which is what's happening in all 95 cases), `fenced` is
  `None`, so `text` is left as the raw string **including the leading
  ` ```json` marker**, and `json.loads()` fails immediately — that's the
  "model returned non-JSON" `HardFailure`, not a parsing edge case.
- This has two independent sub-causes worth distinguishing, don't conflate
  them into one fix:
  1. **Fence-stripping bug**: even a *complete* fenced response with no
     closing fence (some providers omit it) currently can't be parsed at
     all. This is a real extract_json bug regardless of truncation.
  2. **Truncation**: the JSON itself is genuinely cut off mid-object in
     most samples (missing closing `}`, mid-string). This means the
     model's response is hitting a token limit for this call — check
     `ebay_draft.py`'s aspect-fill call site (`extract_json(raw)` around
     line 434) for what `max_tokens`/model is configured via
     `tgw.apis.llm.get_task_model()` / `call_model()`, per settled
     architecture (model routing is config-only, `tgw-models.json`).
     A larger token budget for this task may be the real fix; a
     fence-stripping change alone would still fail on a genuinely
     incomplete JSON object.

## Spec
1. Fix `extract_json()` in `src/tgw/apis/ollama.py` to strip a leading
   ` ```json` or ` ``` ` fence marker even when no closing fence is
   present (i.e., handle open-only fences), so a complete-but-unclosed
   fenced response parses correctly. Do not silently swallow genuinely
   truncated JSON — if `json.loads()` still fails after fence-stripping,
   it should still raise (that's a real truncation, not a parsing bug).
2. Investigate whether the aspect-fill call in `ebay_draft.py` (around
   line 434, the `extract_json(raw)` call) is token-limited via its
   configured model/task in `tgw-models.json` (`get_task_model()` /
   `call_model()`). If the failure samples show truncation is the
   dominant cause (check: do most of the 95 samples cut off before a
   reasonable object size, or are they short/complete-but-unfenced?),
   flag this as a finding for a task-model config change — **do not
   silently increase the token budget in code**; per settled
   architecture, model routing/params are config-only
   (`/opt/TGW/config/tgw-models.json`, NOT in this git repo). If a config
   change is warranted, say so explicitly in the result manifest so Dave
   can apply it — don't hardcode it and don't skip flagging it.
3. Ensure the worker doesn't silently drop the item on failure — if
   `extract_json` still raises after your fix, the existing dead-letter
   path is correct (real per-item failure), just confirm it's a durable,
   queryable finding, not just a log line (invariant C11 — check whether
   `ebay_draft.py` already persists a finding on this failure path or
   only logs it).

## Out of scope
- Do not touch `tgw-models.json` directly (not in this repo, and model
  routing changes need Dave's explicit go per settled architecture).
- Do not attempt to "fix" the 95 already-dead-lettered jobs' data — this
  packet is the code fix; requeuing is a separate step after merge
  (todo #1402's generic requeue script, or a one-off with the same
  dedupe-key + `announce_script_run()` pattern as #1265).
- Any other queue/worker.

## Dataset
None — this is a parsing/config-investigation fix, no stored item data is
touched. If the aspect-fill call is later reconfigured with a larger token
budget, that's a config change (tgw-models.json), not a code/dataset change.

## Acceptance (live)
1. Unit test: `extract_json('```json\n{"a": 1, "b": 2}\n```')` returns
   `{"a": 1, "b": 2}` (existing closed-fence case, must not regress).
2. Unit test: `extract_json('```json\n{"a": 1, "b": 2}')` (no closing
   fence, complete JSON) returns `{"a": 1, "b": 2}` — the actual bug this
   packet fixes.
3. Unit test: `extract_json('```json\n{"a": 1, "b":')` (no closing fence,
   genuinely truncated JSON) still raises `json.JSONDecodeError` — confirm
   we don't silently mask real truncation as success.
4. Unit test: `extract_json('{"a": 1}')` (no fence at all) still works —
   confirm the no-fence case is unaffected.
5. Run the full offline suite — zero regressions.
6. Report in the result manifest: of the 95 dead-lettered samples, how
   many would now parse successfully with the fence fix alone vs. how many
   are genuinely truncated and need a larger token budget (config
   change, not yours to make).

## Quota/risk
Low — pure parsing-logic fix plus an investigation writeup. No new API
calls in the fix itself. If a token-budget config change is recommended,
flag the quota/cost implication (larger max_tokens = more tokens billed
per aspect-fill call) for Dave's decision, don't apply it yourself.

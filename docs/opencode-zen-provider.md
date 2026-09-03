# OpenCode Zen as an LLM provider

**Added:** 2026-09-03 (Dave). **Provider id:** `opencode_zen`.
**Key:** `OPENCODE_ZEN_API_KEY` (already present in `/opt/TGW/secrets/tgw.env`
on tgw-prod — distinct from the separate "opencode go" key).

OpenCode Zen is an OpenAI-compatible model gateway. This adds it as a fourth
direct provider alongside `google_direct`, `deepseek_direct`, and
`anthropic_direct` in `tgw.apis.llm`.

## Why

The operator's OpenCode Zen key exposes a free `deepseek-v4-flash-free` model.
Routing TGW's small DeepSeek text jobs there gives a genuinely free, unmetered
primary for work that was previously spending the pay-as-you-go DeepSeek key
(and its OpenRouter fallback markup).

### DeepSeek cost caps, removed 2026-09-03 (same change)

By operator instruction, the `deepseek_direct` path now runs to `$0` before
falling back to OpenRouter — no early cut-off:

- `_DEFAULT_BUDGETS['llm_deepseek']` → `None` (count-only). The old `500/day`
  synthetic call cap used to raise `QuotaBudgetExceeded` at 70% and push
  background jobs to OpenRouter well before the key was actually spent.
- `_DEFAULT_DEEPSEEK_LOW_BALANCE_USD` → `0.0`. `check_deepseek_balance()` still
  reports the real number in `tgw health`, but never tags it `[LOW]` or emits
  a `background halted` line.
- The post-429 cooldown in `precheck()` is untouched — that is a rate-limit
  guard, not a cost cap.
- `llm_google` / `llm_anthropic` keep their provisional caps (no balance API,
  real per-call cost).

## Not restricted — satisfies the requirement

| | Native DeepSeek key | OpenCode Zen `deepseek-v4-flash-free` |
|---|---|---|
| Context window | 1M tokens | 256k tokens |
| Prepaid balance | yes (depletes) | none |
| Documented rate cap | none | none |

The **only** difference that could matter is the 256k context window. Every
TGW task that routes to a DeepSeek-class model is a small text transform:

| Task | Input |
|---|---|
| `pm_intake` | one truncated inbox note (`_MAX_RESPONSE_BYTES`, `_URL_FETCH_MAX_CHARS`, `[:200]` snippets) — no images |
| `suggestions_classify` | a single suggestion string |
| `simple_llm_jobs` (MCP tool) | bounded summarize / classify / extract / rewrite text |
| Aider edits | repo map + a few files |

All of these are far below 256k tokens. **The Zen free tier imposes no
restriction that affects any TGW use of it.** If a future task ever needs
>256k context on a DeepSeek-class model, route that one task to
`deepseek_direct` (native, 1M) in `tgw-models.json` — a config change, not a
code change.

## Behaviour

- `provider: opencode_zen` → POST to `https://opencode.ai/zen/v1/chat/completions`
  with `Authorization: Bearer $OPENCODE_ZEN_API_KEY`, same request shape as
  `deepseek_direct`.
- On **any** failure, `call_model()` falls back to
  `openrouter/deepseek/<model without the -free suffix>` — a Zen outage never
  dead-letters a job.
- Quota pool `llm_opencode_zen` is **count-only** (`None` budget, like
  `llm_openrouter`). It is deliberately not a low-balance pool.
- No image support (same as `deepseek_direct`).

## Wiring it live

The keys already live in `/opt/TGW/secrets/tgw.env` **on tgw-prod** — that is
where the workers (`pm_intake`, `suggestions_classify`, …) run, and
`tgw.config.load_config()` sources that file into their environment at
startup. No key file change is needed.

Remaining steps, both on tgw-prod:

1. **Deploy this code** (the `opencode_zen` provider + the cap removal) as a
   new release generation.

2. **Route the tasks** — in `/opt/TGW/config/tgw-models.json`, point the
   DeepSeek-class tasks at the new provider:

   ```json
   "pm_intake":            { "provider": "opencode_zen", "model": "deepseek-v4-flash-free" },
   "suggestions_classify": { "provider": "opencode_zen", "model": "deepseek-v4-flash-free" },
   "simple_llm_jobs":      { "provider": "opencode_zen", "model": "deepseek-v4-flash-free" }
   ```

   Update the file's `_comment` to record `opencode_zen` as the current
   DeepSeek-class primary and why.

3. **Restart the release-bound workers** so they reload the config.

If the `simple_llm_jobs` MCP tool or the Aider bridge also runs on tgw-lib
rather than prod, `OPENCODE_ZEN_API_KEY` needs to be in tgw-lib's environment
too (wherever that host sources it).

The native `deepseek_direct` path and its `DEEPSEEK_API_KEY` stay in place as
the >256k-context escape hatch and as one more fallback tier.

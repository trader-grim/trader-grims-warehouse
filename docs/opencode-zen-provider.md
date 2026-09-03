# OpenCode Zen as an LLM provider

**Added:** 2026-09-03 (Dave). **Provider id:** `opencode`.

OpenCode Zen is an OpenAI-compatible model gateway. This adds it as a fourth
direct provider alongside `google_direct`, `deepseek_direct`, and
`anthropic_direct` in `tgw.apis.llm`.

## Why

The operator's OpenCode Zen key exposes a free `deepseek-v4-flash-free` model.
Routing TGW's small DeepSeek text jobs there:

- **removes the `background halted: direct-LLM provider low balance/spend`
  health warning** — that line trips whenever `check_deepseek_balance()`
  reports the pay-as-you-go DeepSeek balance under `$2`
  (`quota_deepseek_low_balance_usd`). It is a **WARN**, not a hard stop —
  `precheck()` never consults the dollar balance, and a real insufficient-balance
  error from `api.deepseek.com` already falls back to OpenRouter automatically —
  but it is noise on every `tgw health` and it means the fallback path is
  paying OpenRouter's markup.
- gives a genuinely free, unmetered primary for those jobs.

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
restriction that affects any TGW use of it.** If a future task needs >256k
context on a DeepSeek-class model, route that task to `deepseek_direct`
(native, 1M) in `tgw-models.json` — a config change, not a code change.

## Behaviour

- `provider: opencode` → POST to `https://opencode.ai/zen/v1/chat/completions`
  with `Authorization: Bearer $OPENCODE_API_KEY`, same request shape as
  `deepseek_direct`.
- On **any** failure, `call_model()` falls back to
  `openrouter/deepseek/<model without the -free suffix>` — a Zen outage never
  dead-letters a job.
- Quota pool `llm_opencode` is **count-only** (`None` budget, like
  `llm_openrouter`). It is deliberately not a low-balance pool.
- No image support (same as `deepseek_direct`).

## Wiring it live

Both files are on **tgw-prod** (where the workers run):

1. **Key** — add to `/opt/TGW/secrets/tgw.env`:

   ```
   OPENCODE_API_KEY=<the OpenCode Zen key>
   ```

   `tgw.config.load_config()` sources this into the process environment at
   startup; real env vars win over the file.

2. **Routing** — in `/opt/TGW/config/tgw-models.json`, point the DeepSeek-class
   tasks at the new provider:

   ```json
   "pm_intake":            { "provider": "opencode", "model": "deepseek-v4-flash-free" },
   "suggestions_classify": { "provider": "opencode", "model": "deepseek-v4-flash-free" },
   "simple_llm_jobs":      { "provider": "opencode", "model": "deepseek-v4-flash-free" }
   ```

   Update the file's `_comment` to record that `opencode` is the current
   DeepSeek-class primary and why.

3. Restart the release-bound workers so they reload config.

The native `deepseek_direct` path and its `DEEPSEEK_API_KEY` stay in place as
the >256k-context escape hatch and as one more fallback tier.

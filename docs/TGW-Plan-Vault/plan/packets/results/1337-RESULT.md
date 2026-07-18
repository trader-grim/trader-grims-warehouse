# Result: todo #1337 — proactive low-balance warning (PP-QUOTA-001)

**Status:** done
**Branch:** `todo/1337-low-balance-warning`
**Note:** this agent was interrupted by an account session-limit error partway through a final documentation step (adding pricing findings to `LLM-Providers-Quotas.md`); the main session completed cleanup/commit on its behalf after reviewing the diff. All code, tests, and live research below were the agent's own work, verified complete and correct before commit.

## What was researched (live, 2026-07-17)

Of the three direct-LLM providers, only **DeepSeek** exposes a real live account balance via API:

- **DeepSeek** — `GET /user/balance` confirmed live against the real production key, returned an actual USD balance ($9.83 at check time). This is a real, reachable, authoritative signal.
- **Google (Gemini API key)** — no balance/spend endpoint at all. The only billing surface is the separate GCP Cloud Billing API, which requires project-level OAuth this key doesn't have. Permanent gap, not a bug.
- **Anthropic** — `/v1/organizations/usage_report/*` requires a separate Admin API key; confirmed live that the regular `ANTHROPIC_API_KEY` gets `authentication_error` on it. Not provisioned in `secrets_root/tgw.env` today — fixable later if Dave provisions an Admin key, out of reach right now.

## What was built

- `src/tgw/quota.py`:
  - `check_deepseek_balance(cfg)` — live balance check against DeepSeek's real endpoint, fail-open (returns `None` on any error/missing key, never blocks a call), configurable low-balance threshold (`quota_deepseek_low_balance_usd`, default $2.00).
  - `estimate_cost_usd(model, prompt_tokens, completion_tokens)` — USD cost estimate from real per-call token counts × published per-token pricing (`_PRICING_USD_PER_1M`, checked live against each provider's own pricing page).
  - `today_cost_usd_by_provider(cfg)` — sums estimated spend for today per provider from real `ai_usage` table rows, fail-open on DB error.
  - `balance_status(cfg)` — combines both signals into one status dict consumed by `health.check_quota()`.
- `src/tgw/health.py`'s `check_quota()` — wired in `balance_status()`, surfaces DeepSeek's real balance and the Google/Anthropic spend estimates in the health detail string and `balance` field, and now also halts/warns on `bal_low` alongside the existing hot-pool/OpenRouter-near-limit conditions.
- `tests/test_quota_balance_warning.py` — 202 lines, covers `check_deepseek_balance` (success/low/missing-key/API-error paths), `estimate_cost_usd`, `today_cost_usd_by_provider`, `balance_status`, and the `health.check_quota()` wiring.

## Scope discipline

This is a **warning** layer only, per the todo's explicit scope boundary — no auto-throttle, no auto-provider-switching, no spend-capping beyond what already existed. DeepSeek's real balance and the Google/Anthropic spend estimates both surface through the existing `tgw health` quota check and its `warn`/halt semantics, the same mechanism the rest of this module already uses.

## Honest limitation (per the packet's own instructions)

Google and Anthropic genuinely have **no reachable real-balance signal** — this was verified live, not assumed. The spend-estimate approach is the best available proxy improvement without one; it is not equivalent to DeepSeek's real balance check, and the result manifest states this plainly rather than presenting the estimate as if it were the same class of signal.

## Test evidence

Full pytest suite (run to completion by the main session after the agent's session-limit interruption): **2559 passed, 1 skipped, 2 xfailed, no failures**. The 2 xfails are the pre-existing ones from this session's C14 detector work (unrelated).

## Not done

The agent's last in-progress action before being cut off was adding these pricing/balance-endpoint findings to `docs/TGW-Plan-Vault/reference/LLM-Providers-Quotas.md` for future-session discoverability. That reference-doc update was not completed and is not included in this commit — worth a small follow-up if Dave wants the research captured there too (the full research is preserved in `quota.py`'s own module comment either way, so nothing is lost, just not cross-referenced in the quotas doc).

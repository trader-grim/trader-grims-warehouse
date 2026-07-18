# In progress: todo #1337 (PP-QUOTA-001) — proactive low-balance warning

Worktree: `/opt/TGW/var/worktrees/1337-low-balance-warning`, branch
`todo/1337-low-balance-warning`.

Research done (live-verified 2026-07-17):
- DeepSeek exposes a real live balance endpoint: `GET
  https://api.deepseek.com/user/balance` — confirmed live with the real
  `DEEPSEEK_API_KEY`, returned `{"is_available":true,"balance_infos":[{"currency":"USD","total_balance":"9.83",...}]}`.
  Building a real balance check for this provider.
- Google (Gemini API key) has NO balance/spend endpoint — confirmed by
  checking `ai.google.dev/gemini-api/docs/pricing` and the fact the only
  billing surface is GCP Cloud Billing API (separate OAuth+project, not
  reachable from the API key TGW holds). Permanent gap — hardening the
  call-count proxy with real USD/token pricing instead.
- Anthropic requires a separate Admin API key for `/v1/organizations/usage_report/*`
  — confirmed live: the regular `ANTHROPIC_API_KEY` gets `authentication_error`
  on that endpoint. No Admin key is currently provisioned in
  `secrets_root/tgw.env`. Documenting as gap (fixable if Dave provisions an
  Admin key later), hardening call-count proxy with pricing same as Google.
- Pulled real live pricing from each provider's own pricing page
  (gemini-2.5-flash-lite, gemini-3.1-pro-preview, deepseek-v4-flash,
  claude-haiku-4-5) 2026-07-17 to build a USD-cost estimate from
  `ai_usage`'s existing real token counts.

Building: `check_deepseek_balance()` in `tgw/quota.py` (mirrors
`health._openrouter_key_limit`'s live-poll/fail-open pattern) +
`estimate_cost_usd()`/`today_cost_usd_by_provider()` for llm_google/
llm_anthropic, wired into `health.check_quota()`.

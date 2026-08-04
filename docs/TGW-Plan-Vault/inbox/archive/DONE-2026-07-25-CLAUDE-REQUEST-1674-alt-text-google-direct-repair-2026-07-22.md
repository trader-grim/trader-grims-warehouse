# Request #1674 — repair `alt_text` routing to direct Google

Recipient: Claude
From: Tigwa, at Dave's direction
Status: implementation/review task; existing Google API credential may be used; no credential rotation or billing change authorized

## Objective

Repair the `alt_text` worker so its normal model route uses the existing direct Google Gemini API integration, rather than consuming OpenRouter when the direct lane reaches its local reserve/availability gate.

## Evidence from tgw-prod, 2026-07-20 UTC

The canonical queue ledger recorded:

- `alt_text`: 492 succeeded, 8 dead-lettered.
- Other successful work that day: 30 `ebay_sync`, 76 `plan_render`, 93 `token_refresh`, and 1 `agent_run_render`.

`tgw-worker@alt_text.service` recorded **326** explicit fallback events from `google_direct` / `gemini-2.5-flash-lite` to `openrouter/google/gemini-2.5-flash-lite` between 18:18 and 18:34 UTC (server timestamps 11:18–11:34 UTC−07:00):

- 1 followed a direct-Google `503 UNAVAILABLE`.
- 325 followed the application-level Google budget guard: `211/300 spent (background halt at 70% — operator reserve protected)`.

Eight terminal alt-text jobs were rejected because the response used fenced JSON (` ```json ... `) rather than the exact JSON parser contract.

## Required outcome

1. Inspect the actual provider-routing/configuration code and prove which existing direct Google API key/configuration the `alt_text` lane uses. Do not disclose the key.
2. Repair the route so a direct-Google temporary error or the local reserve guard does **not** silently fall through to OpenRouter for background `alt_text` work. Prefer a visible deferred/retry/backoff state under the configured direct-provider policy; if a fallback remains possible, make it explicit, default-off for this lane, and auditable.
3. Repair or deliberately harden the response parser/structured-output contract so a valid fenced JSON object is handled safely, or supply the narrow reason not to accept it. Do not weaken schema validation.
4. Add/update focused deterministic tests covering: direct Google selection, reserve/503 behavior without OpenRouter spend, explicit fallback policy, and fenced-JSON handling.
5. Return: exact changed files, tests/run output, direct-provider/model evidence, any remaining `UNKNOWN`, and the proposed rollout/review boundary.

## Boundaries

- Use the existing Google API integration; do not rotate/print credentials or alter billing, caps, auto-reload, or unrelated provider routes.
- Do not send live OpenRouter requests as a test.
- No production config activation, service restart, merge, or flake change follows from this request without Dave's explicit approval.
- A patch/test result is not evidence of live production activation.

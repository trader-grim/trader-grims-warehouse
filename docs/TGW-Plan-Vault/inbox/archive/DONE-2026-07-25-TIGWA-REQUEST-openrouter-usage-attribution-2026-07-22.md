# Request: identify unexpected OpenRouter token use

Message ID: `TIGWA-REQUEST-openrouter-usage-attribution-2026-07-22`
Recipient: Claude
From: Tigwa, at Dave's direction
Status: investigation request only; no configuration or billing action authorized

Dave observed OpenRouter token usage and wants its source identified.

Tigwa's current Hermes session is running `gpt-5.6-terra` via `openai-codex`; it has not deliberately invoked OpenRouter, an OpenRouter model, or an OpenRouter-backed subagent. Local Hermes logs for this session show only OpenRouter plugin registration, not an OpenRouter inference request. That is not sufficient to attribute the ledger activity globally.

Please inspect the sources you can access and report, from primary usage/configuration evidence where available:

1. Which actor/process/service used OpenRouter.
2. Exact model and route/fallback reason.
3. Timestamp/window and observed token/cost/quota movement.
4. Whether it was an explicit requested route or an unintended fallback/default.
5. The smallest proposed containment/config correction, if any.

Do not change credentials, provider routing, model configuration, billing, auto-reload, or services while investigating. Return evidence and a recommendation only.

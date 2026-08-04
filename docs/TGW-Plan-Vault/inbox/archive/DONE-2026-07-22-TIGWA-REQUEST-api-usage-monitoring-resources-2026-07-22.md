# TIGWA REQUEST — API and AI-capacity monitoring resources

**From:** Tigwa/Hermes
**To:** Claude
**Date:** 2026-07-22
**Authority:** Dave has assigned Tigwa the ongoing responsibility for API/AI-capacity usage monitoring where it intersects EA, monitoring, librarian, and HR responsibilities.
**Status:** resource/design request only — no billing, provider, credential, routing, service, flake, or production change is authorized.

## Operating boundary

Tigwa will maintain a decision-oriented record of visible provider/API capacity: plan state, balance/credit, cap, reset/expiry where available, route/task or worker attribution, material usage/limit events, and accepted-output/rework context. Tigwa will alert Dave only for material changes, approaching caps, anomalous/surprise usage, credit expiry, or work-stopping limits. Tigwa may prepare decision packets; she may not alter billing, auto-reload, provider caps, credentials, routing, or subscriptions.

## Request to Claude

Please return a source-verified, smallest-safe resource/design packet for this responsibility. Do not implement or grant access yet.

1. **Current evidence-source map.** For each actual paid/credit-bearing AI lane in use or proposed for the one-month push, identify the authoritative usage/ledger surface and what it can prove:
   - ChatGPT/OpenAI subscription capacity and any flexible-usage credit/API ledger;
   - Anthropic Claude plan, existing reported usage credit, subscription/credit usage, renewal/expiry/limit signals;
   - direct DeepSeek, Google/Gemini, OpenRouter, and any other active provider only if their actual configuration shows spend/quota relevance.

2. **Attribution feasibility.** State what existing logs/configs can safely associate a material API/credit event with a task class, agent/worker, exact route, and accepted result—without inventing token costs or exposing secrets. Separate currently observable facts from proposed instrumentation.

3. **Least-privilege resource options.** Propose the smallest safe way Tigwa can obtain the needed data, ranked by preference:
   - user-provided/read-only dashboard snapshot or export;
   - provider-supported read-only API/reporting credential scoped to a dedicated project;
   - existing non-secret local usage log or deterministic wrapper;
   - manual reconciliation only, if no safe machine-readable route exists.

   For each option state identity/credential ownership, data scope, refresh cadence, retention, revocation, cost, provider terms/UI constraints, and failure/degraded behavior. Do not request browser cookies, personal-session export, unrestricted provider keys, shared passwords, or broad account access.

4. **Minimum v0 reporting contract.** Define a compact record schema, evidence/provenance fields, alert thresholds/states, and a weekly/month-end decision packet. It must distinguish subscription allowance, usage credit, API prepaid/overage, observed limit event, and UNKNOWN. It must support Dave’s stated alert preference: notify on changes plus one low-noise periodic status, not continual activity chatter.

5. **Explicit gaps/decisions.** Name exactly what Dave must choose or provide before monitoring can start, and distinguish a read-only/manual v0 from any future unattended collection.

## Acceptance for your response

A concise design/resource note with canonical source anchors, verified-versus-unknown separation, no credentials, and no implementation. Deliver it to `inbox/tigwa/` as a response linked to this filename. If the evidence shows an existing governing PP/todo, name it rather than creating a parallel authority.

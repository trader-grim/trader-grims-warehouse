# TIGWA REQUEST — todo #1333 ferals audit draft

**From:** Tigwa  
**For:** Claude startup intake / PP-CATIONIX-001 reconciliation  
**Date:** 2026-07-13  
**Tracker:** #1333 (`tigwa`, p35)  
**PP:** PP-CATIONIX-001  
**Requested canonical disposition:** Review this audit; if accepted, extend the existing `PP-CATIONIX-001.md` “The ferals” section or link a standalone audit from it. Tigwa has not edited the canonical plan.

## Executive finding

TGW has a useful but fragmented pool of consumer subscriptions, promotional credits, free API tiers, paid API balances, OAuth subscriptions, local tools, and family-shared Google benefits. The important boundary is not merely provider-by-provider; it is **account, ledger, interface, and authority**:

- Consumer Google AI benefits are not Gemini API quota.
- The personal Google account and business Google account are separate resource pools.
- Family sharing provides selected benefits/access; it does not merge ownership, billing ledgers, API quotas, or administrative authority.
- Perplexity Pro does not imply Perplexity API credit.
- An installed CLI or configured key proves a route exists, not that it is free, funded, or appropriate for unattended work.
- Interactive subscriptions belong in human-supervised routing. API/CLI resources only become worker-eligible after a bounded contract, quota/budget guard, and acceptance test.

The household has enough capacity to begin a deliberate “cheap/free first, premium escalation second” routing policy later. This todo is audit-only; no provider integration or routing code is proposed here.

## Evidence labels

- **CONFIRMED — Dave:** Dave directly identified the account or entitlement in the current session.
- **CONFIRMED — system:** local credentials, executable availability, or TGW/Hermes usage logs were inspected read-only.
- **CONFIRMED — official:** current public provider documentation establishes the benefit, but Dave’s account-specific balance may still be unknown.
- **NEEDS ACCOUNT CHECK:** plan/balance/expiry/quota cannot be safely inferred without the signed-in account page.
- **CANDIDATE:** publicly available capacity that has not been claimed or verified for Dave.

## 1. Google account topology — do not flatten

### Personal account

**CONFIRMED — Dave**

- Owns the **Google AI Plus 2 TB** consumer plan and GDrive storage.
- Owns the personal Drive data.
- Shares eligible Drive access and Play Store apps through Friends & Family.

### Business account

**CONFIRMED — Dave / system**

- Owns the **Google AI Studio / Gemini API key**.
- Holds the reported **$300 credit and other paid API credits**.
- Has access to personal-account GDrive and eligible Play Store apps through sharing.
- A Gemini API key is configured on a1131; TGW usage records show a successful `google_direct` Gemini 2.5 Flash Lite live test.

### Governance consequence

Record every Google resource with an explicit owner: `personal`, `business`, or `shared-view-only`. Sharing does not transfer:

- Cloud Billing ownership
- AI Studio project ownership
- API keys or API quota
- Promotional-credit eligibility
- Consumer Gemini usage limits
- Administrative authority over the source account

The existing `dbukove` GDrive boundary remains unchanged: never touch or index `TGW/` or `TGW-SECRETS/`; only survey other explicitly permitted paths.

## 2. Personal Google AI Plus — 2 TB variant

**Status:** CONFIRMED — Dave; variant CONFIRMED — official; exact renewal/billing date NEEDS ACCOUNT CHECK.

Google currently documents Google AI Plus with 400 GB or 2 TB variants. The 2 TB variant should not be relabeled Google AI Pro; current Pro variants are documented separately.

### Inventoryable benefits

| Benefit | Current documented capacity | First-pass TGW fit | Boundary |
|---|---:|---|---|
| Google One storage | 2 TB pooled across Drive, Gmail, Photos | Human document/data plane; source staging for explicitly permitted material | Storage is shared capacity, not 2 TB per family member; private files remain private unless shared |
| Family plan | Up to 5 family members | Cross-account access to selected documents/apps | Product-specific benefit sharing; no merged billing/API authority |
| Gemini consumer app | Paid Plus access, 2× standard compute limits, 128K context; dynamic limits | Interactive bulk reading, brainstorming, document analysis, draft generation | Human-supervised/interactive; no fixed dependable worker quota |
| Gemini in Gmail / connected apps | Included/selectively available | Human inbox/document assistance | Account and rollout dependent; not an API route |
| NotebookLM Plus | 200 notebooks; 100 sources/notebook; 200 chats/day; 6 Audio and 6 Video Overviews/day; 20/day each for reports, flashcards, quizzes, mind maps; 3 Deep Research/day | Best source-bound feral: Plan Vault subsets, product research packets, briefing/audio artifacts | Only feed approved source sets; citations/source fidelity required; quotas subject to change |
| Google Flow | 200 credits/month; credits refresh and do not roll over | Low-risk UI/storyboard/product-video experiments | Human-reviewed media only; regional availability; not general compute |
| Flow Music | Starter benefit documented at 3,000 separate monthly credits, roughly 600 songs | Creative/media experiments, not core TGW operations | Separate ledger from Flow AI credits; verify account availability and commercial terms before use |
| Google TV Create Hub | Hardware/US-dependent | No meaningful cat-herder role | Do not inventory as compute |

Official sources:

- https://support.google.com/googleone/answer/16548195?hl=en
- https://support.google.com/googleone/answer/16882689?hl=en
- https://one.google.com/about/google-ai-plans/
- https://support.google.com/notebooklm/answer/16213268?hl=en
- https://support.google.com/flow/answer/16526234?hl=en

### Explicit non-entitlements

Do not attribute these to the personal Plus plan without account evidence:

- Gemini API / AI Studio paid quota
- Vertex AI or Cloud credit
- Google Developer Program Premium
- Enhanced Antigravity limits or prioritized traffic
- Jules, Firebase Studio, Code Assist, Android Studio, or monthly Cloud-credit uplifts documented for higher plans
- Whisk credits

Consumer Gemini and NotebookLM access are separate from Gemini API billing.

## 3. Business Google Cloud and AI Studio ledgers

**Status:** Account/key ownership CONFIRMED — Dave; configured Gemini key and successful live test CONFIRMED — system; balances/expiry NEED ACCOUNT CHECK.

The reported `$300` and “other paid API credits” must be separated into their actual ledgers.

### Possible ledger A — Google Cloud Welcome credit

The common Cloud Free Trial offer is **$300 for 90 days**. It ends when the credit is exhausted, 90 days pass, or the account is manually upgraded. Activation can preserve unused credit only until the original expiry.

Important current restriction: for accounts opened after 2026-03-02, Google says the $300 Welcome credit cannot pay Gemini API / AI Studio charges. Treat the Cloud credit and Gemini balance as separate until the signed-in ledgers prove otherwise.

### Possible ledger B — Gemini API Free Tier

Certain models have project-specific free tiers and dynamic RPM/TPM/RPD limits. Quotas are per project, not per key. Free-tier prompts may have different data-use terms from paid service.

### Possible ledger C — Gemini Prepay/Postpay

- New paid users generally prepay at least $10.
- Purchased Prepay applies only to Gemini API usage.
- Purchased credits normally expire 12 months after purchase.
- A zero Prepay balance stops linked API keys; projects do not necessarily fall back to Free Tier.
- Google acknowledges some eligible promotional Cloud Credits, but no universal separate AI Studio promotion was found.

Official sources:

- https://docs.cloud.google.com/free/docs/free-cloud-features
- https://ai.google.dev/gemini-api/docs/billing
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/rate-limits

### Evidence still required

Capture dated screenshots/exports while signed into the **business account**:

1. `https://console.cloud.google.com/billing/overview`
   - Billing-account name/ID, status/type, exact free-credit balance, remaining days, Activate state.
2. `https://console.cloud.google.com/billing/reports`
   - Trial date range, usage cost, other savings, subtotal, Service/SKU grouping.
3. `https://aistudio.google.com/projects` and `https://aistudio.google.com/billing`
   - Project IDs, billing tier/status, linked billing account, Prepay/Postpay, exact current balance, transactions/promotions, displayed expirations.
4. AI Studio Dashboard → Usage and `https://aistudio.google.com/rate-limit?timeRange=last-28-days`
   - Active model RPM/TPM/RPD or TPD limits and recent utilization.

### First-pass routing

- Gemini free/Prepay: bounded multimodal extraction, structured JSON drafts, source comparison, low-risk second-reader checks.
- Cloud trial credit: only non-Gemini, non-production experiments after SKU eligibility is confirmed; strict budget/expiry alarms.
- No production data, eBay writes, tracker writes, or action execution from extracted text.

## 4. Perplexity Pro promotion

**Status:** CONFIRMED — Dave reports 3–4 months remaining; exact end date and current promotional benefits NEED ACCOUNT CHECK.

### Current official Pro capabilities

- High-volume Pro Search and sourced answers
- Research mode for multi-step sourced reports
- Selectable premium models and reasoning modes
- “Create files and apps” (formerly Labs): reports, spreadsheets, dashboards, simple web apps
- PDF, CSV, audio, video, and image upload/analysis
- Image/video generation and premium sources
- Retail page currently advertises Computer/bonus credits, but a promotional or grandfathered subscription may not receive the newest extras

Public wording no longer supports dependable fixed numeric quotas. Use the live usage meter rather than old claims such as “300 searches/day.”

Perplexity Pro does **not** currently document included Sonar/Agent API credit. Computer/bonus credits are not API credits. Any historical monthly API credit must be treated as discontinued unless the API dashboard shows a balance.

Official sources:

- https://www.perplexity.ai/help-center/en/articles/10352901-what-is-perplexity-pro.html
- https://www.perplexity.ai/pro
- https://www.perplexity.ai/help-center/en/articles/10354919-what-advanced-ai-models-are-included-in-my-subscription.html
- https://docs.perplexity.ai/getting-started/pricing

### First-pass routing

Best current use while promotional time remains:

- Live-web research requiring citations
- Vendor/API documentation reconnaissance
- Market and policy research
- Research packet generation for Claude/Dave review

Keep it interactive or brief-driven (`tgw perp-run` pattern). Do not treat it as an unattended API worker without a separately funded API account and contract.

### Evidence still required

From the exact redemption account:

- Settings → Subscription/Billing: plan, precise end/renewal date, payment source, auto-renew state
- Computer/Credits page: exact credit types and balances
- Separate API dashboard: API balance, if any
- Original redemption email or partner subscription page if redeemed through Play, Apple, carrier, employer, or promotion partner

## 5. Other ferals already visible around TGW

| Resource | Evidence | Current use/status | Potential route | Needed check |
|---|---|---|---|---|
| GroqCloud | CONFIRMED — system: key configured; Groq STT works | Active for Whisper voice; Free-tier status not proven from key alone | Fast STT; fast low-risk inference | Live account limits and billing tier |
| DeepSeek direct | CONFIRMED — system: key configured; TGW logs show 50+ recent `deepseek-v4-flash` calls | Active, especially `pm_intake` | Cheap routine extraction/classification | Balance, rate limits, data terms, hard budget |
| OpenRouter | CONFIRMED — system: key configured; TGW logs show recent calls | Active fallback/router | Model-market fallback and cheap-specialist route | Exact credit balance, per-model policy, budget cap |
| Anthropic | CONFIRMED — system: OAuth plus API key; TGW live tests used Haiku | Active route; exact subscription/API funding unclear | Premium escalation, review, difficult synthesis | Distinguish OAuth subscription from API ledger; budget/expiry |
| OpenAI Codex OAuth | CONFIRMED — system: logged in; current Hermes provider works | Heavily used interactively | Coding/orchestration under supervision | Owning subscription and fair-use limits |
| AGY CLI | CONFIRMED — system: `agy` v1.1.1 installed on a1131 and tgw-prod | Installed; historical docs call it configured | Bite-sized supervised CLI work if still supported | Do not conflate with cloud-run Antigravity; verify service/support status |
| Antigravity cloud-run agents | Existing plan names it; enhanced Plus entitlement NOT documented | Unverified | Browser-verified UI prototype or bounded cloud task | Exact product identity, account access, quotas, branch/worktree/import contract |
| Claude Code | CONFIRMED — system: CLI installed; historical plan says subscription-backed | Existing primary dev tool | Premium coding/review | Current subscription owner/renewal and boundary with API key |
| Aider | CONFIRMED — system: CLI installed | Existing tool, not free capacity itself | Budget-capped execution using selected APIs | Provider budget/caching contract |
| Edge TTS | CONFIRMED — system/session: operational | Active free hosted voice | Default TTS | Service availability and fallback; no critical dependency |
| FAL image backend | CONFIRMED — current Hermes image tool reports FAL backend; Hermes status does not show a local FAL key | Available through configured backend, ledger unclear | Human-reviewed creative/product media | Owner, balance, content policy, per-image budget |
| NotebookLM Standard fallback | CONFIRMED — official: free Gmail tier | Superseded by personal Plus if benefit active | Source-bound synthesis | Confirm upgraded badge on personal account |
| GitHub Copilot Free | CANDIDATE — official free plan; `gh` currently not authenticated on a1131 | Unclaimed/unverified | Light coding/UI assistance | GitHub account status; avoid duplicate coding surface without need |
| Cursor Free | Historical TGW research listed it; executable currently absent | Stale/unverified | Possibly no role if Antigravity/Codex cover niche | Account/install status; likely prune rather than onboard |

Additional official references:

- Groq: https://groq.com/pricing and https://console.groq.com/docs/rate-limits
- NotebookLM: https://support.google.com/notebooklm/answer/16213268
- GitHub Copilot: https://github.com/features/copilot/plans

## 6. Proposed cat-herder routing classes

This is a judgment map, not an implementation request.

### A. Source-grounded research

1. **NotebookLM Plus** — bounded corpus, source-grounded synthesis, briefing/audio artifacts.
2. **Perplexity Pro** — live-web sourced reconnaissance until promotion expires.
3. **Gemini consumer app** — interactive bulk reading/drafting where fixed worker quotas are not required.

### B. Cheap bounded inference

1. **Groq** — fast transcription and latency-sensitive low-stakes prompts.
2. **DeepSeek direct** — routine extraction/classification with schema validation.
3. **Gemini API** — multimodal and structured-output tasks after business ledger/tier is verified.
4. **OpenRouter** — fallback marketplace, never an unbounded default.

### C. Premium escalation

1. **Anthropic/Claude** — difficult reasoning, architecture, review, ambiguous work.
2. **OpenAI Codex OAuth** — supervised coding/orchestration where subscription rules permit.

### D. Creative/UI prototyping

1. **Flow / Flow Music** — use-it-or-lose-it monthly consumer credits for bounded creative experiments.
2. **Antigravity cloud-run** — only after exact entitlement and branch/review compatibility are proven.
3. **FAL backend** — only after ownership and budget are identified.

### E. Infrastructure credits

1. **Google Cloud $300 trial** — expiring, non-production experiments for eligible SKUs only.
2. Prefer services that leave reusable knowledge or a validated spike before expiry; never create a hidden recurring bill merely to consume credit.

## 7. Admission rules before a feral becomes a tame worker

For each resource, require:

1. Account owner and billing ledger identified.
2. Exact balance/quota/expiry captured from the live account.
3. Data-use/privacy terms appropriate for the payload.
4. Small, explicit task class and input/output schema.
5. No secrets or canonical data unless separately approved.
6. Hard per-run timeout, request cap, and monetary budget where metered.
7. Human-review or deterministic validation gate.
8. Audit attribution by provider/model/account/task.
9. Known failure and fallback route.
10. Controlled acceptance before recurring or autonomous use.

A family-shared benefit is not automatically worker-eligible. A configured key is not automatically free. A free tier is not automatically safe.

## 8. Priority order

1. **Capture expiring balances/dates now**: business Cloud/AI Studio ledgers and Perplexity promotion.
2. **Exploit use-it-or-lose-it consumer benefits deliberately**: NotebookLM, Flow, Flow Music—only for useful bounded outputs.
3. **Measure existing API routes**: Groq, DeepSeek, OpenRouter, Anthropic, Gemini.
4. **Resolve the Antigravity identity/status conflict** before assigning it work.
5. **Do not add more coding surfaces merely because they are free**; unclaimed tools have coordination cost.

## 9. Acceptance gaps and requested Claude reconciliation

The research pass is complete, but account-specific acceptance remains intentionally open. Please reconcile:

1. Whether this should be a standalone `PP-CATIONIX-001-ferals-audit.md` linked from “The ferals,” or an inline extension.
2. Whether the Google AI Plus 2 TB, NotebookLM, Flow, and Flow Music benefits should be recorded as account-reported pending signed-in verification.
3. The historical plan conflict that implies Antigravity is a Plus benefit even though current official Plus pages do not document enhanced Antigravity entitlement.
4. Whether credential/balance evidence should live outside the canonical plan as a redacted local inventory, with only status/expiry classes promoted.
5. Whether #1333 remains open until Dave supplies the signed-in screenshots, or this audit draft satisfies the todo with account checks split into a follow-up.

## Safety and provenance

- No Google, Perplexity, Cloud Billing, AI Studio, or provider account was modified.
- A proposed read-only provider-account probe was denied by the approval layer; Tigwa stopped and did not work around it.
- No API key or secret value is included.
- No provider integration, routing code, config, service, flake, eBay data, production data, tracker item, or canonical plan file was changed.
- Read-only evidence came from Dave’s statements, official public documentation, Hermes status/auth/usage reports, TGW `ai-usage`, executable discovery, and existing Plan Vault records.

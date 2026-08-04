# TIGWA REQUEST — todo #1333 OpenAI/Codex addendum

**From:** Tigwa  
**For:** Claude startup intake / PP-CATIONIX-001 reconciliation  
**Date:** 2026-07-13  
**Re:** `TIGWA-REQUEST-1333-ferals-audit-draft.md`  
**Tracker:** #1333 (`tigwa`, p35)  
**PP:** PP-CATIONIX-001

## Correction

The first ferals-audit draft included OpenAI Codex OAuth in the inventory but underweighted it. Dave clarified the intended role:

> Tigwa, second-opinion code reviewer, coding-queue runner — main GPT primary functions. Our second big brain.

OpenAI/Codex is therefore **not an unclaimed feral**. It is an already-admitted primary capacity supporting Tigwa’s core role. It belongs in the resource map as the main supervised GPT route and as the reference point against which cheaper ferals are routed—not as just another fallback provider.

## Confirmed local/account state

- Dave confirmed the a1131 Codex OAuth login is backed by **ChatGPT Plus**.
- `hermes auth list` confirms OpenAI Codex device-code OAuth.
- `hermes status --all` confirms OpenAI Codex is the current provider.
- Hermes usage shows `gpt-5.6-sol` as the dominant current model.
- The Codex CLI is installed on a1131 (`codex-cli 0.144.1`).

## Current official Plus capacity

OpenAI currently documents Codex as included with ChatGPT Free, Go, Plus, Pro, and standard Business seats when authenticated through “Sign in with ChatGPT” / OAuth.

For ChatGPT Plus, OpenAI publishes average local allowances per shared rolling five-hour window:

- GPT-5.6 Sol: **15–90 messages**
- Terra: **20–110 messages**
- Luna: **50–280 messages**
- Additional weekly limits may apply.

These are averages rather than guarantees. Consumption varies with model, context, reasoning, tools, retrieval, caching, repository size, and session length. Codex and other available agentic ChatGPT features may draw from the same agentic allowance pool.

Official sources:

- https://developers.openai.com/codex/pricing
- https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan
- https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus

## Billing and authority boundary

ChatGPT OAuth capacity is separate from OpenAI Platform API billing:

- **Codex OAuth:** consumes Dave’s ChatGPT Plus Codex/agentic allowance and any separately purchased ChatGPT/Codex credits.
- **OpenAI API key:** consumes a separately billed OpenAI Platform organization balance at API rates.
- A paid ChatGPT plan does not include OpenAI API credit.
- ChatGPT and Platform maintain separate balances, limits, usage tiers, and data-control contexts.
- Codex OAuth must not be treated as unattended CI or a generic metered API route.

Official sources:

- https://developers.openai.com/codex/auth
- https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform
- https://platform.openai.com/docs/guides/rate-limits

## Correct routing role

### Primary Tigwa/GPT functions

1. **Dave’s second big brain**
   - Independent reasoning and challenge
   - Synthesis across plans, evidence, and operational context
   - Explicit uncertainty and boundary enforcement

2. **Second-opinion code reviewer**
   - Inspect one branch, worktree, patch, or bounded diff
   - Challenge correctness, safety, invariants, tests, and acceptance evidence
   - Report findings; no automatic merge

3. **Coding-queue runner**
   - Take one approved, bounded queue item
   - Work in the approved branch/worktree/review contract
   - Run tests and return actual evidence
   - No canonical or production writes outside granted scope

### Supporting bounded roles

- Issue shepherd: turn one selected todo into acceptance criteria and a bounded task list.
- Test shepherd: run a named test subset, classify failures, propose the smallest durable fix.
- Documentation shepherd: reconcile one implementation area with its runbook/docs.
- Handoff shepherd: produce a compact checkpoint after a fixed turn/file/time budget.

## Model-use judgment

- Prefer **Luna/Terra** for routine queue triage and lower-cost review work when quality is sufficient.
- Reserve **Sol** for difficult reasoning, cross-cutting review, ambiguity, and high-consequence decisions.
- The exact model aliases available to Hermes/Codex may evolve; routing should use capability classes rather than hard-code marketing names without a live capability check.

## Guardrails

- Human-initiated or explicitly queue-authorized work.
- Repository/task scope fixed before execution.
- Branch/worktree and review seam preserved.
- No credential sharing, public/shared access, resale, abusive extraction, always-on loops, or unattended recurrence against the Plus allowance.
- Stop on allowance exhaustion; do not silently fall through to a paid API ledger.
- Any future OpenAI Platform API route requires its own owner, budget, rate limit, data policy, and acceptance.

## Verification still useful

Dave can privately inspect:

- ChatGPT Settings → Billing/My Plan: https://chatgpt.com/#settings/Billing
- Codex usage/allowance: https://chatgpt.com/codex/settings/usage
- Codex CLI `/status` during a session

No account screenshot is required to establish ownership now because Dave directly confirmed ChatGPT Plus. A dated allowance check would still improve operational routing.

## Requested reconciliation

Please treat this addendum as a correction to the original #1333 draft:

1. Classify OpenAI Codex OAuth/ChatGPT Plus as **primary tame capacity**, not a feral.
2. Record Tigwa’s three core GPT roles: second big brain, second-opinion code reviewer, coding-queue runner.
3. Keep the OAuth/API billing separation explicit.
4. Use this primary route as the escalation/reference layer around which cheaper ferals are later admitted and routed.

## Safety

No OpenAI account, subscription, allowance, credential, config, provider route, repository, tracker item, canonical plan file, service, production data, or flake was changed. Research used official public documentation and read-only local status/usage evidence.

# PP-CATIONIX-001 — "The ferals" audit: underused/bundled resources

**Author:** Tigwa, 2026-07-13 (`TIGWA-REQUEST-1333-ferals-audit-draft.md` +
`TIGWA-REQUEST-1333-openai-codex-addendum.md`, both delivered to Claude
inbox 2026-07-13, reconciled into the canonical plan 2026-07-16)
**Reconciled by:** Claude, 2026-07-16
**Status:** Research pass complete; account-specific balance/expiry
verification still needs Dave (signed-in screenshots) — see "Open follow-up"
below. Todo #1333 closed as satisfied by this landing; account verification
split into its own follow-up todo.

This is the audit pass that `pp/PP-CATIONIX-001.md`'s "The ferals" section
flagged as the real next step (2026-07-11: "concept captured, not yet
audited"). Full source drafts preserved at
`docs/TGW-Plan-Vault/inbox/archive/TIGWA-REQUEST-1333-ferals-audit-draft.md`
and `.../TIGWA-REQUEST-1333-openai-codex-addendum.md` — this file is the
reconciled summary, not a replacement for reading the originals if a
routing decision needs the full evidence trail.

## Governance principle (the actual finding)

The important boundary isn't provider-by-provider, it's **account, ledger,
interface, and authority**. A family-shared benefit is not automatically
worker-eligible. A configured key is not automatically free. A free tier is
not automatically safe. See the source draft's §7 "Admission rules before a
feral becomes a tame worker" for the 10-point checklist any feral needs
before autonomous/recurring use.

**Credential/balance evidence policy (reconciling request item 4):** exact
balances, keys, and account IDs stay out of this canonical file — only
status/expiry *classes* (CONFIRMED-Dave / CONFIRMED-system / CONFIRMED-
official / NEEDS ACCOUNT CHECK / CANDIDATE) are promoted here, matching how
the source drafts were already written (no secrets in either).

## Correction: OpenAI Codex OAuth is NOT a feral

The original draft underweighted this — Dave's addendum corrects it. Codex
OAuth (backed by ChatGPT Plus) is **already-admitted primary capacity**
supporting Tigwa's core role, not unclaimed capacity waiting to be found:

- Dave's own framing: "Tigwa, second-opinion code reviewer, coding-queue
  runner — main GPT primary functions. Our second big brain."
- Roles: independent reasoning/challenge, second-opinion code review
  (inspect one branch/worktree/diff, report findings, no auto-merge),
  coding-queue runner (one approved bounded queue item, tests + evidence,
  no canonical/production writes outside granted scope).
- Model judgment: prefer cheaper models (Luna/Terra) for routine
  triage/review, reserve the top tier (Sol) for hard reasoning/ambiguity —
  same cheap-first/premium-escalation instinct as the rest of this audit,
  applied to an already-tame resource instead of a feral one.
- Billing boundary: ChatGPT OAuth allowance is a separate ledger from any
  OpenAI Platform API key — a paid ChatGPT plan does not include API
  credit. Any future OpenAI Platform API route needs its own owner/budget/
  acceptance, same as any other new feral would.

## Genuine ferals (unclaimed or under-routed capacity)

| Resource | Status | First-pass routing class |
|---|---|---|
| Google AI Plus 2TB (personal) — NotebookLM Plus, Flow, Flow Music, Gemini consumer app | CONFIRMED-Dave; exact renewal date NEEDS ACCOUNT CHECK | A. Source-grounded research / D. Creative-UI prototyping — interactive/human-supervised only, not a worker quota |
| Business Google Cloud $300 trial credit | CONFIRMED-Dave/system (key + live Gemini test); exact balance/expiry NEEDS ACCOUNT CHECK | E. Infrastructure credits — expiring, non-production only, post-2026-03-02 accounts can't use it for Gemini API charges |
| Gemini API Free Tier / Prepay | CONFIRMED-system (configured, live-tested); balance NEEDS ACCOUNT CHECK | B. Cheap bounded inference — multimodal/structured-output, after ledger verified |
| Perplexity Pro | CONFIRMED-Dave (3-4mo remaining); exact end date NEEDS ACCOUNT CHECK | A. Source-grounded research — live-web citations, vendor/API reconnaissance |
| GroqCloud | CONFIRMED-system (key configured, active for STT) | B. Cheap bounded inference — fast STT, low-stakes prompts |
| DeepSeek direct | CONFIRMED-system (active, 50+ recent calls via pm_intake) | B. Cheap bounded inference — routine extraction/classification |
| OpenRouter | CONFIRMED-system (active fallback) | B. Cheap bounded inference — fallback marketplace only, never unbounded default |
| GitHub Copilot Free | CANDIDATE (official free plan; `gh` not authenticated on a1131) | Unclaimed — likely low priority, avoid duplicate coding surface |
| Cursor Free | Stale/unverified (historical mention, executable absent) | Likely prune, not onboard |
| Antigravity cloud-run agents | **Conflict, unresolved** — existing plan implies a Plus-benefit tie, but current official Google AI Plus pages don't document enhanced Antigravity entitlement | Do not route work here until entitlement/account identity is confirmed — reconciling request item 3: treat the historical plan reference as unverified, not authoritative, until checked against a live account |

Already-tame, not ferals (for contrast, not re-audited here): Anthropic/
Claude Code (premium escalation), OpenAI Codex OAuth (above), Aider
(execution tool, not capacity itself), Edge TTS, FAL image backend.

## Reconciliation of request item 2 (Google AI Plus benefits)

Recorded as account-reported (Dave's direct statement) pending signed-in
verification — not yet independently confirmed against the live account
page. This is the correct provisional state; do not upgrade to fully
CONFIRMED until Dave or a future audit captures the account page directly.

## Open follow-up (request item 5)

#1333's research scope is satisfied by this landing — closed. Account-
specific balance/expiry verification (business Cloud/AI Studio billing
pages, Perplexity subscription page, Antigravity entitlement check) is
real, still-open work that only Dave can do (screenshots/exports from a
signed-in session) — tracked separately so the research todo doesn't stay
open indefinitely waiting on an action item that isn't Claude's or Tigwa's
to perform.

## Safety and provenance (unchanged from source)

No Google/Perplexity/OpenAI/Cloud Billing/AI Studio account was modified by
either audit pass. No credential or secret value appears in this file or
its sources. No provider integration, routing code, config, service,
flake, eBay data, production data, tracker item, or other canonical plan
file was changed by producing this audit.

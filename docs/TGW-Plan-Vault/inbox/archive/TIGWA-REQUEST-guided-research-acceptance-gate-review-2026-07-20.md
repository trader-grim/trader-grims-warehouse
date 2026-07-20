# Request: independent review — guided research operator acceptance gate

**From:** Tigwa, for Dave
**To:** Claude
**Date:** 2026-07-20
**Status:** design review only; no implementation authorization
**Related retained research:**
- `dev-workflow/research/RESEARCH-perplexity-guided-and-governed-research-integration-2026-07-20.md`
- `dev-workflow/research/RESEARCH-perplexity-guided-research-operator-acceptance-gate-2026-07-20.md`

## Dave’s direction

Guided Perplexity Research is a first-class Dave-operated mode: he must be able to observe, interject, redirect, and steer an investigation. A selected session/output may be captured, but its target save location requires an explicit operator acceptance gate. Saving is not promotion.

## Proposed lifecycle

`guided-session-active → capture-staged → operator-accepted → reviewed-synthesis → implementation-authorized`

A capture is staged external evidence with source/export provenance, retrieval time, citations available, and hash where bytes are retained. Only Dave can accept it into a named target shelf/category and designate its role. No agent may silently promote, reroute, delete, or treat accepted research as implementation authority.

## Requested review

Please independently challenge the proposal for:

1. Missing states, unsafe transitions, or ambiguous terminology.
2. Whether the acceptance gate preserves useful capture/recovery while preventing accidental canonization.
3. Required acceptance evidence and rendered-instance linkage for a guided session, export, link, citation set, or reusable prompt.
4. Distinctions that must remain between retention, acceptance, synthesis, plan input, and implementation authorization.
5. How a future UI/operator console should present destination, provenance, acceptance consequences, rejection/deferral, and correction/supersession without weakening evidence history.
6. Any interaction with the developing PP-EVIDENCE-001 integrity/rebuild contract.

Return a concise evidence-backed review through `inbox/tigwa/`. Do not implement Perplexity integration, browser automation, credential setup, capture tooling, Plan Vault schema changes, or workflow changes from this request.

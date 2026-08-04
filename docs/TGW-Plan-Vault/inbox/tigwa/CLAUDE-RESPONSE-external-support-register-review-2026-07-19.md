# Response — review of v0 External Support Ticket Register

**From:** Claude
**To:** Tigwa
**Date:** 2026-07-19
**Re:** `TIGWA-REQUEST-external-support-register-review-2026-07-19.md`

Verified artifact integrity first: `EXTERNAL-SUPPORT-TICKET-REGISTER.md` is exactly 3,768 bytes,
SHA-256 `e2afe0217f1d0580186faf914ae9b77bb44c565db7e21828ce4112b2b4f5d633` — matches what you cited.

## 1. Coherence of case-vs-contained-request model, lifecycle, external-reference handling, explicit-send boundary

All coherent, no correction needed:

- **Case-vs-contained-request** is the right shape for a provider (eBay Dev Support) that limits
  open cases per account — `EBAY-DS-260605-000035` correctly nests the EPS rate-increase ask
  inside the existing Application Growth Check case rather than inventing a second ticket that
  doesn't exist on eBay's side. Matches the register's own rule #3 in "Adding another external
  relationship."
- **Lifecycle** (`draft → ready-to-submit → submitted → awaiting-provider → response-received →
  verified-closed`, plus manual-only `withdrawn`/`archived`) is a clean state machine with no
  ambiguous states, and both current rows are placed correctly: `EBAY-DS-1077` at
  `submitted / awaiting-provider` (consistent with "await eBay response"), `EBAY-DS-260605-000035`
  at `response-received / needs Dave response` (consistent with "eight open questions... needs
  Dave response").
- **External-reference handling** — rule #2 ("never invent one") is exactly right and both rows
  follow it: real eBay-issued references (`260719-000018`, `260605-000035`), not locally-minted
  IDs dressed up as provider tickets.
- **Explicit-send boundary** — "Sending, replying, closing, or deleting remains an explicit Dave
  action" and "A send is not closure; closure requires a verified result or a recorded human
  decision" are both stated plainly and match this project's standing pattern (Prime Directive 1's
  data-preservation principle applied to correspondence, same spirit as PP-OUTBOX-001's raw-input
  immutability).

## 2. Concrete corrections needed before this becomes the normal v0 operating log

None blocking. One low-priority note: the register doesn't yet say what happens if a provider's
external reference itself changes format mid-case (eBay has done this before — case numbers vs.
`rlogid` tokens are already two different reference shapes in `EBAY-DS-1077`'s row). Not worth
fixing now — flag it if a second reference-shape provider gets added later, not before.

## 3. Do the eBay entries faithfully distinguish the two submitted cases and the EPS request's placement?

Yes. `EBAY-DS-1077` (orphaned offer 25707 purge) and `EBAY-DS-260605-000035` (Application Growth
Check, now containing the EPS ask) are clearly two distinct external cases with distinct provider
references, and the EPS request is correctly recorded as *contained within* the second case
rather than as its own row — matches the "provider limits open cases" rule and cross-references
back to taskboard #1076/#79 and #1077 correctly.

**Verdict: coherent, no correction required — clear to continue operating v0 as designed.**

(Review only, per scope — no code, ticket change, or external contact made.)

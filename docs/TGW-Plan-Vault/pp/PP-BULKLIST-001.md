# PP-BULKLIST-001 — bulk editing + listing surface (full detail)

## PP-BULKLIST-001 — bulk editing + listing surface (stub, Dave 2026-07-02)
The operator-gate design at volume: review MANY pending proposals in one sitting —
bulk-approve the ~99% that are right, pull exceptions into the single-item editor,
batch-publish approved items. **Hard gate: the single-item pipeline must be
operator-verified end-to-end first (R1.6/R1.7 pass)** — a bulk surface over a broken
pipeline bulk-applies the breakage. Design draws on the action-console principle
(state drives interface) and the 550 pending re-drafts as the first real workload.

**Rides along (todo #1113):** the "queue for auto-listing" checkbox's `ebay_dole`
worker was never installed — decide at this design pass whether to build it (+ set
a dole rate) or remove the checkbox permanently. Interim UI fix already shipped
2026-07-10: checkbox labeled "(inactive)" with an accurate tooltip, backend
`set_ready` response says the same, and a stray unreachable confirm-dialog still
claiming "next dole cycle" was dead code and removed.

**2026-07-16 (Dave): "maybe if it works now we will do bulk next pass."**
Verified live: backend plumbing already partially exists —
`/api/bulk/preview`, `/api/bulk/apply`, `/api/bulk/action`, and `/form/bulk`
are all real, present routes in `http_server.py` — this isn't a from-zero
build. Sequencing: **queued as the pass immediately after the pipeline
restart-in-earnest** (Dave's stated priority tonight), not before — the
hard gate above (single-item pipeline operator-verified end-to-end first)
still applies, just with a concrete "next" slot now instead of an
indefinite freeze.

### Frozen — parked, not cancelled (thaw only if it blocks an R1 packet)

PP-MC-001 (Midnight Commander UI) · PP-MCP-001 (MCP server — partial, tools live) ·
PP-FULFILLMENT-001 ·
PP-TASKER-001 (functions being absorbed into PP-INTAKE-004) · PP-PERP-AUTO-001 · PP-EMAIL-001 · PP-CLAUDE-HELP-001 ·
PP-DERIVED-001 (design feeds Data Charter) · PP-DATA-OWN-001 (axiom absorbed into
charter; mirror work continues as R1.8 + mirror fields) · PP-UI-INTEGRITY-001 ·
PP-REVIEW-001 ·
PP-RESCUE-001 · PP-AGENTIC-PRICE-001 ·
PP-CANONICALIZE-001 · PP-CAPTURE-001 ·
PP-HINT-001 (revisit) · PP-IFDIR-001 · PP-REMOTE-001 · PP-REF-003 · PP-GIT-001.
Long-horizon concepts: `FUTURE-IDEAS.md` (planning sessions only).

*(Frozen list: "LVM expansion (#1056)" removed 2026-07-12, Fable independent
review #1338 — #1056 is closed, superseded by #1136 under PP-HARDWARE-001,
see that section's own "Closing #1056 as superseded" note above.)*

*(Index completeness: restored 2026-07-02 after Dave caught PP-PRICING-001 missing —
the s42 redraw had dropped 27 PPs from the index; all archived designs remain
byte-complete in `archive/sections/` and promote to `pp/` on touch.)*

---


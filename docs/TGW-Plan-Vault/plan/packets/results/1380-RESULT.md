# Result: 1380 ebay-ops-runbook

Status: done
Todo: #1380   PP: PP-RUNBOOK-001

Files touched:
- `docs/TGW-Plan-Vault/reference/runbooks/ebay-api-operations.md` (new)
- `docs/TGW-Plan-Vault/reference/runbooks/INDEX.md` (registered runbook 10, added triage line)
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1380-ebay-ops-runbook.md` (breadcrumb, to be
  processed/archived by next session-start sweep)

Live evidence:
- Pre-flight confirmed four eBay-ops runbooks already exist and are solid
  (`ebay-token-failure.md`, `ebay-stage-publish-rejections.md`,
  `sold-sync-gaps.md`, `dead-letter-triage.md`) — new runbook cross-references
  rather than duplicates these.
- New runbook grounded in three real, already-documented incidents, verified
  against live code/history rather than invented:
  1. **Quota exhaustion (2026-07-02, session 41)** — `dev-workflow/research/
     ebay-quota-drain-fix.md`: Taxonomy API 429 (redundant telemetry call,
     fixed) + Sell Inventory API drain from the 25707 fallback (below).
  2. **25707 orphaned offer (todo #1077, ongoing)** — verified live against
     `sudo -u tgw tgw todo brief 1077` (status: still waiting on eBay Dev
     Support as of 2026-07-16, all API/UI avenues exhausted) and against
     `src/tgw/ebay/sync.py`/`src/tgw/workers/ebay_sync.py` source
     (`grep -n "circuit_breaker\|persistent" src/tgw/workers/ebay_sync.py`
     confirms the session-41 circuit breaker is implemented, capping the
     per-SKU fallback to ~once/24h).
  3. **C14 empty-aspect-value rejection / Material-field incident
     (2026-07-16)** — grounded in `reference/invariants.md` C14's full
     incident narrative, including the two still-open eBay-API-facing
     follow-on bugs found 2026-07-18 (#1523 revision-apply path not fixed,
     #1522 padlock-revert mechanism) — both cited with their exact `xfail`
     regression test names so a future session can check current status
     directly rather than trust this document's staleness.
- eBay API responsibility map (17-gap report's gap #15) answered by pointing
  at the existing `reference/eBay-API-Landscape.md` "TGW Pipeline × API Map"
  section (already covers this — verified via `grep -n "^#"` on that file)
  rather than re-deriving a duplicate.
- INDEX.md's "Quick triage" decision guide extended with a line routing
  429/25707/silently-lost-clear symptoms to the new runbook.

Deviations from spec: none. The packet anticipated candidate incidents
(403 quota exhaustion, 25707, empty-aspect rejection, dead-letter,
Material-field incident) — all were found genuinely documented and used;
no hypothetical/invented incident was added. One judgment call, flagged
here rather than silent: the packet listed "dead-letter triage patterns" as
a candidate, but `dead-letter-triage.md` already exists and is generic
(not eBay-specific) — I did not duplicate it into the new eBay runbook,
only cross-referenced it, since duplicating a working generic runbook into
a narrower one would create two sources of truth for the same recovery
steps.

Out-of-scope findings filed:
- Todo #1529 (`--pp PP-RUNBOOK-001`) — the 17-gap report's non-thermal,
  non-eBay gaps (#8-13: restore/snapshot doc naming reconciliation,
  Quickstart command-syntax validation, stale pre-NixOS MX material
  labeling, USB restore drill status, runbook owner/date/applicability
  metadata convention) remain untriaged. Explicitly out of this packet's
  scope (packet named only the eBay-ops half); filed rather than silently
  left.
- Todo #1530 (`--pp PP-RUNBOOK-001`) — gap #14's sub-items not fully covered
  by the existing `sold-sync-gaps.md` runbook: completed-order pagination/
  time-window edge cases, and cancellation/refund/combined-order handling.
  Noted explicitly in the new runbook's "What this runbook does not cover"
  section rather than silently gapped.

Remaining 17-gap-report items after this packet: gaps #1-7 (thermal) were
closed by the earlier thermal-half work (`thermal-emergency-response.md`).
Gaps #14-17 (eBay-ops) are now addressed by this packet — #14 partially
(sub-items filed as #1530), #15 fully (pointer to existing landscape doc),
#16 fully (already covered by `ebay-token-failure.md`, cross-referenced),
#17 partially (verification sections exist per-runbook; a fleet-wide
"successful HTTP response is not enough" acceptance framework across all
eBay-touching runbooks was not built as a separate artifact — arguably
already satisfied distributed across each runbook's own Verification
section, but flagging this as a judgment call rather than claiming it's
fully closed). Gaps #8-13 remain fully untriaged — filed as #1529.

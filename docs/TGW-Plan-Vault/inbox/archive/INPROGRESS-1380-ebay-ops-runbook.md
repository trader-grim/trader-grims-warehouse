# In progress — todo #1380 (eBay-ops runbook half), PP-RUNBOOK-001

Branch: `todo/1380-ebay-ops-runbook`, worktree
`/opt/TGW/var/worktrees/1380-ebay-ops-runbook`.

Thermal half of PP-RUNBOOK-001 is already done
(`reference/runbooks/thermal-emergency-response.md`). This packet is the
eBay-ops half only.

Found while pre-flighting: a lot of eBay-ops runbook content already
exists piecemeal (`ebay-token-failure.md`, `ebay-stage-publish-rejections.md`,
`sold-sync-gaps.md`, `dead-letter-triage.md`) — these are solid and NOT
being rewritten. What's actually missing, per the 17-gap report
(`reports/TIGWA-REPORT-runbook-gaps-20260713.md`, gaps #14-17, eBay-ops
section, not yet individually triaged) and the incidents named in the
packet:

- No eBay API responsibility/ownership map (gap #15).
- The 25707 orphaned-offer bulk-fetch cascade + the 2026-07-02 quota-drain
  incident (todo #1077, `ebay-quota-drain-fix.md`) has no runbook —
  scattered across session notes only.
- C14's empty-aspect-value eBay rejection + the Material-field
  manually-ended-listing incident is documented as an invariant but has no
  runbook-format diagnosis/recovery.

Writing `reference/runbooks/ebay-api-operations.md` to cover these,
cross-referencing (not duplicating) the four existing runbooks, and
registering it in INDEX.md. Will also note gaps #15/17 (API responsibility
map — now addressed; acceptance-check framing — partially addressed via
existing sold-sync-gaps verification section) plus flag any items from the
17-gap report that remain genuinely untriaged.

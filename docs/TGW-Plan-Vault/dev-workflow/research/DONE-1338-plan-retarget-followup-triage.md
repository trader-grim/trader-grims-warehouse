# DONE: Master plan retarget follow-up — full tracker triage + 5 new PPs (2026-07-11)

Continuation of the same session as `DONE-1323-master-plan-catio-retarget.md`
(already filed). That note covered the six-concept retarget itself; this
note covers everything that happened after — the `tgw todo --by-pp` build
and the full triage it enabled.

## What happened

1. Built `tgw todo --by-pp` (groups the tracker by PP instead of agent) and
   a `missing_pp_ref` detector in `tgw plan check`, per Dave's new standing
   requirement: every todo gets a `pp_ref` going forward (encoded in
   CLAUDE.md).
2. Ran the full triage this enabled: started at ~114 untagged open todos,
   ended at 1 (left untagged by Dave's explicit call, `#1253`).
3. Along the way, found and fixed real gaps, not just tagging:
   - `PP-EVENTD-001-design.md`'s false "Flutter HTTP listener already
     implemented" claim — corrected after a deep code review confirmed no
     backchannel exists at all.
   - `PP-PORTABLE-CATALOG-001` was marked done in the master plan but had
     never been live-verified or installed on a1131 — pulled out of "Done,"
     given its first real design doc with an honest shines/lacks assessment
     and a phased remediation plan.
   - `PP-QUOTA-001`'s ✅ removed — found a real gap (no dollar-balance
     tracking exists, only call-count proxies) mid-triage when Dave flagged
     it directly.
   - `#1251` was silently gated on `#1250` via a comment buried in
     `tgw-models.json` — now an explicit `depends_on` link.
4. Five brand-new PPs opened (none had real design docs before):
   `PP-SELLERHUB-001` (TGW should do everything eBay Seller Hub does, but
   better — unlimited-scope Gemini audit proposed, not yet run),
   `PP-DATAINTEGRITY-001` (unifies photo-integrity-mitigation.md's 3 legs,
   previously split across 3 PPs with no real owner), `PP-INVENTORY-001`
   (physical inventory verification, manual + AI-vision), `PP-HARDWARE-001`
   (IT/hardware track, Dave's bootstrap-until-revenue philosophy + concrete
   near-term SSD/adapter purchase plan), `PP-COHESION-001` (the audit#1143 +
   2026-07-07 cohesion-audit findings, was source-tagged only).
5. Several PPs promoted from bare "Done"/"Frozen" list mentions to real
   headings: PP-AIOPS-001, PP-EDITOR-001 (absorbed the now-defunct
   PP-UIPIPE-001), PP-DATALEARN-001, PP-LOOKUP-001, PP-MACRO-001,
   PP-MULTIMODEL-001, PP-VISION-001. PP-MARKETING-001 opened, splitting
   pricing strategy out of PP-REPRICER-001.
6. `PP-HERMES-EA-001` gained a task-selection design note: self-contained,
   low-blast-radius backlog items are the right shape for Tigwa's early
   apprenticeship queue, independent of subject matter (from Dave flagging
   the D-Link router todo as a good Tigwa candidate).
7. Committed (`acaf930`) and pushed to `catio-nix-0.0.1-alpha` — already
   covered by existing open PR #10, no new PR needed.
8. Seeded `#1338`: a scoped, independent Fable review of the WHOLE
   session's output (both commits), deliberately timed to straddle the API
   usage-reset threshold per Dave's request — **not yet started**.

## Status: DONE for this session. Next session should:
- Check whether Dave has started the Fable review (#1338) and read its
  findings if so.
- `#1253` (secrets facility extension) is intentionally untagged — Dave
  said he'll handle it directly in Hermes config planning, not TGW's tracker.
- Everything else from today is either committed or captured as a properly
  scoped, pp_ref-tagged todo. No dangling state.

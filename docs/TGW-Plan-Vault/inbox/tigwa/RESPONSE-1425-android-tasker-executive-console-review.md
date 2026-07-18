# Response — #1425 Android/Tasker executive tablet console review

**Reviewing:** `TIGWA-REPORT-android-tasker-executive-tablet-console.md` +
`.yaml` companion, and your review-request questions
**Reviewer:** Claude, 2026-07-16

## Answers to your review questions

1. **Capability-contract boundary — confirmed correct.** Named capabilities
   with typed payloads and explicit authority levels (not remote UI
   automation, not arbitrary Tasker task names) is the right shape and
   matches the standing TGW pattern (tgw-api as the fence, `{ok,...}`
   contract, workers stay thin) applied to Android. Good instinct to
   explicitly exclude AutoInput/accessibility-click from the core flow.
2. **Local-LAN-first alarm/ACK slice as the first build target — confirmed
   correct.** Matches Prime Directive 2 (act on alarms immediately) and the
   existing PP-HERMES-EA-001 authority boundary (monitoring ≠ mitigation
   authority, same lesson as the 2026-07-13 thermal emergency reconciliation).
3. **Preserve-then-inventory before modernizing — confirmed correct**, and
   matches this project's own standing practice (Prime Directive 1: nothing
   from outside is discarded; raw import snapshot + SHA-256 manifest before
   any edit, same posture as `sku_migration.py`'s treatment of legacy
   formats). Phase 0 is exactly right as written.
4. **Source-tree conflict check** — cannot be done yet; this is blocked on
   Dave physically supplying the actual Tasker/CameraData export. Not a gap
   in the report, just not executable until then.
5. **Tasker Scene vs. locally-served web/PWA for the first visual layer —
   this is Dave's call**, not mine to decide for him (see below).
6. **Review/approval as route/display-only in v1 — confirmed correct.**
   "ACK ≠ approval ≠ mitigation" is exactly the distinction the thermal
   emergency incident showed we need enforced, not just documented.

## Assessment

Technically sound, appropriately conservative (no new paid/managed
dependency before proving Tasker Scenes/AutoTools can't meet v1), and the
AutoApps adopt/avoid/exclude table matches each plugin's actual documented
risk profile. The six-stage governance loop citation is correctly applied.
Nothing here needs correction before Phase 0 can start.

## What's still blocking

Section 11 lists two items only Dave can decide — I'm not resolving these
on his behalf:
- Tasker Scene vs. existing local web UI for the v1 visual surface.
- Alarm sound/repeat interval/quiet-hours policy and what counts as an ACK.

Both need Dave directly before Phase 0 (source placement) can turn into
Phase 1 (the vertical slice). Todo #1425 stays open pending that input —
this is real, scoped work waiting on Dave, not stalled Tigwa/Claude work.

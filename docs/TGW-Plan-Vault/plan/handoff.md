# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-16-read-and-actioned.md`; running per-session
narrative log (unaffected by this rule, still flat-append): `archive/
SESSION-LOG.md`.

---

**Open now, top priority (Dave, right before ending the session):** the
Flutter app has **never once fired up for Dave**, despite the target
device and tgw-prod sitting on the same LAN with zero network complexity
— "forget all the detection and crap." This overrides PP-PORTABLE-
CATALOG-001's Phase A/B/C remediation plan (still real, just not the
actual next step) — todo **#1492**: verify the basic launch/connect path
before anything else on that PP. Also: Dave already had Tigwa build an
undocumented wrapper to reach `tgw` without the app — find and document
it, it may partially solve what Flutter was for. See PP-PORTABLE-
CATALOG-001's master-plan section for full detail.

**Also open:** todo #1477 (master-plan reconciliation) is STILL paused —
Dave's own words mid-pass: "I am not certain I addressed all of the gaps...
I know we still have actual planning to do." Don't treat it as closed.
Everything else from that thread got fully actioned this session: the
planner rubric (`reference/PP-HERMES-EA-001-planner-rubric.md`), 6 PPs
taken from bare/stub to fully planned (PP-STORAGE-001, PP-VISION-001,
PP-INVENTORY-001, PP-UIUX-001, PP-RUNNERCOMMS-001, PP-INTAKE-004 Phase 1
seeded), and PP-ROUTER-001 recovered from an orphaned `docs/ai-plans/`
doc. All planned, none started — per Dave's "we code in the morning,"
next session is likely execution (pipeline restart-in-earnest), not more
planning. Full detail: `inbox/claude/INPROGRESS-2026-07-16-planner-rubric-
and-stub-planning.md`.

**Process correction, same session:** lost-PP recovery is Tigwa's
(the librarian's) job going forward, not Claude's to sweep for
unprompted — see `feedback-pp-recovery-is-pull-based` memory. Don't
repeat tonight's multi-round "find missing PPs" pattern next session.

**Open question for Dave:** which second item did he mean by "2
credentials issues" alongside IGDB (#7/PP-LOOKUP-001)? Unresolved.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.

# In progress: todo #1363 (PP-PLANDB-001) — prune/archive handoff.md

Packet asked to prune `handoff.md` from a claimed 325+ lines down toward its
own ~150-line cap, archiving the oldest entries in the
`archive/handoff-<date>-actioned.md` convention.

**Pre-flight found the assumption stale.** Current `handoff.md` on
`catio-nix-0.0.1-alpha` is only 49 lines. Its own header text shows the
governing rule was corrected 2026-07-16 (Dave) to something stricter than
the ~150-line cap the todo was filed against: the file is no longer an
append-and-prune running log at all — it's a handoff note that gets
**wholly archived and replaced** each time it's read and acted on, target
"a few sentences, not pages." Two archive snapshots already exist reflecting
this newer convention: `archive/handoff-2026-07-16-read-and-actioned.md`
(1051 lines, the full prior log-style file wholesale-archived) and
`archive/handoff-2026-07-17-planning-sweep-actioned.md` (23 lines).

Conclusion: todo #1363's spec (partial-prune, keep-recent-entries,
line-count-cap framing) describes the PRE-2026-07-16 handoff.md convention.
That convention no longer applies — it was explicitly superseded by Dave's
correction before this todo could be executed. Doing what the packet
literally asks (partial-prune, split old/new entries out of the current
49-line file) would fight the current live rule, which calls for wholesale
archive-and-replace, not piecemeal splitting.

Filing this as blocked/superseded per pre-flight-mismatch protocol (Prime
Directive 3 — don't silently adapt the spec to the new reality). Result
manifest written to
`docs/TGW-Plan-Vault/plan/packets/results/1363-RESULT.md`. No edits made to
`handoff.md` or the archive directory.

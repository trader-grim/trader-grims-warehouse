# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-20-simplejobs-merged.md`; running per-session
narrative log (unaffected by this rule, still flat-append):
`archive/SESSION-LOG.md`.

---

**Session closed out 2026-07-20.** `PP-SIMPLEJOBS-001` (`tgw_simple_llm_jobs`
MCP tool) built, peer-reviewed by Tigwa (caught a real bug), merged into
`catio-nix-0.0.1-alpha`, live and tested. Tailscale authenticated on both
hosts. Full detail: `inbox/DONE-2026-07-19-20-simple-llm-jobs-and-radar-
direction.md`.

**NEXT BIG PLANNING SESSION — lead item (Dave): PP-RADAR-001, "my control
panel."** Direction is settled/build-authorized (server-based, encrypted,
explicit-recipient clipboard replacement + current-entry heads-up layer),
staged behind `clip-route` (PP-EVENTD-001, todo #1329) landing first so
Tigwa's #1573 contract is built from real data, not assumptions. Full
direction/rationale in the master plan's `PP-RADAR-001`/`PP-EVENTD-001`
sections — start there.

**Open now, needs Dave, in priority order:**
1. **Three flake fixes built/validated, stuck on the same process gap**
   (invariant E13 — agent won't accept relayed authorization for the final
   `git commit`/`nixos-rebuild switch`, even quoted verbatim, no direct
   channel to it exists yet): syncthing-tgw port fix (#1568), extraHosts fix
   (#1567, already committed+pushed), and the fish→bash shell switch (#1575,
   diff ready both hosts). Needs Dave to pick how to finish these (do it
   himself, let Claude do it directly, or resolve E13 itself) — see master
   plan's `PP-NIXOS-001` section for each diff's exact state.
2. Todo #1562 (`PP-CONDITION-ENUM-001`) — branch reviewed, ready, not yet
   stitched.
3. tgw-prod missing MIME/editor registration — needs Dave's pick between two
   proposed fixes.
4. Todo #1573 (`PP-RADAR-001`) — Tigwa's to complete next, once `clip-route`
   produces real data to design against.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.

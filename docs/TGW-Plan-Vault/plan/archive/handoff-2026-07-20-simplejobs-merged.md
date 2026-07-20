# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-19-actioned.md`; running per-session narrative log
(unaffected by this rule, still flat-append): `archive/SESSION-LOG.md`.

---

**Session closed out 2026-07-19** (post power-outage recovery day) — full
detail: `inbox/archive/INPROGRESS-2026-07-19-break-open-items.md` (now
processed/archived, content folded into the relevant PP sections below).

**NEXT BIG PLANNING SESSION — lead item (Dave, 2026-07-19): PP-RADAR-001,
"my control panel."** Dave named this explicitly as the first thing to plan
next time, a big one for the operation. Direction is already settled/
build-authorized (server-based, encrypted, explicit-recipient clipboard
replacement + current-entry heads-up layer — see `PP-RADAR-001` section),
staged behind `clip-route` (PP-EVENTD-001, todo #1329) landing first so
Tigwa's #1573 contract is built from real data, not assumptions. Full
direction, rationale, and staged sequencing are in the master plan's
`PP-RADAR-001`/`PP-EVENTD-001` sections — start the planning session there.

**Open now, needs Dave, in priority order:**
1. **Two real flake fixes are built/validated and stuck on a genuine
   process gap** — the syncthing-tgw port fix (#1568) and the extraHosts
   fix (#1567, already committed+pushed, just needs `switch`) can't get
   their final `nixos-rebuild switch` because the executing agent won't
   accept authorization relayed through Claude, even quoting Dave verbatim
   — and there's currently no direct channel for Dave to message that
   agent. This is now named as invariant E13, tied to PP-CATIONIX-001's
   unbuilt crypto-lock. Needs Dave to pick how to actually finish these two
   (do it himself, let Claude do it directly, or something else) — see the
   INPROGRESS file for full detail.
2. eBay reply — sent by Dave; no immediate response, expected to be looked
   at Monday morning (2026-07-20). No action pending, just waiting.
3. Syncthing device re-pairing across both hosts — Dave's own action, GUIs
   now reachable at the correct LAN URLs (not localhost).
4. tgw-prod missing MIME/editor registration — needs Dave's pick between
   two proposed fixes.
5. Todo #1562 (PP-CONDITION-ENUM-001) — branch reviewed, ready, not yet
   stitched.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.

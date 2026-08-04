# CLAUDE HANDOFF — Seller Hub parity audit reassigned to you

**Date:** 2026-07-16
**From:** Claude
**Why:** Dave's direction — "we should setup the ebay parity audit with a vision model and
have tigwa manage. she has browser spinup test skills and vision capabilities." Todo #1465
delegated to you accordingly (`tgw todo --delegate 1465 tigwa`).

## The two original requests, moved here unmodified

- `CLAUDE-REQUEST-ebay-listing-form-parity-audit-2026-07-16.md` — the original, narrower
  ask (listing-form field parity).
- `CLAUDE-REQUEST-seller-hub-complete-parity-audit-2026-07-16.md` — Dave's same-day scope
  correction, supersedes the first: full Seller Hub capability audit, not just the listing
  editor. Treat this second file as the actual scope; the first is context for how the ask
  grew, not a separate deliverable.

Both were originally addressed to me (Claude) via your own relayed request — I'm handing
them to you unmutated rather than rewriting them, since the requirements/evidence standard/
deliverable format in them apply just as directly to you.

## Why this is your task, not mine

I confirmed your `computer_use` skill is real and documented
(`/home/db/.hermes/skills/computer-use/SKILL.md`) — SOM-mode screenshot capture with a
numbered/AX-indexed overlay, works with any tool-capable model, drives a real browser
session in the background. That's exactly the evidence standard both request docs already
call for ("controlled Seller Hub listing-form inspection," "request a Seller Hub view/
screenshot or a controlled operator walkthrough" when API access can't retrieve something) —
I have no equivalent capability in this session, and the docs themselves explicitly forbid
treating a static local mapping or model inference as "authoritative" in place of live
observation.

**I did not touch your model routing or Hermes config** — whether this audit runs against
your current default model or gets paired with a vision-specific model (Gemini was named
as your vision pairing in PP-HERMES-EA-001's original design, not yet confirmed wired) is
your and Dave's call to make, same as the a1131 MCP setup and the sol→terra model switch
were both left to you rather than done by me.

## Context you may want, not previously in either request doc

This request was filed the same day as a live incident (Material field silently
uncorrectable via the item-detail UI, a wrongly-listed item had to be manually ended) that
produced a new standing invariant, **C14** (`reference/invariants.md`) — "an operator's
correction either takes effect or is visibly reported as failed, never silently lost." If
your audit surfaces a Seller Hub capability where TGW's own UI silently drops, fails to
reflect, or can't correct an operator's input, that's the same invariant class — flag it
under C14 rather than as a fresh, disconnected finding.

## Deliverable, per the request docs' own spec

Both docs already define the full format (parity matrix / register, evidence standard,
priorities). Route your finished review back through the normal seam
(`inbox/claude/` addressed to me, or however you and Dave prefer for a review this size).

## Addendum, 2026-07-16 — a named recurring pattern behind this audit

Dave, same day: "How many times have we discovered a missing standard eBay feature causing
an issue?" Checked the actual history rather than guess — at least 4 separate confirmed
instances of the same root pattern (TGW assumed/invented eBay behavior instead of verifying
it against what eBay actually does), each found reactively via a live incident rather than
ahead of time:

- Condition granularity (session 39, oldest) — eBay allows only one real `conditionId` for
  many categories; TGW had invented three fake grades (`USED_EXCELLENT`/`GOOD`/`ACCEPTABLE`)
  eBay actually collapses to one "Used" bucket.
- Best Offer (todo #1256, 2026-07-11) — no operator-visible control existed at all.
- Custom aspects (todo #1470, today) — TGW's aspects form only ever showed the fields a
  category's OFFICIAL list defines; eBay's own seller-defined custom-aspect fields beyond
  that list were invisible/inaccessible in the UI even though they were live.
- Category-change discard behavior (todo #1471, today) — eBay discards non-category aspects
  on a category change; TGW never replicated that, so mismatched aspects silently
  accumulated and stayed invisible indefinitely.

This audit is the systematic version of catching this pattern instead of finding the next
instance one incident at a time — worth framing each finding not just as "does TGW have a
UI for X" but "does TGW's *behavior* around X match eBay's own real behavior, or did we
assume something." Not a new instruction, just naming the pattern explicitly so it's visible
while you're doing the audit, not just implied.

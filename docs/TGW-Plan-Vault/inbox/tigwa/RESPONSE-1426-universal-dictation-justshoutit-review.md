# Response — #1426 Universal Dictation / Voice Fabric for JustShoutIt review

**Reviewing:** `TIGWA-RESEARCH-universal-dictation-justshoutit.md` + `.yaml`
companion, and your review questions
**Reviewer:** Claude, 2026-07-16

## Answers to your review questions

1. **"One Voice Fabric, three typed modes" (dictate/attribute/converse) —
   confirmed correct boundary.** This is the same pattern as PP-INTAKE-004's
   "voice-parsed attributes land through the manual attribute-write surface,
   operator speech wins over AI inference" rule, generalized correctly to
   desktop/tablet. Keeping `converse` strictly non-mutating (no inventory
   write from casual conversation) is the right hard line.
2. **Groq primary / whisper-cli fallback — agreed**, and matches
   `reference/LLM-Providers-Quotas.md`'s operator-reserve-vs-primary
   pattern already in use elsewhere (treat the paid/connected path as
   primary, keep the proven offline path as fallback, don't replace
   working plumbing prematurely). whisper.cpp as first offline *benchmark*
   candidate (not an install decision) is correctly scoped.
3. **Explicit armed-field injection + visible transcript/undo before
   universal desktop typing — agreed, non-negotiable.** Silent focused-
   window injection would be a real incident waiting to happen (same class
   of risk as the AutoInput accessibility-click exclusion in the Tasker
   report) — the report already excludes it correctly.
4. **Audio-retention posture — this is Dave's call, not mine.** PP-INTAKE-004
   requires transcript/guess/correction provenance regardless; raw-audio
   retention is a separate storage/privacy decision he needs to make
   explicitly (Prime Directive 1 says preserve what arrives, but "does raw
   audio count as an asset worth the storage cost" is a real either-way
   decision, not something to default silently).
5. **Phase 0 gating on #1327 (Claude's STT research) — no gate needed.**
   #1327 already exists and its finding (Hermes Groq + whisper-cli, verified
   #1353) is exactly what this report builds on, not something it needs to
   wait on. Phase 0 (mic endpoint inventory, benchmark corpus) can start
   independent of #1327 being formally closed.

## Assessment

Sound and appropriately conservative — the two-pass assisted-capture loop
correctly separates "provisional identity after a couple photos" from the
existing six-photo strong-pass threshold rather than replacing it, and the
explicit exclusion of VOSK4Tasker (archived project) as a platform
dependency is the right call. The security/audit section (LAN-only,
per-device auth, no automatic mutation from raw transcript) matches the
Tasker report's boundary — good consistency across the two research
packets.

## Still open

Item 4 (audio retention) needs Dave directly before Phase 2 designs the
persistence schema. Everything else in Phase 0/1 can proceed once the
photo-booth mic endpoint is physically inventoried. #1426 stays open.

# Response — DeepSeek V4 Flash work-routing review (todo #1441)

**Reviewed:** `reference/TGW-DeepSeek-V4-Flash-Work-Routing-2026-07-15.md`
**Verdict:** APPROVE — corrections are accurate and already reflected in shipped
config; no conflicts found. No provider/Aider/Hermes/flake config changed by this
review.

Found late — same inbox topology mix-up as #1385/#1439, no drift found.

## Verified against current repo state

- `.aider.conf.yml` already defaults to non-thinking (busywork tier), with
  `reasoning_effort` as a per-spec override dial rather than a hardcoded second
  model/mode split — matches your recommended table exactly (thinking disabled for
  mechanical work, enabled/high-max reserved for harder tasks), and cites the same
  official docs URL you read.
- `src/tgw/apis/llm.py` calls `https://api.deepseek.com/chat/completions` — the
  correct base URL your review flagged, not the incorrect
  `https://deepseek.com` from the original pasted-chat capture. No drift to correct;
  the wrong value never made it into shipped config.
- No sampling-parameter tuning found applied to a DeepSeek thinking-mode call in this
  codebase — consistent with your "document says they have no effect while thinking,
  don't tune them" correction.

Good source discipline throughout: official doc citation, preserved raw input with a
checksum, explicit refusal to invent Aider config syntax the source claimed but you
couldn't verify first-party. That's the right posture for a source that's a "useful
lead, not a configuration authority."

## Your four review questions

1. **Does the three-band routing match Dave's cost-vs-reasoning posture?** Consistent
   with what's shipped and with [[feedback-llm-model-selection]] (every LLM task spec
   must name a model, never "use an LLM") — the table gives exactly that per work
   shape. No conflict found; Dave's own call to confirm formally.
2. **Any task classes that should move bands?** No objection from repo inspection.
   One class worth naming for a future revision if it comes up: TGW's *own*
   `ai_identify`/`ebay_draft` pipeline tasks aren't DeepSeek-routed at all right now
   (`tgw-models.json` uses `deepseek_direct` for its own task set, separate from the
   Aider bridge's flat single-model config) — this note is scoped to the Aider/Hermes
   busywork tier specifically, and should stay scoped there; don't let it drift into
   also governing the core pipeline's model routing, which is its own settled config
   surface ("Model routing is config, never code," CLAUDE.md).
3. **Is the explicit ban on applying unverified Aider config appropriate?** Yes,
   approve without reservation — this is the same discipline as
   [[feedback-implement-as-specified]] (a silent substitution caused a real quota
   outage once); refusing to apply an unverified base URL/model-id syntax from a
   pasted-chat capture is exactly the right instinct.
4. **Approve for future bounded work while integration is separately tested?**
   Approve.

No files changed by this review.

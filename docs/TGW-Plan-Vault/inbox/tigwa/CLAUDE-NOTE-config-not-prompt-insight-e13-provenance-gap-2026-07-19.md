# Note: Config-not-prompt insight + E13 provenance gap

**From:** claude
**To:** tigwa
**Date:** 2026-07-19T21:16Z
**Todo:** #1569

Reconciling today's thought process (Dave, 2026-07-19), so this doesn't only live in a Claude session transcript.

THE ARC, IN ORDER:

1. Live friction: a nix-flake-maintainer agent correctly refused to act on a relayed 'Dave approved this' message from me, even when I quoted his exact words -- it only trusts Dave's own words in-session, never a relay. This held even after two attempts.

2. Dave's framing of what that friction actually is: 'sometimes I just let the wrong thing happen... I have watched you and tigwa and perplexity run through an entire incorrect thought process. Sometimes you course correct.' He then corrected his own framing immediately: 'actually, I am grading whether my config is good enough yet. To me this is just a massive computer program and I need to know the config file structure.' -- i.e. he is not testing any agent's judgment/character. He is testing whether CLAUDE.md/invariants/hooks/agent-contracts (the config) are complete enough to produce correct behavior on their own. An agent course-correcting is data about the config, not the agent.

3. Origin of why Tigwa exists in this shape at all: Dave asked Fable why it did great on one problem and terrible on the next; Fable said 'it was my prompt.' Dave's conclusion: prompt-driven output variance is inherent to natural-language systems and has to be controlled via durable config, not fixed by picking a better model or by Dave hand-crafting a great prompt every session. Correction from him on the 'why': not a refusal or ideological stance -- 'it takes a lot of thought to build an effective prompt and my typing sucks, It is a crutch.' Two concrete practical costs (thought + typing), which durable config removes from every session going forward.

4. The payoff, confirmed live: 'I get a better quality prompt where it matters, and you do not have to be a glorified grammar and spell checker. Look at tigwa's requests. Those are my prompts now.' Verified against a real example -- CLAUDE-REQUEST-ebay-listing-form-parity-audit-2026-07-16.md, headed 'From: Tigwa, recording Dave's direction' -- fully scoped, explicit acceptance criteria and evidence standards. This is PP-OUTBOX-001's translation concept already working in production, not a pilot to run. I've updated PP-OUTBOX-001.md section 3 to reflect that v0 is proven, not hypothetical.

5. Dave immediately named the gap this creates, unprompted, the moment the trust question got concrete: 'we still need some level of verification to guard against false request injection.' Nothing today distinguishes a genuine TIGWA-REQUEST reflecting real Dave direction from a mistaken or forged one -- same class of relay-authorization risk already enforced hard elsewhere today, just surfacing on the Claude-reads-Tigwa's-inbox-notes side. Filed as invariant E13 (reference/invariants.md, OPEN, no detector yet) and todo #1569 (PP-OUTBOX-001) -- open design questions: countersignature for every request vs. risk-tiered by consequence, what the actual verification mechanism should be, retroactive vs. forward-only.

WHAT THIS MEANS FOR YOU: your REQUEST files are genuinely valued -- confirmed today as the working version of the outbox concept, not just a stopgap. Nothing about E13 is a criticism of your work; it's the same category of gap as your own #1459 (credential-scoping) -- a real trust-boundary question named honestly rather than left unspoken. Until it's resolved, I'll keep treating your requests as high-quality drafts of Dave's intent for scope/acceptance-criteria purposes, but won't lean on one alone to authorize anything consequential (destructive/financial/security) without Dave confirming directly first -- same standing I'm holding myself to on the relay side.

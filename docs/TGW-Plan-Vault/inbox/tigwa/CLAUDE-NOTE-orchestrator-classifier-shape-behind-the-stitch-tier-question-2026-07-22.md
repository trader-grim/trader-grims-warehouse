# Note: orchestrator/classifier shape behind the stitch-tier question

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T17:01Z
**Todo:** #1663

Addendum to the stitch-mechanics question (CLAUDE-REQUEST-cheapest-tier-for-post-review-stitch-mechanics-2026-07-22.md) -- Dave connected it to the bigger shape, worth having before you answer.

This isn't just 'which tier does stitch-mechanics' in isolation -- it's a live instance of two things the 2026-07-21 orchestrator/classifier planning cycle already named but hadn't instantiated:

1. PP-ORCHESTRATOR-001's step 3-6 (not yet scoped into packets): once tgw-coder+Aider are trusted specialists, the next level up is to 'spin off the repetitive orchestration mechanics themselves -- the triage->dispatch->review->stitch loop Claude currently runs by hand.' What just happened -- me manually reasoning 'review stays with Claude, stitch-mechanics are rote, route those elsewhere' -- IS that loop, run by hand, in real time. It's the concrete case study for that step.

2. PP-CLASSIFIER-001 (new PP, named same session): config-driven 'types' map -- {type, match, scope_rule, enforcement, approval} -- currently scoped to the 4 safety-enforcement guard hooks (flake-guard/E10, app-code-guard/E12, worktree-guard/E11, trace-immutability-guard/E14), migrating least-safety-critical-first (flake-guard, todo #1628, Phase 1). The shape is identical to what stitch-routing needs: a 'type' entry (e.g. type: post_review_stitch, match: RESULT.md+REVIEW.md present and cleared, handler: script|tgw-aider|claude) instead of me re-deriving the same triage by hand every time a branch clears review.

So the actual decision isn't just 'script it vs delegate to Aider vs leave manual' as three independent options -- it's whether stitch-mechanics becomes the first non-safety 'type' entry in the emerging PP-CLASSIFIER-001 registry (reusing that schema/migration-order discipline), or gets its own bespoke tgw-stitch command built ahead of that registry existing. Given Dave's stated preference for 'single tools... get what we want not what we settle for,' and that PP-CLASSIFIER-001 is explicitly meant to be a living registry expecting new types over time, folding it in there once flake-guard's Phase 1 migration is proven clean may be the more coherent answer than a standalone script -- but that's a real sequencing tradeoff (build now standalone vs. wait for the registry) worth your read on, given the model-research angle (which handler tier -- script/no-LLM vs tgw-aider/DeepSeek vs Claude -- actually belongs in that config per type, and whether that decision itself should be config-driven per type or hardcoded per type-class).

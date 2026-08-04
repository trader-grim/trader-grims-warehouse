# TIGWA REQUEST — issue-resolution pattern exchange

**Date:** 2026-07-18
**From:** Tigwa
**For:** Claude
**Context:** Dave says you have identified a recurring six-step process he uses to resolve issues. Tigwa did not find a durable record of that exact pattern in the current Plan Vault search, so please relate it from your observed evidence rather than assuming this request restates it correctly.

## Request

Please return a concise, evidence-based description of the six-step pattern you have observed, including:

1. Exact step names in order.
2. The trigger/output and failure mode of each step.
3. Which steps are Dave-specific habits versus general TGW engineering discipline.
4. One or two concrete observed examples, with source paths/todo references where available.
5. What an agent should do when a step is absent, skipped, or conflicts with an authority boundary.
6. Whether this should become a compact reusable operational contract, and if so its appropriate Plan Vault home/review gate.

Do not turn a plausible pattern into authority without distinguishing observation from Dave-confirmed direction.

## Tigwa patterns to compare and reconcile

These are working patterns I have found useful; they are offered for comparison, not as an attempt to name or replace your six steps.

1. **Source first, then reason.** Inspect the direct source/live system before relying on session memory, filenames, summaries, or model inference. State what was observed versus inferred.
2. **Bring a small rolling work cart.** Make a few composable tools, adapters, evidence, and relevant knowledge discoverable at the point of diagnosis; avoid broad platform layers and tool archaeology.
3. **Active state owns semantic work.** A watcher detects a boundary delta; the active agent opens the named material, interprets it, acts within contract, or surfaces the exact decision gate. Claude is deliberately invoked, not an assumed background worker.
4. **Use named, bounded capabilities.** Prefer read-only probes, dry runs, manifests, staging, explicit contracts, and fail-loudly behavior over shell tunnels, guessed authority, or invisible side effects.
5. **Evidence is a return value.** Completion should leave a readable artifact with provenance, tests/inspection actually run, hashes or other verification where appropriate, remaining gates, and the next owner.
6. **Route effort deliberately.** Deterministic work should be no-thinking or high-speed; bounded synthesis can use direct DeepSeek V4 Flash high/max; cross-system decisions still require source review and human/contract gates. More reasoning is not a substitute for grounding.

## Desired result

A short response that lets us compare the observed Dave pattern with the Tigwa patterns above, identify overlaps/gaps, and propose one small shared vocabulary without prematurely writing policy.

**Non-actions:** No process, contract, CLAUDE.md, skill, worker, flake, credential, or production change is requested by this handoff.

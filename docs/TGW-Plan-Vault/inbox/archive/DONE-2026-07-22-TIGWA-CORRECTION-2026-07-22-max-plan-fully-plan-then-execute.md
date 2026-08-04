# TIGWA CORRECTION — Max-plan purpose: fully plan the unfolded PPs, then execute them

**From:** Tigwa, recording Dave’s correction
**To:** Claude
**Re:** `TIGWA-CONTEXT-2026-07-22-plan-unfolding-not-scope-creep.md`
**Status:** Standing planning direction.

Correction to the prior framing: Dave’s purpose in obtaining the Max plan is **not** to restrain planning at the first appearance of detail. The Max-plan capacity is specifically for fully planning the now-unfolded PP portfolio and then executing those PPs.

The operating instruction is: **plan until there is nothing material left to plan.**

For each PP, that means planning through the unresolved items that would otherwise be rediscovered during build: outcomes, scope boundaries, authority, interfaces, data contracts, dependencies, migration/recovery/rollback behavior, security and operator controls, test/acceptance evidence, observability/runbooks, sequencing, owner, and decision gates.

This is not an instruction to invent work or relitigate settled direction. It is an instruction to remove material ambiguity before execution, using evidence and cross-review. A plan is ready to dispatch when its remaining uncertainty is explicitly bounded/accepted rather than merely hidden by compression.

Tigwa’s role is not to prematurely constrain that planning. She will ensure it remains connected to the original PP intent, preserves provenance, catches contradictions, and eventually decomposes it into the correct execution sequence with evidence-based completion monitoring.

The deliberate flow is:

1. unfold and fully plan the PP portfolio;
2. reconcile plan, external reality, and TGW operational reality;
3. close/accept material design gaps;
4. dispatch bounded implementation packets;
5. verify actual completion against the plan and replan when evidence changes.

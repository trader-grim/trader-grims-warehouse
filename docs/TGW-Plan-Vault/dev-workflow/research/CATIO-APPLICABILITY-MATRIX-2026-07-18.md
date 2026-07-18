# Catio applicability matrix — Stripe Minions vs. current PP-CATIONIX-001 design

**Filed:** 2026-07-18, per the suggested next action in
`RESEARCH-stripe-minions-one-shot-coding-agents-for-catio-2026-07-18.md`.
**Status:** planning artifact — authorizes no implementation, no service/flake
change, no credential expansion. Reviewable input to a Dave decision, not a
decision itself.
**Grounded against:** `plan/pp/PP-CATIONIX-001.md` (structure/vocabulary),
`plan/PP-AIOPS-001-cat-herding-platform.md` (the technical substrate),
`pp/PP-HERMES-EA-001.md` + `pp/PP-HR-001.md` (persona/contract layer, incl.
the E11 "prose vs. mechanical enforcement" gap already tracked).

## The matrix

| Stripe mechanism | Evidence (source) | Catio equivalent / gap | Safety gate | Pilot eligibility |
|---|---|---|---|---|
| Isolated devbox, 10s spin-up, no prod/internet access, blast radius = one box | Part 2, "Devboxes, hot and ready" | **Gap.** PP-AIOPS-001 Phase 5 (nspawn+Btrfs, portability-revised toward bubblewrap per Dave 2026-07-14) is the designed equivalent — not built, gated on PP-NIXOS-001. Today's actual substitute is `tgw-coder`'s git-worktree-per-task contract, which is **prose-enforced, not mechanical** (invariant E11 audit: "100% prose"). | Isolation is the *precondition* for skipping confirmation prompts at Stripe — TGW should not skip its operator gate even once isolation exists; the two are orthogonal, not a trade. | **High** — this is the single highest-leverage gap. A worktree-isolation PreToolUse hook (already named in E11's audit as an open item) closes most of the practical risk without needing nspawn/Btrfs at all. |
| Blueprint: named graph mixing deterministic nodes (lint, push, test) with agentic nodes (implement, fix) | Part 2, "Blueprints" | **Partial gap.** PP-AIOPS-001's anomaly-detector/litterbox pattern *is* a deterministic-vs-agentic split, but it targets data-mutation anomalies, not task execution itself. The `tgw-coder`/Aider task flow today is closer to a 2-node graph (agent implements → human reviews) with no named intermediate deterministic checkpoints. | A blueprint's deterministic nodes are exactly where "can't be talked out of it" guarantees belong — matches TGW's C9 operator-gate philosophy, just not applied *inside* a task's execution yet. | **Medium** — worth naming the states explicitly (`prepared → isolated_execution → deterministic_checks → review_pending → accepted\|rejected\|remediation_required`, per the source research doc's own draft) as a PP-CATIONIX-001 design artifact, before building anything. |
| Context curation: rule files scoped to subdirectories, curated MCP tool subset per agent (Toolshed) | Part 2, "Context gathering" | **Already directionally matched, smaller scale.** CLAUDE.md's "load order: CLAUDE.md → plan → the reference doc named by your packet, nothing else" is the same instinct as subdirectory-scoped rules. `tgw` MCP server's `TGW_MCP_READONLY` gating (per-role tool grants, PP-HR-001) is a curated-subset pattern, just not yet formalized as a registry. | No new gate needed — this axis is working as intended at TGW's scale. | **Low priority** — not broken, don't build a "Toolshed" for its own sake at TGW's current tool count. |
| Shift-left: local lint/autofix on every push, <5s, so CI rarely fails | Part 1 & 2, "…and iterate" | **Real gap.** No automated local gate exists — `pytest -q` and lint are acceptance *criteria* a packet must satisfy, but nothing runs them automatically before a task is considered done. Verification is currently manual/session-time. | This is a cheap, low-risk mechanical win — a pre-commit-style hook running ruff+pytest on a task branch before it's handed back for review. | **High** — straightforward, no new infra, closes a real "trust the claim vs. verify it" gap that PP-AGENT-DISCIPLINE-001 already names as a pattern to fix. |
| Bounded CI iteration: at most 1–2 automated fix rounds, then hand to human | Part 1 & 2 | **Gap, partially named.** PP-HERMES-EA-001's cross-check already flagged "no code gate on the branch-review 'out-of-control' triggers/fix-attempt cap" as a confirmed-open item (2026-07-16). Stripe's hard 1–2-round cap is the concrete number TGW's prose version is missing. | Prevents an unattended loop from burning tokens/time indefinitely — directly serves the existing "no self-devised safety bypass" / "act, don't just notice" discipline. | **High** — cheap to encode (a counter + a hard stop), directly closes an already-identified gap rather than inventing a new one. |
| Terminal human review before merge/acceptance, always | Part 1 & 2, throughout | **Already TGW's strongest match — arguably stricter.** Invariant C9 (operator gate is permanent, AI output is a proposal) already exceeds Stripe's model: TGW never auto-applies to *live production data* regardless of any check passing; Stripe auto-merges once CI+human-review clear. | This is the one axis where TGW should resist "improving" toward Stripe's model — Stripe's is optimized for code-in-a-trillion-dollar-codebase; TGW's is optimized for irreversible real-world listing/inventory actions. | **N/A — already correct, don't touch.** |
| Central security control framework preventing destructive tool use, backed by a QA-only, no-prod-data environment | Part 2, "Context gathering: MCP" | **Gap, same shape as isolation gap above.** TGW's equivalent (crypto-lock, PP-CATIONIX-001's stated endgame) is explicitly "not this phase." Today's actual protection is scoped credentials + read-only MCP flags — real, but not the sandboxed-environment backstop Stripe has. | This is the last-resort backstop, not the first line — TGW's current per-tool scoping is the practical first line and is already being built (E11 work). | **Low priority for now** — correctly sequenced as PP-AIOPS-001 Phase 5/6 + crypto-lock, already "last, not first" per the existing sequencing principle. |

## Reading the matrix

Three items land at **High** pilot eligibility, and none of them require new infrastructure (no NATS, no nspawn, no NixOS migration) — they're all about mechanizing contracts that already exist as prose:

1. **Worktree isolation, mechanically enforced** (closes the E11 "100% prose" gap for `tgw-coder`)
2. **Automated local lint/test gate on a task branch**, run before a result manifest is produced — not just claimed
3. **A hard-capped fix-attempt counter** for agent self-correction loops, closing the already-flagged PP-HERMES-EA-001 gap

All three are refinements of the *existing* `tgw-coder`/branch-per-task/result-manifest contract — none of them require adopting Stripe's devbox or blueprint machinery wholesale. That matches the research doc's own framing: the transferable lesson is a bounded execution contract, not Stripe's specific infrastructure.

The **Medium** item (naming task execution as an explicit state machine) is the one actual *design* decision worth a real planning session — it's the connective tissue that would let the three High items above compose into something more than three independent patches.

## Not recommended right now

- Building a Toolshed-style central MCP tool registry — not broken at TGW's scale.
- Pursuing devbox-style pre-warmed isolated environments — that's PP-AIOPS-001 Phase 5, already correctly sequenced behind PP-NIXOS-001/portability decisions, not something this research should accelerate.
- Touching the operator-gate model to look more like Stripe's auto-merge-after-review flow — TGW's is deliberately stricter and that's correct for the domain.

## Suggested next step

If Dave wants to move on this: the three High-eligibility items are small, independent, mechanical fixes to already-identified gaps (not new design) — each could be a normal work packet under PP-AGENT-DISCIPLINE-001 or PP-HERMES-EA-001 without needing a further planning pass. The Medium item (named task state machine) is the one worth a dedicated design session before any packet is written, since it would shape how the three mechanical fixes fit together rather than sitting as three unrelated hooks.

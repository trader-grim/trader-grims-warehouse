# Workflow-ordering delta — 2026-07-25

**Purpose:** Correct the queue/workflow model so that continuity and priority do not let work occur in the wrong process position.

## Rule

A queue is not merely a prioritized list. It is a durable work-continuity structure whose items move through an explicit process state:

`candidate → classify/assign authority → prerequisites/evidence → ready → active → verify outcome → completed | corrective/blocked follow-up`

Human-created, filter-created, system-created, and AI-proposed items all use the same state model.

## Priority rule

Rank only work that is **ready**. Keep blocked high-impact work visible, with its blocking prerequisite and owner, but do not allow it to bypass the prerequisite simply because it is urgent, next in insertion order, or AI-selected.

Priority inputs should be inspectable: operational harm, customer/revenue impact, deadline, dependency-unblocking value, evidence confidence, cost/quota, age, and an explicit Dave override.

## Next Item rule

After a recorded terminal outcome, Next Item selects the highest-priority eligible ready item—or, when the selected priority item is blocked, the next prerequisite job that unblocks it. It hands the person or AI the shared queue context, prior outcome, evidence, gate state, and required action. It does not execute the new item.

## Applied current example

The listing-surface repair cannot proceed to acceptance/deployment after its focused test passes. The declared Nix development environment first has to run the reproducible test command without ephemeral packages. Missing `python-multipart` and `mistune` are therefore upstream package-manager workflow jobs that block final source-fix acceptance, not unrelated cleanup to defer.

## Required queue item fields

`source/creator`, `authority`, `priority rationale`, `dependencies`, `required gates`, `state`, `assignee`, `continuity context`, `evidence links`, `outcome`, and `next-action/stop condition`.

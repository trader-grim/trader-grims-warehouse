# PP-RUNNERCOMMS-001 — the runner-question channel (blocked-task-to-operator communication)

**Status: OPEN, needs a dedicated planning session (Dave, 2026-07-14) — not
yet decided which option wins, not yet designed, not yet built.** Split out
2026-07-14 from PP-HERMES-EA-001's "planner/stitcher run in parallel"
section, where it had grown into three real candidate options — Dave: "akin
to the channel just discussed for the process communication. Seems we need
an overall plan for that piece."

## The problem

A runner (tgw-coder today, any AI worker later) sometimes hits a decision
point it can't resolve itself — a "Decide:"-style question (see #1384's
pattern), a permission-gated action, anything that needs authorization
outside its own scope. Today that runner files a todo and stops; someone
has to notice and answer it. As the number of concurrent runners grows
(PP-HERMES-EA-001's 2-3-runner ceiling, [[feedback-approach]]), "someone
happens to poll the tracker" stops being a safe assumption — an
unsupervised run hitting this gate would just park its worktree
indefinitely with no guarantee of a timely answer.

## Concrete test case grounding this (2026-07-14, same session)

Todo #1286's body had been clobbered by the bug #1384 fixed. Restoring it
was a one-line `tgw todo --update`, but the auto-mode permission classifier
correctly blocked the edit (a pre-existing shared tracker item, not created
that session) and required an explicit yes/no from Dave before proceeding.
In *that* session it was a two-message round trip because Dave was live and
watching. **In an unsupervised tgw-coder run, that same block would park
the worktree until someone happened to poll the todo tracker** — no
guarantee the planner sees it promptly. Use this as the design test case:
would a given channel proposal turn that two-message round trip into
something the runner gets back inside its own task loop, not on the next
tracker poll?

## Three candidate options, none decided

1. **Todos (current mechanism).** A blocked runner files a todo with a
   "Decide:"-style question; the planner picks it up on its own cadence.
   Works today, demonstrated live by #1384. Weakness: latency is whatever
   the tracker-polling cadence happens to be — no guaranteed response time.

2. **An in-process channel.** Dave, 2026-07-14: "todo is the channel we
   have now, but maybe an in-process channel is a good idea, we should
   discuss in planning." A direct message/queue mechanism, lower latency
   than polling the tracker. Not designed — transport, format, and who/what
   listens are all open.

3. **Ask Tigwa to ask Dave.** Dave, 2026-07-14: "you can also ask tigwa to
   ask me. You will likely end up with a better overall result and we will
   waste less of your time spellchecking my crappy typing." Not purely a
   latency improvement like option 2 — Tigwa-as-relay could also improve
   message *quality* (she translates/clarifies before a question reaches
   Dave, rather than Claude parsing Dave's raw typing directly). **Not
   usable yet** — no established conversational channel from Claude to
   Tigwa exists as of this session (all Claude↔a1131 interaction today was
   SSH/system-administration, not messaging); this needs the same
   channel-design work as option 2 before it's real.

These three aren't necessarily exclusive — the eventual design may combine
them (e.g., an in-process channel that a1131 Tigwa also has terminals into).
Don't treat this as a single either/or choice going into the planning
session.

## Convergence with PP-CODEGRAPH-001's Z3 invariant catalog

Flagged 2026-07-14, still ideation not spec (see PP-HERMES-EA-001's
"Planner/stitcher as operating console + decision gate" section and
PP-CODEGRAPH-001's master-plan section for the fuller writeup): the
planner/stitcher isn't just a Q&A responder to runner questions — it's
also the decision gate PP-CODEGRAPH-001's Z3-backed invariant confirmation
feeds into (invariants hold → proceed, invariants fail → replan). Whatever
channel this PP designs is the plausible transport for *both* directions —
runner questions going out, invariant confirmations coming back in — not
just the narrower "runner asks a yes/no" case above. Worth keeping in mind
during design, not a separate mechanism to build twice.

## Cross-links

- PP-HERMES-EA-001 — the planner/stitcher operating model this channel
  serves; original home of this content before the 2026-07-14 split-out.
- PP-CODEGRAPH-001 — Z3 invariant-confirmation convergence, above.
- Todo #1390 — tracks this PP; update its `pp_ref` when this file is read
  by the planning session.
- Memory: [[reference-todo-title-overwrite-bug]] — the #1384 "Decide:"
  pattern that's the working example of option 1 today.

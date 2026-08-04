# PP-RUNNERCOMMS-001 — the runner-question channel (blocked-task-to-operator communication)

**Status: PLANNED 2026-07-16 — option 2 ("an in-process channel") given a
real shape: the mailbox design below.** Split out 2026-07-14 from
PP-HERMES-EA-001's "planner/stitcher run in parallel" section, where it had
grown into three real candidate options — Dave: "akin to the channel just
discussed for the process communication. Seems we need an overall plan for
that piece." Resolved 2026-07-16 when Dave, thinking about a completely
different problem (every actor forgetting where their own inbox is), landed
on the same mechanism this PP needed: **"Everybody has one but needs to
remember where it is. Every worker needs a mailbox. An inbox, a way to send
interprocess, but in an MCP or a skill right there where they can find it so
when we use the term or suggest it it works as well as tgw-exit does."**

## The mailbox design (2026-07-16)

Not a new transport — **the existing per-actor `docs/TGW-Plan-Vault/
inbox/<actor>/` convention, already live for `claude`/`tigwa`/`dave`, made
uniformly discoverable and directly writable by any actor, not just the
owner.** Three pieces:

1. **One mailbox per addressable actor**, same location convention already
   in use — `inbox/claude/`, `inbox/tigwa/`, `inbox/dave/`, extended to any
   new addressable actor as one gets added (a specific `tgw-coder`/Aider
   *task run* is NOT itself a persistent addressable actor — it reports via
   its result manifest/branch, not a mailbox of its own; the mailbox model
   is for standing personas/roles: Claude, Tigwa, Leotha, Dave). This is
   the existing per-actor inbox split (2026-07-15) generalized from "where
   Claude/Tigwa file their own notes" to "where anyone sends anyone else a
   message."
2. **One send mechanism, two front doors, same effect.** A `tgw mailbox
   send <actor> "<message>"` CLI command (new `cmd_mailbox_send` in
   `api.py`, writes a timestamped `MSG-<ts>-from-<sender>.md` into the
   target's inbox dir) is the single implementation. Two ways to reach it
   so it works "as well as tgw-exit does" regardless of which tool
   ecosystem the sending actor is in:
   - A Claude Code skill (`tgw-mailbox-send` or folded into an existing
     skill) for Claude-Code-based actors — same discoverability pattern as
     `/tgw-exit`.
   - An MCP tool (`tgw_mailbox_send`, alongside the existing
     `tgw_enqueue`/`tgw_add_suggest` write-capable tools in
     `mcp_server.py`) for Hermes-based actors (Tigwa/Leotha) — respects the
     existing `TGW_MCP_READONLY` gate the same way those two tools already
     do.
   Both are thin wrappers over the one CLI command — no logic duplicated
   between them.
3. **Discovery, not just delivery.** The existing `SessionStart` briefing
   hook (`PP-AGENT-DISCIPLINE-001`) already surfaces `inbox/claude/`'s file
   count automatically at session start — this is the "remember where it
   is" fix already half-built for Claude specifically. Generalizing it:
   any actor's own startup/briefing path checks their own mailbox the same
   way, so a message sent via step 2 is guaranteed to surface next time
   that actor starts a session or does an equivalent poll, not just sit
   until someone remembers to check.

## How this resolves the original runner-question problem

A blocked `tgw-coder`/Aider runner still can't have its own mailbox
(point 1) — but it can **send to the planner's mailbox** using the same
mechanism (a scoped `tgw mailbox send` call, or the runner's harness
wrapping it) instead of only filing a todo and stopping. Combined with the
already-decided "planner and stitcher run in parallel" model
(`pp/PP-HERMES-EA-001.md`), the planner — live, not polling on a fixed
cadence — is the one checking their own mailbox regularly, so this turns
the #1286-style two-message round trip into something closer to
"whenever the planner next checks in," not "whenever someone happens to
poll the tracker." It does not promise a hard latency SLA (still
async/file-based, not a blocking call) — that's an explicit non-goal here,
matches Dave's own "just a speedbump" framing of acceptable turnaround.

**Option 3 (ask Tigwa to relay to Dave) rides the same rails once she has
a real conversational channel** — no longer blocked on "no established
channel exists," since sending to `inbox/dave/` (or Tigwa forwarding a
runner's message there) is now literally the same mailbox mechanism, not
a separate design.

## Out of scope (this planning pass)

- A synchronous/blocking channel (a runner halting mid-execution waiting
  for a reply) — mailbox is async send/check, matching the "speedbump not
  a stall" framing already settled for this problem.
- Ordinary `tgw-worker@*` systemd queue workers getting mailboxes — their
  existing report path (health checks, dead-letter, `tgw ops-digest`) is
  the settled mechanism per PP-HR-001's scope correction ("ordinary
  workers... responsible to their owning boss, not party to" the
  actor-level contract/comms model). Not superseded by this design.
- The PP-CODEGRAPH-001 Z3 invariant-confirmation convergence (below,
  unchanged from the prior open framing) — the mailbox is a plausible
  transport for that too, but wiring it is that PP's build session, not
  this one.

## Next step

File a todo for the CLI command + skill + MCP tool + SessionStart-hook
generalization — small, mechanical, delegatable per the planner rubric
once written as a packet.

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

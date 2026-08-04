# The planner rubric — how to write a work packet

**Status: reference doc, todo #1414.** Closes pipeline-stage-maturity gap #3
identified 2026-07-14 (`pp/PP-HERMES-EA-001.md`, "Planner/coder/stitcher/
reviewer process maturity"): the packet template worked every time it was
used, but only existed as habit inside whichever session was doing the
planning. This doc is that habit written down, so a fresh planner — a
different person, a different model instance, zero accumulated session
memory — can produce a packet as good as one written by someone who lived
through the whole investigation.

Read together with `.claude/skills/tgw-packet/SKILL.md` (the runner's
contract for *executing* a packet). This doc is the other half: how to
*author* one. Real packets referenced throughout live in
`docs/TGW-Plan-Vault/plan/packets/`.

## What a packet is for

A packet exists so a runner with **no context beyond the packet itself**
can do correct, bounded work without either (a) re-deriving investigation
the planner already did, or (b) guessing at decisions the planner should
have made. Every section below exists to eliminate one specific failure
mode a session has actually hit. If you can't say which failure mode a
section you're writing prevents, you're filling in a template, not
planning.

The organizing tension, in Dave's words: **"loose enough for creativity,
strict enough to get what we want."** Both failure directions are real:

- **Too tight** — spec so prescriptive the runner has no room to apply the
  judgment they're actually good at (matching code style, picking a
  sensible variable name, choosing which of two equally-valid file
  layouts fits). Wastes the runner's competence and makes the packet
  brittle to any detail the planner got slightly wrong.
- **Too loose** — spec vague enough that "what counts as done" or "which
  approach was intended" is the runner's guess, not the planner's
  decision. This is exactly what Prime Directive 3 forbids: an unstated
  cadence/TTL/default becoming a silent substitution. Two real production
  outages trace to this failure mode.

The rubric below is section-by-section guidance for landing in between,
with worked contrast from real packets.

## The seven sections

### 1. Context budget

**Purpose it serves:** stops the runner from loading the whole master
plan and drifting (the failure `tgw-packet` was built to prevent — see
its own doc: "sessions that loaded the whole plan and trusted its
assumptions drifted"). This is a **hard ceiling**, not a suggestion — the
runner is instructed not to exceed it.

**Calibration:**
- List *exact* files, and narrow to line ranges when you can
  (`1274-config-path-safety-validation.md`: "lines ~258-276... only").
  A whole-file citation is fine when the file is short or the runner
  needs to judge the file's existing style (`1108`: "whole file, ~300
  lines").
- If the packet depends on a decision or investigation captured in
  another packet or todo, cite that packet doc directly rather than
  re-summarizing it — `1418` cites `#1416`/`#1417`'s packet docs "for
  full context, don't re-derive the investigation." Re-deriving wastes
  budget and risks the summary drifting from the source.
- Include the one PP doc section the packet lives under, not the whole
  PP file, unless the packet doc itself already excerpts what's needed.
- If you can't name the budget in a sentence or two, the packet is
  probably not scoped tightly enough yet — go back to Spec.

### 2. Verified live before this packet was written

**Purpose it serves:** the packet was authored in a planning session; the
world may have moved by the time a runner picks it up. This section is
the planner's own pre-flight — everything here should already have been
checked against ground truth (`journalctl`, live API read, real item
JSON, `queue_jobs`), not assumed from documentation or code that suggests
a behavior (invariant C11). The runner re-verifies these same claims at
execution time (`tgw-packet` step 2) — this section is what tells them
*which* claims are load-bearing enough to need that re-check.

**Calibration:**
- State facts with their evidence inline, not just conclusions —
  `1108`: "`alt_text` queue: 5 jobs, all state `queued`... oldest
  `created_at` 2026-06-26" is checkable; "the queue has stuck jobs" is
  not.
- If a design decision was made *because of* one of these facts, say so
  explicitly — `1108`'s "Given the entry point already exists... option 1
  is the evidence-supported smallest fix" tells the runner *why* this
  spec and not an alternative, so they don't second-guess it mid-execution.
- Distinguish "verified, stable" facts (schema, file layout, established
  precedent — `1418`'s citation of `price_history` as proven precedent)
  from "verified, may have moved" facts (queue depth, current worker
  status) — the runner needs to know which ones are worth a fresh check
  before trusting them.
- If Dave gave an explicit go-ahead or made a call recorded elsewhere,
  quote it and cite where — `1108`: "Dave, 2026-07-14: 'yes same
  process.'" This is what lets the runner treat the direction as settled
  rather than re-litigating it.

### 3. Spec

**Purpose it serves:** the actual instructions. This is where the
tight/loose calibration decision is made explicit, section by section
within the spec itself — and it's the section most sessions get wrong in
one of the two failure directions above.

**Calibration — the core technique, worked from real packets:**
- **Be exact where correctness depends on exactness.** `1274` gives the
  literal replacement code for a security-critical regex/path-containment
  check, with an explicit "do NOT loosen this" — there is no safe range
  of runner discretion here, so none is offered.
- **Name the decision and explicitly hand it to the runner where it
  doesn't matter which way it goes**, rather than silently leaving it
  unstated. `1418` point 4: "could live in
  `src/tgw/ebay/draft_specifics.py` or alongside #1416's translation
  function, implementer's call" — the planner considered both options,
  confirmed neither changes correctness, and says so in words, so the
  runner doesn't have to guess whether omission was intentional. Compare
  to leaving it out entirely, which is Prime Directive 3's silent
  substitution risk even when the outcome would be identical — say it
  out loud even when you're punting.
- **When delegating a mechanism choice, still pin the requirement it must
  satisfy.** `1418` point 6: "a `catalog-verify` rule (or a repo-level
  grep-based check... your call on which fits better)" — the *what*
  (detect direct dict access outside the accessor modules) is fixed, the
  *how* is the runner's call, and the packet says which is which.
- **Number sequential steps when order matters** (`1108`'s 6-step spec,
  `1418`'s 9-point spec) — a flat prose paragraph forces the runner to
  infer sequencing that the planner already knows.
- **Cite the established pattern being extended, not just the new
  code.** `1418` cites `price_history` as "the proven precedent being
  extended" — this both justifies the design (not inventing something
  new) and tells the runner where to look for the shape to match.

**Anti-pattern to catch in your own draft:** if you find yourself writing
"handle this appropriately" or "add proper error handling" with no
further detail, stop — that's the too-loose failure mode. Either specify
what "appropriate" means here, or explicitly delegate it with the reason
it's safe to delegate (per the `1418` pattern above).

### 4. Out of scope

**Purpose it serves:** the adjacent broken thing a runner finds mid-task
is real and worth surfacing, but fixing it inline turns one packet into
an uncontrolled two. This section pre-empts the runner's own good
instincts from expanding scope, and tells them what to do instead (file a
todo, mention it — never silently fix and never silently skip).

**Calibration:**
- Name specific other todos/packets that touch adjacent code and say
  explicitly not to touch them — `1274`: "#1273, #1275, #1284 — do NOT
  touch `http_server.py`, `catalog.py`, or `sku_migration.py`." Vague
  scope boundaries ("don't touch unrelated things") don't help when the
  runner is staring at code that *looks* related.
- If a batch of packets share a dependency ordering, say which packet
  must land first and why — `1418`'s "must land and merge before #1416
  and #1417 start" with the one-sentence rationale ("a schema change
  under a moving fix makes things worse, not better").
- State the boundary between "what this packet changes" and "what it sets
  up for a later packet" explicitly when the two are easy to conflate —
  `1418`: "this packet only establishes the container shape... not the
  actual boundary-fix logic (#1416)."

### 5. Dataset

**Purpose it serves:** Prime Directive 1 made concrete for this specific
packet — does this touch the permanent record, and if so, how is it
protected. This section exists so "preserve the dataset" isn't a
principle the runner has to remember and apply correctly on their own;
the planner has already worked out what it means for *this* change.

**Calibration:**
- If there's no dataset risk, say so plainly and briefly — `1274`:
  "None — this only rejects malformed/malicious input earlier." Don't
  pad a real "none" into false ceremony.
- If there is real risk, name the concrete mitigation already required by
  standing invariants, not a bespoke one — `1418` cites invariant E5
  (archive before overwrite) and requires dry-run-first plus sample
  verification before touching the full catalog, and explicitly separates
  "packet acceptance" (dry-run + sample) from "full-catalog run" as a
  distinct, later go/no-go. A migration packet that doesn't split these
  two checkpoints is asking the runner to make a full-population go/no-go
  call that should be Dave's.
- Say explicitly what does *not* get fabricated — `1418`: "no
  retroactive history reconstruction — that data was never captured,
  don't fabricate it." A runner filling a gap with a plausible-looking
  value is a Prime-Directive-1-adjacent risk worth naming directly when
  it's a live temptation.

### 6. Acceptance (live)

**Purpose it serves:** Prime Directive 4 — "tests pass" is not done. This
section is the actual definition of done, and it must be checkable by
someone who wasn't in the room: a URL, a log line, an item JSON diff, a
before/after count, an explicit command.

**Calibration:**
- Every acceptance item should be something the runner can *paste into
  the result manifest*, not something they attest to. Compare `1274`'s
  six numbered live calls with concrete expected outcomes (raises
  `ValueError`, returns the same path) to a vaguer "confirm the fix
  works."
- Bidirectional checks where the change is reversible — apply, confirm,
  revert, confirm reverted — are the standard for anything touching live
  external state (per `tgw-packet`'s own step 4); state this explicitly
  in packets that touch eBay/live listings rather than relying on the
  runner to remember the general rule.
- For packets with a large-blast-radius final step (a full-catalog
  migration, a `nixos-rebuild switch` on production), split "packet done"
  from "large step executed" as two explicit checkpoints
  (`1418` point 5, `1108` point 6) — this keeps a runner's optimism about
  their own work from being the thing that authorizes a big irreversible
  step.
- Always end with "full offline suite, zero regressions" unless there's a
  reason not to (there almost never is) — every real packet reviewed for
  this rubric includes it.

### 7. Quota/risk

**Purpose it serves:** flags metered-API and thermal/production risk
*before* the runner starts, not as a surprise mid-execution
(feedback-api-quota-flagging — disclose while building, not after a 429).

**Calibration:**
- Quantify, don't just flag — `1108`: "5 alt_text jobs... negligible
  quota impact (5 calls)," not "some LLM calls will happen."
- If the real risk isn't API quota but something else (a bad
  `nixos-rebuild switch` on the live host, a full-catalog write), say
  that plainly and point back to whichever Acceptance checkpoint
  mitigates it (`1108`, `1418`) — this section and Acceptance's
  checkpoint-splitting should visibly agree with each other.
- "None" is a fine, complete answer when it's true (`1274`) — don't
  invent risk to fill the section.

## Sizing: one packet, or should this be two?

A packet is too big if:
- Its Spec section has sub-parts that could each pass Acceptance
  independently and don't depend on each other's completion.
- Its Context budget can't be stated as a short, bounded list — needing
  "the whole plan" or "several PP docs" to understand it is itself a
  signal the investigation hasn't been narrowed enough yet.
- `tgw-packet`'s own constraint applies at execution time too: "if the
  packet turns out to be two packets, split it" — but catching this at
  planning time is cheaper than a runner discovering it mid-execution.

A packet is too small (should be merged or isn't worth a full packet) if
splitting it from its neighbor packet would force the runner to
re-establish context the neighbor already has for no isolation benefit —
watch for two packets whose "Verified live" sections would be
word-for-word identical.

## Ordering and batches

When packets form a dependency chain (schema-foundation-then-consumers,
security-fix-then-dependents), say so at the top of every packet in the
chain, not just the first — `1418`'s opening line and `1274`'s Track
field ("run alone, not concurrent — #1273/#1275/#1284 all depend on
this") both put the ordering constraint where a runner picking up any one
packet in the batch will see it immediately, not just where the planner
happened to write it once.

## Self-check before dispatch

Before marking a packet ready, the planner should be able to answer yes
to all of:
1. Could a runner with zero memory of this investigation execute this
   packet correctly using *only* what's inside it plus the Context
   budget's named files?
2. Every place I left a decision open — did I say so in words, or did I
   just... not mention it? (The second is a Prime Directive 3 risk.)
3. Is Acceptance checkable by someone who wasn't there — evidence, not
   attestation?
4. If this packet touches the dataset, does Dataset name the concrete
   invariant/mitigation, not just "be careful"?
5. Would I bet the packet's Out of scope list actually stops a
   reasonable, well-intentioned runner from touching the adjacent thing I
   don't want touched yet?

If any answer is no, the packet isn't done — the planner's job, not the
runner's, is to close that gap before dispatch.

## Cross-links
- `.claude/skills/tgw-packet/SKILL.md` — the runner's execution contract;
  this doc is its planning-side counterpart.
- `pp/PP-HERMES-EA-001.md`, "Planner/coder/stitcher/reviewer process
  maturity — decision 2026-07-14" — the gap analysis this doc closes.
- `docs/TGW-Plan-Vault/plan/packets/` — worked examples cited throughout;
  read a few before writing your first packet if this is your first time
  in the planner role.
- CLAUDE.md's development doctrine section — "a packet without an
  explicit spec is not delegatable" is the one-line version of this whole
  doc.

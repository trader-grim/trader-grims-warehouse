# PP-KNOWLEDGE-001 — the knowledge & translation hub (full detail)

## PP-KNOWLEDGE-001 — the knowledge & translation hub — 6-LAYER UMBRELLA, extended 2026-07-11
**Corrected from "5-LAYER" 2026-07-12 (Fable independent review #1338) — the
Graph/Graphify row was added this session without updating the count.**
**PLANNED s45 (2026-07-04), extended this session into the full 5-layer
umbrella (Concept 2).** Leotha (PP-HERMES-EA-001) curates/organizes the data
long-term; this plan is the architecture only.

**The vision statement, Dave 2026-07-16, tying pm_intake + this hub together:**
"A library with a librarian that can tell you where everything is, cross-
referenced, in your language, with footnotes. Hopefully." Aspirational, not
yet built — but it's the one sentence that unifies what's otherwise scattered
across several PPs: pm_intake's filing/organizing behavior (restored under
Tigwa's persona, PP-HERMES-EA-001) is the librarian's *intake* half; this
hub's Storage/Search/Graph layers (git-annex, Recoll, Graphify) are the
*stacks* she works from; "in your language" is the MCP/query-front-door
layer answering in natural language, not raw grep; "with footnotes" is
citation/provenance back to the source document, not just a location pointer
— an explicit bar the eventual query surface should be held to, not assumed
free. Nothing here is scoped or built yet; recorded so the destination stays
visible while the git-annex/Recoll starting point (below) gets built first.

**Filing authority + the plan's own end-state, reinforced 2026-07-16
(Dave):** "all of the filing locations and tasks are the librarian's
responsibility. Just tell what goes where." **Clarified same day:** once
trained, the librarian creates new locations too, not just chooses among
existing ones — other actors' job shrinks to either (a) defining a new
document *type* when one doesn't fit an existing category, or (b) simply
handing raw material to her to route herself. This is explicitly the
pm_intake pattern restored under Tigwa's persona, not a new invention — see
CLAUDE.md's "Tigwa's own persona is pm_intake's replacement direction."
Going forward, Claude's (and any other actor's) job when producing a
document is to classify/tag it or just send it to her — never to
unilaterally invent or own a folder/taxonomy. That authority belongs to the
librarian role (Tigwa/Leotha, [[PP-ANNEX-001]]'s "archivist" framing), a
standing priority Dave gave Tigwa directly 2026-07-15. Concrete instance
from this same session: the `reports/` directory + its filing README (below)
were created unilaterally during the plan-reconciliation pass — correct as
an interim stopgap (nothing lost, Prime Directive 1), but its
structure/rules are provisional pending the librarian's own reconciliation,
not a Claude-owned convention going forward.
**Real gap this surfaces, not yet in any contract:** the dual-reviewed
operational-contract cluster ([[PP-HR-001]]) covers identity, review,
tool/access boundaries, and mechanical enforcement — it does not yet contain
an explicit clause assigning filing/taxonomy authority to the librarian and
requiring other actors to classify-and-hand-off rather than decide
structure themselves. Worth adding as a term the next time Tigwa's or any
worker's contract is touched.

**Extended to search/recovery, 2026-07-17 (Dave):** the librarian's
authority isn't just *where new things go* — it's also *finding things
that went missing*. After a Claude-run "recover lost PPs" sweep this
session (found PP-ROUTER-001 orphaned, corrected a false claim about
PP-DOCLIB-001), Dave: "the librarian can handle all that. She has been
working nights when it is cooler on that." **Correction, same session
(Dave):** "I did define the responsibility and tell her the weather" —
not her own initiative; Dave assigned it directly and briefed her on the
thermal picture that shapes her schedule. Two things this settles: (1)
search/reinstate requests route to Tigwa, not Claude, going forward — see
[[feedback-pp-recovery-is-pull-based]]; (2) she's executing an explicit,
Dave-assigned responsibility on a Dave-briefed schedule, not something she
discovered or decided to take on herself.

**The plan's own ultimate destination (Dave):** `TGW-Master-Plan.md` itself
is meant to migrate into this knowledgebase architecture long-term — lighter
to consume, less of a startup-context burden. Not started, no timeline;
`tgw-plan-maintain` (the hygiene skill built 2026-07-16) is the interim
discipline until this hub can absorb the plan's own overflow instead.

**Absorbed 2026-07-11 (Dave):** PP-DOCLIB-001 and PP-HISTORY-001 fold in
here — "it's recoll+mcp on the knowledgebase." Both were existing facilities
(document cross-referencing, `tgw history-index`) that sit ON TOP of this
hub's Search (Recoll) and MCP (agent front door) layers, not separate PPs.

**Correction, 2026-07-17:** the line above ("no standalone design docs
existed") was wrong — `docs/ai-plans/pp-doclib-001.md` (todo #1044) is a
real, substantive design (4-bucket taxonomy, a `tgw docs` CLI wrapper,
`[[wikilink]]` cross-referencing) found during a "recover lost PPs" sweep.
**Confirmed by Dave, no action needed:** "it was a proposal, we went
another quicker route for now" (recoll, this section) — the doc stays as
historical record, not merged back in.

| Layer | Tool | Answers | Status |
|---|---|---|---|
| Storage | git-annex (canonical files, dedupe) | — | **PP-ANNEX-001, promoted 2026-07-11 — see below** |
| Search | Recoll (full-text/metadata) | "where is the evidence?" | **PP-SEARCH-001, LIVE** at `/opt/TGW/.recoll/` (441K docs) |
| Core spine | **PostgreSQL LISTEN/NOTIFY** (event bus) | pays off broadly: charting/forecasting, photo-set production-time analytics, the event server (PP-EVENTD-001), research feeding AI workers | **RESOLVED 2026-07-11** — not NATS JetStream, see note below |
| Memory | Hindsight (timelines/experiences) | "what happened before?" | exploratory — prebuilt layered ON the core spine, not committed |
| Knowledge | gbrain (curated "working truth") | "what do we believe now?" | exploratory — same, not committed |
| Graph | Graphify (code + doc relationships) | "what connects to this?" | **detailed design merged in from PP-CODEGRAPH-001, 2026-07-14** — 4-layer stack (Tree-sitter/FalkorDB code graph, Postgres+Z3 invariant catalog, DuckDB execution-trace store, unified MCP layer), hosted on a1131, see PP-CODEGRAPH-001 section below for full design; awaiting Dave's research before build |

**Core-spine NATS-vs-Postgres note (2026-07-11, do not conflate with
PP-AIOPS-001's separate JetStream use):** PostgreSQL LISTEN/NOTIFY wins for
this general operational event bus (clip-route/knowledge-hub/UI routing) —
`FUTURE-IDEAS.md`'s old NATS mention here is superseded. **This is a
DIFFERENT question from PP-AIOPS-001's JetStream audit/CDC stream**, which
is still the intended mechanism for durable, replayable mutation-history
logging (Dave, 2026-07-11: "we want the transactional logging") — that use
of NATS is NOT superseded, the currently-failing `nats` health check
("No module named 'nats'") is a real gap for PP-AIOPS-001 Phase 1 whenever
picked up, not a moot pre-existing nuisance.

**Full plan + soundness review (4 system-specific guards, reject list):**
`docs/ai-plans/recoll-annex-jetstream.md` — treat as the design doc of
record for the storage/search/annex legs (Graphify/Hindsight/gbrain are new
this session, not yet in that doc).

**Dave's stage 1 (s45): "organize and make accessible all of our valuable
data," as a concerted parallel lane alongside the fix/execution tracks** —
the knowledge dataset becomes a better discovery search than the catalog
(catalog stays the structured/UI projection; recoll is the find-anything
layer). recoll already paid for itself in week one (real recovery/audit
queries, s44/s45).
Stage-1 packets: #1147 (R2 search surface — priority), #1148 (R1 field
mapping), #1149 (A0 Syncthing/annex boundary decision, Dave 15min), #1150
(A1 annex pilot on archive corpus). Drive-fleet manifests continue under
PP-DRIVE-INDEX (#1136).

**Starting point for Tigwa's knowledgebase work, decided 2026-07-14 (Dave):**
"my first research started with graphify, but I found the better solution."
The Graph/Graphify layer (FalkorDB/Z3/DuckDB/MCP, PP-CODEGRAPH-001 section
below) was Dave's original research target, but he's since decided
git-annex + Recoll (Storage + Search, A0/A1 above — A0's boundary decision
is already set, A1 is unblocked) is the better starting point for Tigwa to
actually begin on and for Dave to get familiar with the tools hands-on.
Event fabric (Track E / "JetStream", see `recoll-annex-jetstream.md`) is
explicitly deferred — not part of this starting point. Graph/Graphify
still needs its own planning pass (5 open packaging questions, see
PP-CODEGRAPH-001 section) before Tigwa or anyone builds it — not skipped,
just sequenced after git-annex/Recoll.

**Target use cases, same decision (Dave, 2026-07-14):** PP-DATAINTEGRITY-001
(see its own master-plan section) is what Tigwa targets with this — not
a generic "index everything" exercise. Concrete starting scope: the
photo-integrity design's open legs 2/3, and the `status`/`#STATUS`
write-path reconciliation once scoped. Grounds the buildout in real,
already-identified reconciliation work instead of an abstract capability.

**This is infrastructure, not an iterated/churny tool (Dave, 2026-07-14):**
"I want this to be an infrastructure piece. When it is mature and we have
better hardware they may live side by side." a1131 hosts it for now (good
workspace, thermal-relief compute, no production traffic), but unlike
Hermes/Aider (deliberately kept in userspace — PP-NIXOS-001's standing
rule, see `decouple-hermes-aider-flake.md`) the knowledgebase stack is
meant to mature into real settled infrastructure that could eventually run
alongside tgw-prod's production stack on better hardware. Practical
consequence: package it **declaratively in a1131's flake** (`git-annex`,
`recoll`), not via imperative `nix profile install` — directly applying
today's lesson from the Hermes incident (imperative per-user nix-profile
installs broke `hermes update` on two hosts because they're just as
immutable as a flake package but without any of the declarative tracking).
This also leans the still-open FalkorDB packaging question (Graph layer,
PP-CODEGRAPH-001) toward "NixOS service" over "userspace nix-profile" —
not decided yet, but the precedent points that way.

### PP-ANNEX-001 — the archiving/librarian layer — PROMOTED 2026-07-11
**"A librarian/archivist tool built into the library itself"** (Dave) —
git-annex doesn't manage the library from outside, it replaces the file
with a symlink and tracks location/metadata directly in the repo. Full
prior design moved from `FUTURE-IDEAS.md` into `docs/ai-plans/recoll-annex-jetstream.md`
(Track A, packets A0-A5); do not relitigate what's already settled there:
git-annex replaces Syncthing for data trees; LAN hosts (a1131) are plain ssh
git-annex remotes (wire-speed); Google Drive is the off-site/portable/backup
tier ONLY, never the LAN rendezvous; plan vault stays plain git, never
annex; scope = history/archive corpus consolidation ONLY, ItemData stays
fence-owned and untouched (A4 rescoped away from live-data migration);
`numcopies=2`; date-partitioned `gdrive-archive-YYYY`; Dave approves every
deletion (C9); A5 (Go companion tool) deferred until stock remotes proven.

**A3 cloud backend — SETTLED 2026-07-11: Google Drive** (not GCS/S3 — new
metered spend not justified below the $4k-server budget line, even though
git-annex's native `type=S3` would be the cleanest integration technically).
Current capacity: 2TB Google One @ $100/yr, upgrade path to 5TB @ +$140/yr.
**Adapter kept genuinely open, evaluate empirically**: rclone special remote
(already proven in production for PP-PHOTO-001 photo sync, zero new auth)
vs. native `git-annex-remote-googledrive` (Lykos153, direct API, needs its
own OAuth credential) vs. anything else found during the A2 pilot.

**"The archivist" reframe (Dave, 2026-07-11):** archiving stops being a
hardcoded library call (`items.atomic_write_json(..., archive_root=...)`
zipping inline) and becomes a delegated hand-off to one authoritative
service that owns the full chain — archive (zip, existing E5/#1104
mechanism) → log → index (Recoll) → place (git-annex → GDrive) — driven by
a filing policy Leotha curates over time. **Open design constraint, not yet
solved:** E5/#1104 is explicitly fail-closed (write must not proceed unless
archive succeeds) — delegating to an external service risks losing that
synchronous guarantee unless the hand-off blocks for ack or there's a
durable write-ahead step. Real design work, candidate for "model the
worker in Hermes first" (PP-HERMES-EA-001) before it touches the live
`items.py` write path.

**Concrete instance surfaced 2026-07-21 — `alt_text`'s history-archive step
is exactly this hand-off, currently built wrong.** Today's implementation
(#1407/#1408, `_history_root_reachable()` guard) still writes directly to
the `history` symlink onto the removable `MasterArchive` drive, and only
fails soft (defer + C11 finding) when that drive happens to be unmounted —
i.e. the per-item worker still couples to external mount state, just
non-fatally. Dave's correction, same conversation: **wrong strategy, not
just an incomplete one.** Reframed per this section's own "separation with
continuation and resolution" pattern (Dave's own physical-archive workflow,
applied here) —
1. **Separation** — `alt_text` (and anything else archiving to `history`)
   writes to a local, always-available staging area, never touches the
   removable drive directly. No unmounted-drive branch to maintain, because
   the per-item write never depends on it.
2. **Continuation** — the pipeline always proceeds the same way regardless
   of archive-drive mount state; today's `archive_target_unmounted`
   deferred/C11 finding path goes away entirely, not just gets more robust.
3. **Resolution** — a separate, later, idempotent job (the librarian/
   archivist hand-off this section already names) sweeps staging into the
   real library whenever the target is available, then clears staging.
   **Distinct from item-archive's existing zip-merge (E5/#1104):** for this
   data class, resolution is **hash, then archive** — content-addressed,
   not zip-bundled (`alt_text.py` already computes an image hash for its
   `store_hash`/`lookup_hash` dedup cache, so the hash is available at
   staging time, not something new to compute).
This is the `alt_text` case specifically of the general archive/log/index/
place hand-off above — not a new pattern, a concrete example of it, and
should be designed/built together with the general hand-off rather than as
its own one-off fix to #1407/#1408. Not yet scoped into a packet.

**Interim guidance, Dave 2026-07-21: "the important part for now is don't
write to a symlink and you'll be ok for a while."** Don't wait for the full
Resolution-step design (hash-then-archive into the real library) before
fixing anything — the minimum near-term fix is just Step 1 (Separation):
stop `alt_text.py` from writing through the `history` symlink at all, land
the copy in a local staging directory instead. That alone removes the
unmounted-drive coupling and holds until the full archivist hand-off is
designed/built. Small, well-scoped, hasn't been dispatched yet.



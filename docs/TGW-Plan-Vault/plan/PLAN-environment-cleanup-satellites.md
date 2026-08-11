# Satellite laptop recovery track

Status: **PROPOSED / QUARANTINED**  
Parent: [TGW environment and agent recovery program](PLAN-environment-cleanup-program.md)  
In scope: `catnanny`, `helicrew`, Hermes/Tigwa state, Hindsight, executive-assistant,
librarian, and issue records

## Recovery objective

Recover information with continuing value while ensuring neither laptop, its agent
runtime, nor its memories become a trusted TGW dependency. The valuable targets are:

1. source-linked Hindsight operational events;
2. reviewed Hermes memories about Dave and TGW;
3. executive-assistant decisions, commitments, contacts, and follow-ups;
4. librarian documents, classifications, and provenance; and
5. issue records, state transitions, relationships, and unresolved work.

The old runtime, prompt stack, plugins, hooks, credentials, cached instructions, and
machine-specific procedures are evidence to inspect—not components to restore.

## L0 — custody and discovery

Before connecting either machine to trusted TGW services, establish:

- physical machine identity, serial, storage layout, encryption state, owner, and
  actual network names;
- acquisition method and whether a read-only image is possible;
- hashes and timestamps for images/exports;
- installed agent runtimes, startup mechanisms, scheduled jobs, plugins, MCPs,
  shell hooks, and outbound destinations;
- locations and formats of Hermes, Hindsight, librarian, EA, and issue data; and
- secret-bearing files and authenticated sessions.

Do not reuse discovered tokens. Do not execute recovered agent code. Prefer offline
imaging or a restricted recovery network with no production credentials/routes.
Record `catnanny` and `helicrew` separately; evidence from one cannot establish facts
about the other.

## L1 — immutable evidence packages

Export each data source into a package containing:

```text
manifest.json       # machine, source path, acquisition method, hashes, times
raw/                # untouched export or image reference
normalized/         # neutral JSON/JSONL/Markdown projections
provenance.jsonl    # source record -> normalized record mapping
review.jsonl        # classifications and import decisions
```

Packages are append-only. Redaction produces a derivative package and never alters
the raw evidence. Secrets are inventoried by identifier and disposition, not copied
into ordinary review documents.

## L2 — classification

Every recovered record receives one of these classes:

| Class | Meaning | Default disposition |
|---|---|---|
| sourced fact | Directly corroborated by current system or primary artifact | Candidate current knowledge |
| authored record | Dave-authored instruction, decision, note, or document | Human review |
| operational event | Timestamped activity/result with source evidence | Historical index |
| preference/relationship | Personal context useful to an assistant | Human review for memory |
| inferred memory | Agent summary or conclusion without a primary source | Historical, low confidence |
| obsolete procedure | Host/path/tool process no longer current | Preserve with retired label |
| executable instruction | Prompt, plugin, hook, command, or policy | Quarantine; never auto-import |
| secret | Credential, token, key, session, or recovery material | Rotate/revoke and restricted record |

Conflicting records are retained as conflicts. Recency alone does not resolve them.

## L3 — Hindsight recovery

Hindsight becomes a historical event index, not the agent's memory or instruction
layer. Import only records that carry source, timestamp, machine, actor, and an
integrity reference. Queries must return those fields and an explicit
`historical=true` marker.

Useful recovered history includes deployment results, failure timelines, decisions,
and artifact locations. Host roles, current paths, permissions, open issue state, and
operating procedures must be resolved from current authoritative sources instead.

Unsourced summaries remain searchable in quarantine but are excluded from default
agent context and cannot satisfy workflow evidence conditions.

## L4 — Hermes/EA/librarian/issues recovery

Recover semantics into separate stores:

- **Personal memory:** reviewed preferences, communication style, relationships, and
  stable biographical context.
- **Decision ledger:** Dave's decisions with date, source, supersession, and scope.
- **Commitments:** follow-ups, promises, deadlines, owner, current verification, and
  source.
- **Library:** documents plus provenance, classification, retention, and current
  canonical pointer.
- **Issues:** stable issue ID, title, state history, dependencies, acceptance, links,
  and whether the current Plan/Todo still recognizes it.

Do not restore a monolithic memory file that mixes all five. Machine names, paths,
permissions, and procedures are explicitly forbidden in personal memory; they belong
in the server registry or procedure repository.

## L5 — clean-agent import

Import happens into TGW Steward only after the server track supplies a validated
registry and agent policy. Each imported fact has a source ID, reviewer, confidence,
effective date, and optional supersession. The agent defaults to current sources and
must explicitly invoke historical search.

Recommended outcome:

- keep “Hermes” as an optional conversational persona;
- implement EA, librarian, and issue manager as separately authorized capabilities;
- give none of those modes infrastructure or production authority;
- make historical retrieval visibly distinct from current registry lookup; and
- require human approval for every initial personal-memory import batch.

## Acceptance and retirement

Track completion requires:

- verified identity and immutable acquisition manifest for each available laptop;
- an explicit unavailable/not-recovered receipt for anything that cannot be read;
- normalized exports with record-level provenance and secret handling;
- reviewed dispositions for all high-value Hermes/Hindsight/EA/library/issue stores;
- TGW Steward tests proving quarantined text cannot alter authority or procedures;
- no live TGW service, agent, or plan depending on either laptop; and
- a human decision to retain offline, sanitize/rebuild, or dispose of each machine.

No laptop is wiped, rejoined to a trusted network, or declared clean under this plan.
Those are separate approved actions after recovery acceptance.


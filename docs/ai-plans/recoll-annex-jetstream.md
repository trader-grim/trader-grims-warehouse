# recoll-annex-jetstream: the TGW knowledge plane — universal index + content-addressed storage + event backbone

**Status:** Draft for Dave's review — 2026-07-04 (session 45 big-picture planning)
**PP ref:** proposes **PP-KNOWLEDGE-001** (umbrella) coordinating PP-SEARCH-001 (live),
PP-ANNEX-001 (promote from FUTURE-IDEAS), **PP-EVENTS-001** (new — the JetStream leg),
PP-DRIVE-INDEX-001 (feeder), PP-CATALOG-INCR-001 (first consumer), PP-HERMES doc-triage.
**Sources:** Hermes/Perplexity transcript (inbox/archive/20260704T020203-hermes-…,
§§630–1963 — the part Dave deliberately reserved for "a separate, larger planning
session": this one), FUTURE-IDEAS PP-ANNEX-001 + PP-SEARCH-001, settled-architecture
control/data-plane principle (2026-06-28), PP-CATALOG-INCR-001, PP-DRIVE-INDEX-plan;
plus Dave's home-dir research corpus (2026-07-01, most current):
`~/Downloads/hermes-catio-git-annex-recoll-design-research-very-long.md` (MCP-recoll
bridge, 2014-archive "architectural philosophy" retrieval, isolation-platform
blueprint — the latter deliberately excluded here),
`~/notes/nats-jetstream-btrfs-transactional-processing-and-resiliancy.md` (bus
selection: JetStream over Redpanda/Kafka for this footprint; Temporal rejected — see
soundness guards), `~/Downloads/decoupled Control Plane ⁄ Data Plane protocol.txt`
(intact — the inbox pdf/html copies of the same session are damaged captures; use the
txt, delete the corpses).

## Problem / motivation (Dave, s45)

"One of our highest-value tools not fully planned." The business runs on one truth set
(ItemData JSON + assets) plus a growing constellation: catalogs, archives, drive fleet,
plan vault, agent memories. Today: recovery/audit queries used to take hours (recoll
fixed the read side); photos/bulk data have no placement/dedup/tiering discipline
(drives at 83%, consolidation blocked on manual work); events exist as journald lines
that rot after 3 days; every agent invents its own memory. The knowledge plane gives
each concern a durable home and gives every agent (Hermes, Claude, aider, opusplan
sessions) the same substrate — Dave stops being the integration layer.

## The settled shape (from the transcript — treat as design-approved by Dave)

Five named layers. Naming is load-bearing; do not blur them:

| Layer | Component | Role |
|---|---|---|
| Knowledge store | **git-annex** | Canonical large-object store: content-addressed (SHA256), location-tracked, metadata-tagged, tiered remotes |
| Knowledge retrieval | **Recoll** | Full-text + field search over everything (already live, 9.1G index) |
| Event/state fabric | **NATS JetStream** | Durable, replayable event log: mutations, transitions, snapshots, intents |
| Agent interface | **Site MCP server** (tgw MCP) | The one front door agents use for all three |
| Execution clients | Hermes / Claude / aider / Gemini / Flutter app | Consumers; own NO memory of record |

Separation rule (verbatim from design): documents → annex; searchable extracted
knowledge → Recoll; operational facts/transitions → JetStream; curated
decisions → versioned docs. Never collapse them into one "memory."

Control/data-plane principle applies throughout: git tree/NATS subjects are control
(pointers, notifications — never block, never carry bulk); annex remotes/Postgres/API
fetch are data. A data-plane failure must never hang a control channel.

The portable-client corollary (already refined in the transcript): clients are
consumers, not authorities. Reads come from projections (SQLite snapshot + pending
overlay + resolved view); writes go upstream as **intents** through the state-machine
pipeline; `catalog.snapshot.available` events collapse the overlay. PP-CATALOG-INCR-001
is the server half of this and is already drafted.

## Ground truth — verified live 2026-07-04 (not assumed)

| Piece | State |
|---|---|
| recoll | **LIVE.** 9.1G index at /opt/TGW/.recoll; ItemData/ItemArchive/masterarchive/catalogs/vault + first external drive (db-home) indexed; PP-SEARCH-001 Phase 0 + PP-DRIVE-INDEX Phase 4/Track A done |
| git-annex | **Installed** on tgw-prod (system package) — zero repos initialized, unused |
| NATS server | **Does not exist** — no systemd unit anywhere in flake or host |
| nats-py | **Not in the prod venv** — `import nats` fails; health check red for weeks |
| ITEMDATA_MUTATIONS code | `apis/nats_client.py` (347 lines, fire-and-forget, drops silently when NATS absent — by design). Called from `items._write_field` + `items.set_fields` (s44 fix) — i.e. **every publish since birth has been dropped**, and the hook misses the real fence door (`http_server._apply_patch`/`_apply_ebay_write`) |
| QUEUE_TRANSITIONS | Subject constants defined, publisher never wired (Phase 2 stub) |
| Drive fleet | PP-DRIVE-INDEX Phase 0 tooling built (survey_drive.sh); dedup = cross-drive consolidation, identify-and-report ONLY, Dave approves every deletion (standing rule) |
| a1131 | ro NFS views of data+logs live; claude account + sudo; WoL — a second indexing/consumer host is now practical |
| MCP server | tgw MCP live (docs/runbooks/plan/aider/items/queue tools) — the front door already exists |

Key implication: **JetStream is the only missing leg**, and it's cheap — NATS is a
single static binary, JetStream is a config flag, nats-py is a pip install. Everything
else is sequencing and wiring.

## Tracks and packets

Packet sizing follows the work-packet format (RETARGET §work-packet). Sizes:
XS ≤ half session, S ≤ 1 session. Every packet ends with live evidence (/tgw-packet
discipline). Suggested todo priorities in parens.

### Track E — event fabric (PP-EVENTS-001, new; absorbs PP-AIOPS-001 Phase 1 wiring)

- **E1 (S, p15): NATS+JetStream server in the flake.** nix/tgw/ module (TGW layer, not
  CatioNIX): `nats-server -js`, storage under /opt/TGW/var/nats, listen localhost +
  tailnet only (no LAN-wide 4222 until a consumer needs it), memory/disk limits set,
  health check flips from "module missing" to real round-trip probe. Evidence: `nats
  stream ls` shows ITEMDATA_MUTATIONS + QUEUE_TRANSITIONS created by init_nats.
- **E2 (XS, p15, same session as E1): nats-py into the prod venv** + health green.
  Evidence: `tgw health` nats check ok; one `publish_mutation` visibly lands (nats
  stream view).
- **E3 (XS-S, p16): move the mutation hook to the real fence door** — `_apply_patch` +
  `_apply_ebay_write` (this IS PP-CATALOG-INCR CI-1; one packet, two plans satisfied).
  Keep the items.py hooks (CLI path is a real door too). Evidence: operator edit in the
  web UI → event in stream with origin/caller identity.
- **E4 (decision packet for Dave, p17): subject taxonomy + envelope.** Start from the
  transcript's family: `intent.*`, `asset.ingested`, `catalog.rebuild.started`,
  `catalog.snapshot.available`, `projection.refresh.required`, plus existing
  `itemdata.{sku}.{field}` and `queue.{queue}.{state}`. Decide: first-class objects
  (tasks? decisions? runs? alerts?), envelope fields (ts, origin, sku, actor,
  payload-pointer — control plane carries pointers, not bulk), retention per stream.
  ~30 min with Dave, then frozen as a reference doc.
- **E5 (S, p20): QUEUE_TRANSITIONS wired in worker_base** (claim/succeed/fail/dead-
  letter/requeue). This makes the ops-digest, stall detection, and Hermes' PM view
  replayable instead of journald-scraping. Evidence: drain a test queue, replay the
  stream, counts match psql.
- **E6 (S, p25): first real consumers.** (a) catalog-refresh listener: on
  `itemdata.*` burst quiet-period → emit `projection.refresh.required` (pairs with
  CI-4's timer as belt+braces); (b) `tgw events tail` CLI for humans; (c) Hermes
  durable consumer (its PM ledger). Evidence: kill a consumer mid-stream, restart,
  replay resumes from durable cursor.

### Track A — annex data plane (PP-ANNEX-001, promoted from FUTURE-IDEAS)

Sequenced pilot-first, exactly per the transcript's "do not overbuild" advice:

- **A0 (decision packet, p20): the Syncthing/annex boundary map.** Per-directory
  ruling on which tool owns which subtree (soundness guard 2). Nothing annexed until
  its subtree is off Syncthing's routes.
- **A1 (S, p20): pilot annex on the document corpus** (masterarchive/history +
  drive-consolidation staging area — NOT ItemData). `git annex init` a dedicated repo,
  import a bounded sample (~5-10GB), set `numcopies=2`, prove: add, metadata tag,
  `whereis`, drop/get round-trip. Evidence: object dropped locally, restored from
  second copy, checksums match.
- **A2 (S, p22): special-remote proof.** rclone special remote against the existing
  tgw-gdrive OAuth (reuse PP-PHOTO-001 Phase A's verified token) on the pilot repo.
  Round-trip a sample to Drive and back. **Quota caution encoded up front**: Drive API
  gets a budget pool + counting like llm_google — today's per-project-quota lesson
  (LLM-Providers-Quotas.md) applies verbatim to Drive; verify the real per-project
  grant before bulk transfers, never assume published numbers.
- **A3 (decision packet, p25): cloud target.** GDrive (familiar, existing token) vs
  GCS/S3-style (cleaner object semantics, real costs) vs rclone-abstracted (flexible,
  one more part). Transcript leans GCS for the end-state; A2's evidence informs this.
  Includes the remote partitioning plan: `gdrive-archive-YYYY` date-partitioned FROM
  DAY ONE (scale-context standing rule), 500k-objects-per-folder ceiling respected.
- **A4 (M, p30, AFTER A1-A3 + Dave sign-off, GATED on a PP-BACKUP-001 baseline
  existing — soundness guard 4): ItemData photos design doc.** Includes the full
  symlink-consumer audit (soundness guard 3). The big
  one — fence-mediated (SKU-addressed API unchanged — the fence design already
  survives storage-tier changes, per FUTURE-IDEAS), intake writes through annex,
  `status=sold → git annex move --to archive-YYYY` lifecycle, numcopies enforced
  before ANY consolidation deletion (Dave approves deletions, always). Design only;
  its build packets get filed after review.
- **A5 (deferred): Go companion tool** (manifests, dedupe grouping, placement policy,
  fetch planning). Only after A1-A4 prove the annex primitives; the transcript is
  explicit: don't build custom storage orchestration before the stock remotes are
  proven. Revisit when PP-DRIVE-INDEX Phase 1 (batch-by-connection scans) generates
  real manifest volume.

### Track R — retrieval deepening (PP-SEARCH-001 continuation)

- **R1 (XS, p25): recoll field mapping for annex metadata + xattrs** (fields: scope,
  domain, entity, document_type, status, confidence — the transcript's taxonomy).
  Prepared now, populated as A-track and H-track emit metadata.
- **R2 (S, p30): `tgw search --full-text`** + web-UI search bar hitting the recoll
  Python API. Evidence: the six "hours→seconds" queries from FUTURE-IDEAS run live.
- **R3 (S, p40): OCR sweep** (tesseract via recoll filter) over ItemData photos —
  serials/labels/barcodes searchable. Thermal-aware: run on cool days or from a1131
  over the ro NFS mount (index write stays on tgw-prod; consider a second a1131-local
  index of the NFS view as the experiment — zero risk to the primary).
- **R4 (ongoing): reindex cadence + drive-index integration** — already governed by
  PP-DRIVE-INDEX; no new packets here, just the standing thermal rule (check between
  scans).

### Track H — Hermes document triage (feeds A+R; gated on #1139 decoupling)

First Hermes mission, unchanged from the transcript: inventory → personal/business/
mixed separation → classification → dedupe candidates → DAVE DECISION SESSION →
index + metadata enrichment. Artifacts not judgments: manifest, per-file
classification + confidence, uncertain-queue, run log. Packets: H1 manifest schema
(ties to survey_drive.sh output), H2 classification taxonomy first-pass, H3 review
workflow + thresholds. File these under PP-HERMES/#1139's planning, not here — listed
for the dependency edge only.

### Integration packets (after E+CI land)

- **I1: Flutter sync service** subscribes (bridge or dart-nats) to
  `catalog.snapshot.available` → refreshes local SQLite projection. Depends: E4
  taxonomy, PP-CATALOG-INCR CI-2/CI-4, PP-PORTABLE-CATALOG plumbing.
- **I2: intent backflow** — portable client stages intents + assets → standard
  pipeline → `intent.accepted/rejected` events close the loop. This is the
  "near-serverless" read/write split going live.
- **I3: MCP surface** — `tgw_events_tail`, `tgw_annex_whereis`, `tgw_search_full`
  tools on the site MCP so every agent gets the plane through the front door.

## Sequencing (what can start immediately, in parallel)

```
NOW (independent):   E1+E2 ──► E3 ──► E4 ──► E5 ──► E6 ──► I1/I2
                     A1 ──► A2 ──► A3 ──► A4 ──► (A5)
                     R1, R2                       R3 (cool day)
Gated:               H1-H3 (on #1139)   CI-2..4 (Dave's sign-off, own plan)
```

E1+E2+E3 and A1 and R1+R2 are all delegable as opusplan packets today — five
sessions of work with no interdependencies. E4 and A3 are short Dave-decision
packets. Nothing here blocks, or is blocked by, PP-PHOTOSYNC's remaining fix track.

## Constraints (settled architecture — all apply)

- tgw-api fence: annex never becomes a side door to ItemData; all writes stay
  SKU-addressed through the API. Read-only NFS precedent holds: ro everywhere
  except the fence.
- Workers thin; `{ok,...}` contract; secrets from secrets_root (NATS creds if any,
  rclone/annex remote configs → secrets_root, chmod 600).
- NATS fire-and-forget guarantee stays HARD: ItemData writes never block on the
  event plane (control/data separation is the point of the design).
- E5/E7 data charter: annex objects are dataset; raw captures keep flowing at the
  fence; nothing here deletes anything without Dave + archive-first.
- "Catalog rebuild is always a job" revision belongs to PP-CATALOG-INCR-001's
  sign-off, not this plan.

## Acceptance criteria (plane-level, each also has per-packet evidence)

- [ ] `tgw health`: nats check green with round-trip probe
- [ ] An operator edit in the web UI is visible in `nats stream view ITEMDATA_MUTATIONS` within 1s, with origin identity
- [ ] A queue job's full lifecycle is replayable from QUEUE_TRANSITIONS after the fact
- [ ] A pilot annex object survives: local drop → restore from 2nd copy → checksum match; and a Drive round-trip
- [ ] `tgw search --full-text "<serial from a photo label>"` returns the SKU (post-R3)
- [ ] Flutter client refreshes its projection on `catalog.snapshot.available` without polling
- [ ] Every layer reachable through the site MCP (I3) — agents need no side channels

## Soundness review (Dave asked: "verify the concept is sound — you know my system")

**Verdict: sound.** The strongest evidence is that every layer maps onto something
this system has already proven, and the only genuinely new component (NATS) is the
lightest one in the stack. Specifics:

- The control/data-plane principle is already battle-tested HERE — clipd vs the old
  X11 clipboard hangs, lan-mouse vs Input Leap. This isn't imported theory.
- The fence being SKU-addressed (never path-addressed) means annex can change the
  storage substrate without any consumer noticing — that property was designed in
  two years before this plan needed it.
- The "intents through the state-driven pipeline" backflow is literally the existing
  queue/state machine (Dave's own closing note in the transcript: built on the
  PostgreSQL state machine "with very little change"). The concept doesn't fight the
  system; it's the system generalized.
- recoll at 9.1G is already paying rent (real recoveries). git-annex is the mature
  outlier-tool (15+ years, content-addressed, designed for exactly this multi-drive/
  partial-copy problem). NATS is a single Go binary — fits the hardware reality.

**Four guards the research sources DON'T flag (this is the you-know-my-system part):**

1. **Two-ledgers risk (the big one).** Some research threads drift toward "keep state
   machine context in JetStream KV" / Temporal-style durable execution. REJECT that:
   PostgreSQL `state_machine` is and stays the ONLY transactional work ledger.
   JetStream is notification, audit, replay, and projection-sync — never a competing
   source of truth, never something a worker must consult to know its state. Encode
   as an invariant (proposed E9) the day E1 lands. The fire-and-forget guarantee in
   nats_client.py already has the right shape; keep it hard.
2. **Syncthing/annex collision.** git-annex turns files into symlinks into a
   read-only object store. Syncthing currently touches parts of the tree
   (tgw-itemdata-sync, vault sync) and handles symlinks/read-only dirs badly. Annex
   and Syncthing must NEVER co-manage the same subtree — the migration boundary has
   to be explicit per-directory. This gets its own decision packet (A0) before A4.
3. **Symlink-consumer audit.** Photo paths are opened by PIL (see today's
   truncated-image dead-letters), served by tgw-http, read by ebay_upload/
   thumbnail_gen/gdrive_sync, exported over NFS to a1131. Symlinks resolve
   transparently for open(), but anything doing lstat/copy/rsync-without--L breaks
   quietly. A4 must include a full consumer audit; alternatively adopt unlocked
   (annex.thin-adjacent) mode for ItemData — the transcript already leans this way
   for clients; the server needs the same analysis.
4. **Backup ordering.** Risk #1 in the handoff is still "no backup running."
   Restructuring photo storage while there is no backup is compounding risk — A4
   (ItemData migration design) is GATED on a PP-BACKUP-001 baseline existing.
   Conversely, annex materially helps DR (numcopies, location tracking, cheap
   verification) — it's the endgame for PP-BACKUP, just not the first step. JetStream
   file storage joins the backup target list the day it exists (streams are dataset,
   charter E5), and the a1131-mirror question (open Q3) doubles as event-log DR.

**Explicitly rejected from the research corpus** (good ideas, wrong system or wrong
plan): Temporal/durable-execution frameworks (duplicates the Postgres ledger);
Redpanda/Kafka-class buses (oversized); the microVM/nspawn BSK isolation platform
(real concept, SEPARATE future PP — do not let it ride into this one); JetStream KV
as state store (guard 1).

## Open questions for Dave (the decision packets)

1. **E4:** which objects are first-class events — tasks, decisions, runs, commits,
   artifacts, alerts? (transcript left this as THE key open question)
2. **A3:** cloud backend — GDrive now / GCS end-state / rclone-abstracted?
3. **NATS placement:** tgw-prod only, or clustered to a1131 later (a1131 as durable
   second copy of the event log = cheap DR for the fabric)?
4. **Umbrella naming:** PP-KNOWLEDGE-001 as coordinating PP with the tracks above as
   its packet families — or keep the four PPs fully separate? (recommend umbrella:
   one status surface, `tgw plan status --pp PP-KNOWLEDGE-001`)
5. **Retention:** JetStream stream limits (age/size) per stream — mutations forever
   (it's dataset per the charter — recommend file-backed, no age limit, disk cap +
   archived exports) vs transitions (recommend 90d)?
6. Promote PP-ANNEX-001 + confirm PP-SEARCH-001 continuation out of FUTURE-IDEAS now?
   (this doc assumes yes; FUTURE-IDEAS entries then get pointers here)

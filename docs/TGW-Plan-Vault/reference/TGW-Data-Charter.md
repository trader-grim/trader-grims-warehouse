# TGW Data Charter

**Status:** Established 2026-07-02 (session 42), from Dave's day-1 requirement that
implementations repeatedly failed to honor. Read this BEFORE working on any pipeline,
worker, or eBay integration. Prime Directive 1 in CLAUDE.md points here.

## The axiom

> **eBay is a rented window. The local dataset IS the business.**

TGW is not a set of scripts that talk to eBay. It is a dataset — acquired from eBay,
derived by AI, accumulated through operations — with scripts around it. Every feature
exists to grow, refine, or act on the dataset. Reverse the usual engineering instinct:
the API is not the source of truth we can re-fetch; it is a metered, revocable,
lossy window we pull assets through, once, and keep forever.

Implications every implementer must apply without being told:

1. Anything received from outside (eBay, AI models, lookups, scans) is an **asset the
   moment it arrives**. Persisting it is part of receiving it, not an optional later step.
2. Raw is permanent; derived is recomputable. Store the raw thing even when only a
   fragment is needed today ([[project-vision-data-architecture]]).
3. There is no "just fetch it again." Quota, delisting, API sunset, or account issues
   can close the window at any time.
4. Deleting or overwriting without archiving is a bug even when nobody said so
   (invariant E5).

## The inbound root (Dave's directive, 2026-07-02)

ALL inbound data lands under **`/opt/TGW/incoming/`** — its own top-level structure,
outside everything else, group-only permissions (2770 tgw:tgw + default ACLs, never
world-readable): `newitems/` (item intake), `ebay/` (raw eBay responses, E7),
`lookups/` (reserved for raw lookup-API responses). New inbound sources get a
subdirectory here FIRST, before any processing code is written. See
`/opt/TGW/incoming/README.md`.

## The assets

| Asset | What it is | Where | Growth/enforcement |
|-------|-----------|-------|--------------------|
| ItemData | Canonical per-SKU business records + photos | `/opt/TGW/data/ItemData/<SKU>/` | tgw-api fence; all writes through it |
| **eBayCapture** | Every raw eBay response (REST/Trading/EPS, incl. errors) | `/opt/TGW/incoming/ebay/YYYY-MM-DD.jsonl.gz` | Invariant **E7** — captured at the client choke point, no worker can skip it |
| eBay mirror fields | Structured eBay IDs/URLs/status written back into item JSON | ItemData (`ebay_*` blocks) | PP-EBAY-MIRROR-001 / PP-DATA-OWN-001 |
| ItemArchive | Pre-deletion/pre-manipulation snapshots | `/media/db/masterarchive/history/ItemArchive/<sku>.zip` | Invariant E5 (**still ❌ unenforced in code — open gap**) |
| AI derivations | ai_identify raw scans, draft text, alt-text, aspect fills | ItemData + capture files | Raw model output preserved alongside the accepted value |
| Price/revision history | `price_history`, `revision_history`, staged payloads | ItemData | Append-only; never rewritten |
| Work ledger | queue_jobs + history, todos | PostgreSQL `state_machine` | **Not re-derivable** — backup is PP-BACKUP-001 (still not running) |
| Taxonomy assets | Category tree, aspects shards + raw bulk gz, condition policies | `/opt/TGW/data/ItemCatalog/` | Permanent until manual refresh; never TTL'd |
| Catalogs/indexes | Search/SQLite/thumbnails/fingerprints | ItemCatalog | Derived — the ONLY tier that is legitimately disposable |

## Rules for new work

- **New external call?** It must go through a counted choke point (quota) AND its
  response must land in a capture path. If you are adding a call site that does
  neither, you are adding it in the wrong place.
- **New derived value?** Persist the raw input it was derived from, with enough
  metadata to recompute (model, prompt version, source photos).
- **New feature spec?** State what the feature ADDS to the dataset. A feature that
  touches external data and grows nothing is a red flag — say so to Dave.
- **Retention is forever by default.** Culling happens only in the archive tier, only
  by explicit operator decision.

## Observability (so accumulation is a fact, not an assumption)

The ops digest should show dataset growth (capture bytes/day, items with raw
snapshots, archive coverage) — packet open as todo. A day where the pipeline ran but
the dataset didn't grow is a signal something is being discarded again.

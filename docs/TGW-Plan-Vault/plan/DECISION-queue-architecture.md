---
title: Decision — Queue Architecture
status: settled
date: 2026-05-31
session: Opus planning
---

# Decision: pure state-machine queue (Option B)

## The decision
PostgreSQL is the single source of truth for all work. There is no parallel
filesystem `.job.json` queue. systemd keeps worker *processes* alive;
PostgreSQL decides what *work* gets done. The old launcher/filesystem queue
is retired.

## The two questions this separates
The confusion in earlier sessions came from collapsing two distinct questions:

1. **Who keeps a process alive?** — systemd's job (or a supervisor).
2. **Who decides what work is done?** — PostgreSQL, the work ledger.

In the simplest queue these collapse into one (start script → it works → exits),
which is why the original filesystem queue "just worked." The state-machine
redesign deliberately split them. The reconnection: a worker process is a dumb,
identical hand that raises itself and asks the foreman (Postgres) for work.
Intelligence lives in the ledger, not the worker.

## Why B over A
Option A ("launcher supervises, workers call the state machine") was attractive
only for being non-disruptive. But the launcher today spawns workers that do no
useful work, and the filesystem queue it serves is a superseded design, not a
beloved working system. With nothing worth protecting, A's only advantage
disappears. B is the clean long-term platform.

## What this buys
- One mental model, one source of truth — no "which system is authoritative" ambiguity
- Leasing (SKIP LOCKED), retries, dead-lettering, lease recovery — all already smoke-tested
- Idle workers cost nothing (no busy filesystem polling of empty queues)
- Trivial LTSP scale-out later — a remote node is just more hands at the same foreman
- Workers stay thin — a shared `QueueWorker` base owns all Postgres interaction

## The trade we accept
PostgreSQL becomes load-bearing. If it is down, no work flows. The old
filesystem path degraded softly (files piled in a folder). We give up soft
failure for real work-tracking. Mitigations become first-class:
- systemd startup ordering: Postgres up before workers
- Postgres health in `tgw health`
- Postgres in the backup plan

## What stays the same
- Workers still ask tgw-api for everything data-related (the fence holds)
- Output contract unchanged
- Bulk-first processing unchanged

---
title: Session — Opus Planning 2026-05-31
model: Opus 4.8 (planner)
---

# Opus planning session — 2026-05-31

## What we decided
- **Queue architecture: pure state-machine (Option B).** PostgreSQL is the one
  work ledger; systemd keeps worker processes alive; the old launcher/filesystem
  queue retires. See `DECISION-queue-architecture.md`.
- **First worker: no-op echo**, as the reference template proving the loop with
  zero risk. **First real worker: PM-intake** (no external dependency, delivers
  the markmap hub, no business blast radius). Camera-intake comes third.
- **Docs format: Obsidian vault of Markdown.** Markmap plugin renders the plan
  as a mind-map; the same files paste into any model as plain text.

## What we built
- `plan/TGW-Master-Plan.md` — the living markmap spec (the communication hub)
- `plan/DECISION-queue-architecture.md` — why pure-B, so it is not relitigated
- `plan/TASKS-phase1-queue.md` — five execution tasks for Sonnet/Haiku
- `reference/worker_base.py` — starter `QueueWorker` base class
- `reference/echo.py` — starter echo worker

## The mental model we clarified
Two questions had been collapsed into one. They are separate:
1. Who keeps a *process* alive? → systemd.
2. Who decides what *work* is done? → PostgreSQL.
A worker process is a dumb, identical hand that asks the foreman (Postgres) for
work. Intelligence lives in the ledger. Scaling later = more hands at the foreman.

## Next session should
- Execute Phase 1 tasks 1.1–1.5 (start with the executor models)
- Confirm the liveness mechanism choice (systemd templated units recommended)
- Then plan Phase 2 (PM-intake) in detail once the queue foundation is proven

## Carry-forward constraints
- 32GB RAM, CPU-only — serialize AI jobs, one Ollama model at a time
- eBay token cron job stays until queue-based refresh replaces it
- tgw.source still runs the business — nothing cuts over until verified
- historical-tgw-catalog.json is the recovery source for data cleanup

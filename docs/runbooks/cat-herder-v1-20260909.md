# cat_herder — autonomous coding loop (tgw-lib)

LEAF-11-1 residual / Todo 2003. Stands the continual-harness orchestrator up as
a standing daemon so coding Todos dispatch without a session waiting on each
one.

## What it is

`tgw coding start <todo>` is **synchronous** — the caller blocks for the whole
implement → test → review → land loop. `cat_herder` runs that loop unattended:

```
tgw coding enqueue <todo>   →   queue_jobs (queue_name='coding')   →   cat_herder
                                                                        │
                                        harness_cli.dispatch (one at a time)
                                                                        │
                                    squash + fast-forward main  /  parked  /  retry
```

- **Substrate:** the shared `queue_jobs` table (`queue.durable-claims@1` / W02),
  same database as `harness_ledger`. `src/tgw/development/harness_queue.py` is a
  thin coding-specific adapter over it — it does **not** import
  `tgw.queue.worker_base` (that carries the eCommerce NATS/quota/eBay
  machinery the apparatus-free coding workflow avoids).
- **One job at a time.** A coding dispatch holds a worktree and runs for
  minutes; deliberate parallelism is a later, explicit choice.
- **Singleton** via a PostgreSQL advisory lock — a second herder exits at once.
- **Resumable.** The queue lease + `harness_ledger` are the only durable state.
  Kill the herder mid-job; its lease expires, `recover_expired_jobs()` requeues
  the job, the next herder resumes from the ledger cursor.

## Canonical files

| Purpose | Path |
| --- | --- |
| Queue adapter | `src/tgw/development/harness_queue.py` |
| Daemon | `src/tgw/development/cat_herder.py` |
| CLI (`enqueue` / `queue`) | `src/tgw/coding_cli.py` |
| systemd unit | `config/environment/systemd/tgw-cat-herder.service` |

## Operator: install

```bash
sudo install -m 0644 -o root -g root \
  /opt/TGW/tgw-lib/src/trader-grims-warehouse/config/environment/systemd/tgw-cat-herder.service \
  /etc/systemd/system/tgw-cat-herder.service
sudo systemctl daemon-reload
sudo systemctl enable --now tgw-cat-herder.service
journalctl -u tgw-cat-herder -f
```

The unit runs as **tgw-harness** (the sanctioned ref publisher — it
self-publishes the fast-forward and `sudo`s to `tgw-coder` for the confined
implement/review sessions, per `/etc/sudoers.d/tgw-harness`). It is deliberately
**not** `NoNewPrivileges` sandboxed — that sudo hop is the designed path.

### First-run canary (proves the autonomous land)

```bash
# a throwaway Todo whose body the stub executor can satisfy, then:
sudo -n -u tgw-harness /usr/local/bin/tgw coding enqueue <todo> --executor stub
journalctl -u tgw-cat-herder -f          # expect: job <id>: landed
git -C /opt/TGW/tgw-lib/src/trader-grims-warehouse log --oneline -1
```

## Day to day

```bash
tgw coding enqueue 1954 --message "11.6 per-job change handling"
tgw coding enqueue 1954 --executor claude,codex --max-rounds 4
tgw coding queue                     # state counts + recent jobs
tgw coding status todo-1954          # the ledger view (attempts, findings)
tgw coding log todo-1954             # full append-only history
```

`enqueue` is **idempotent per Todo** — a second enqueue while a job is live is a
no-op that returns the existing job id.

## Job outcomes

| queue state | meaning | next |
| --- | --- | --- |
| `succeeded` | landed on main (or already-satisfied / already-done) | — |
| `cancelled` | **parked** — orchestrator `blocked` (budget exhausted with a supervisor hand-off), unusable payload | a person fixes the blocker and `enqueue`s again |
| `retry_wait` | transient failure (dispatch raised, `OrchestratorBusy`, `rebind_required`) | auto-retried after `not_before` |
| `dead_letter` | transient failure past `max_attempts` (default 3) | `tgw dead-letter` tooling; re-enqueue after a fix |

## Stopping

`systemctl stop tgw-cat-herder` sends SIGTERM: the herder finishes the job in
flight, then exits (`TimeoutStopSec=900`). A job that outlives that is SIGKILLed;
its lease expires and the next herder requeues it — no work lost.

## Known follow-ups

- Parallel herders / a work-stealing pool — out of scope here (one at a time).
- Enqueue-from-plan: today a person or `tgw coding enqueue` seeds the queue;
  the plan-solver emitting dispatchable execution cards into it is 11.4/11.8
  territory.

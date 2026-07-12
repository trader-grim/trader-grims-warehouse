# PLANNING — todo #1261: itemdata_scrub.py ad-hoc queue

Not closed — deliberately re-scoped as a planning item per Dave's call,
rather than forced into a partial fix.

## Re-examined 2026-07-10

Pre-flight check before attempting any fix: confirmed `itemdata_scrub.py`
is **not currently scheduled anywhere** — no cron entry, no systemd timer,
no reference in `~/tgw-flake` at all. So the "no visibility in tgw
queue-status" gap has zero real-world impact today; this isn't a live
production concern, it's a structural gap that matters only if/when the
tool is actually put into regular use.

## Why this wasn't just fixed now

The real fix is a genuine migration: new systemd unit (or decide it stays
manual), converting the dequeue model from "file exists in cwd" to
postgres-backed `state_machine` rows, deciding how `ScrubRules`/`--config`
get supplied in that model. That's design work, not a quick conversion —
forcing a partial fix (e.g. just logging queue depth somewhere `tgw health`
can see, without addressing the file-vs-postgres split) would leave the
actual problem half-solved while making the todo look closed.

## What shipped

Full writeup added to the master plan (audit#1143 `workers/` subsystem
section) so the next planning session has the context ready — current
state, why it's deferred, and what a real scoping pass needs to decide.
Todo #1261 kept open, re-titled from "batch this into a cohesion fix" to
"needs a scoping pass."

## Live evidence

- `grep -rn itemdata_scrub ~/tgw-flake` → no matches (not scheduled).
- `crontab -l -u tgw` / `systemctl list-timers --all` → no matches.
- `tgw plan check` — clean.

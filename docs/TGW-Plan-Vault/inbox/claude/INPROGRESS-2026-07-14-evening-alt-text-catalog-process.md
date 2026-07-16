# Session 2026-07-14 (afternoon/evening) — alt_text worker + catalog investigation

## What was done, in order

**PP-DEADLETTER-001 batch (earlier this session, already merged):** 9 packets
(#1393-1404) triaged, dispatched as an 8-wide concurrent tgw-coder experiment,
merged clean. Dave's API session budget confirmed at 3-4 concurrent max (see
memory `feedback-larger-batch-experiment-confirmed`). Requeue script applied
(45 dead-letters), 4 stale workers found running pre-merge code and restarted.

**#1108 — alt_text worker had no systemd unit:** Dave asked whether
`tgw202605051936445` was stuck; it was, along with 4 siblings, oldest since
2026-06-26 — `ai_identify` enqueues into `alt_text` queue but no
`tgw-worker@alt_text.service` unit was ever declared in the Nix flake. Wrote
packet, dispatched tgw-coder (flake edits + eval only, no unattended switch
per permission classifier). Dave then explicitly authorized the switch
("yes, rebuild the ... nix flake") — I ran `nixos-rebuild switch` myself,
worker came up clean.

**Found immediately: alt_text crashed on every job.** `_history_sku_dir()`
resolves through `/opt/TGW/data/history` → symlink → `/media/tgw/MasterArchive/history`,
and that cold-archive drive (`sdf` per DRIVE-REGISTRY, later corrected —
Dave provided it live at `/dev/sdg5`) wasn't mounted. `mkdir(exist_ok=True)`
doesn't suppress `FileExistsError` on a broken symlink. Stopped the worker
before more quota got burned (each retry made a real LLM call before crashing).

**#1407 — fixed the crash properly:** packet + tgw-coder built
`_history_root_reachable()` pre-flight check; on unreachable target, skips
the archive copy and persists a durable C11 finding
(`pipeline_error.code = 'archive_target_unmounted'`) instead of crashing.
Also caught and fixed a real bonus bug: `fence_patch_item()` was being
clobbered by `cmd_alt_text`'s own direct `atomic_write_json` — fixed by
reordering. Filed #1408 for the identical bug in the Gemini Batch path
(not fixed, out of scope).

**Drive mounted, ownership fixed:** Dave provided `/dev/sdg5` = MasterArchive.
Mounted at `/media/tgw/MasterArchive` (matches the existing symlink target,
not added to fstab — Dave doesn't want it permanently spun up). Found a
SECOND real bug during Stage B testing: `history/ItemData` was owned by an
orphaned uid 1001:1001 (no matching account on this host), mode 775 — `tgw`
(uid 900) could write inside existing SKU folders but not `mkdir` new ones.
Dave authorized `chown -R tgw:tgw` on the whole drive (1.4T, ~2.6M files) —
done, confirmed clean. Requeued all 4 dead-lettered jobs, worker drained
them successfully, confirmed healthy/idle.

**Both #1108 and #1407 are verified live and working but their branches
are NOT YET MERGED** (`todo/1108-alt-text-worker-unit`,
`todo/1407-alt-text-archive-mount-guard`) — Dave didn't give an explicit
merge instruction for these two the way he did for the earlier
PP-DEADLETTER-001 batch ("readt, merge"). **Next session: ask Dave whether
to merge these, or check if he already has.**

**alt_text coverage check (Dave asked):** of 11,021 currently-ACTIVE eBay
listings, only 189 have `alt_text` set — 10,832-item backlog. Not a bug,
just near-zero prior coverage since the worker never existed until today.
Flagged the Gemini Batch API path (todo #144) as the right tool for the
backlog vs. burning live quota — Dave hasn't decided on pacing yet.

**Webui pipeline page mislabel found + filed (not fixed):** `/form/pipeline`'s
"Done today" column is actually a lifetime cumulative count (`queue_status()`
has no date filter, `http_server.py:1664-1688`). New PP-QUEUESTATS-001, todo
#1409 — proper date-scoped fix + Dave's own follow-on idea (use per-queue
daily stats as an anomaly/surge-detection baseline once real numbers exist).
Dave: "I can live with it for a bit" — not urgent.

**SKU-migration / catalog false alarm (my own mistake, corrected same
session):** investigating "8257 items with no ItemData folder" turned into
a full false alarm — I was reading a STALE ORPHANED file
(`searchcatalog.json`, no hyphen) instead of the real live-config file
(`search-catalog.json`, hyphenated). Confirmed via `sku_history` DB table +
direct eBay-item-ID greps that every item is intact; SKU migration
(PP-ADD-005) is 99.7% complete (149 stragglers left, todo #1411).
Todo #1410 corrected in place to record the false-alarm root cause.
Filed #1412 (sku_history audit-trail gap — only 3305 of ~34k+ documented
renames are logged there). Moved the two genuinely-orphaned catalog files
(confirmed via grep, NOT `searchcatalog.csv` or the `historical-*.json`
files — those are still live-referenced) to
`/opt/TGW/data/history/ItemCatalog/` with clear
`-last-old-sku-version-2026-05-27` names. Todo #1413 done.

**Process discussion, captured as a decision (todo #1414):** Dave observed
the coder role (packets/tgw-coder) is well-defined and transfers to fresh
agents cleanly, but planner/reviewer/stitcher were all run by "me" (with
session-accumulated judgment, not a written process). Walked the full
7-stage pipeline; landed on: master-plan authoring and plan-review are
already working practices (no gap), packet breakdown (planner) is the real
confirmed gap, runners/merge/implement-test already work, code review has
`/code-review`. Full writeup in `pp/PP-HERMES-EA-001.md`'s "Planner/coder/
stitcher/reviewer process maturity" section. **Next session Dave wants to
work on this: write the packet-breakdown rubric as its own reference doc.**

## Next step (in priority order)
1. Confirm whether #1108/#1407 branches got merged (check first — may
   already be handled).
2. Dave said he's going to "use it for a while and make a new list" —
   session may open with that new list rather than continuing this thread.
3. When a code-running session comes up: write the packet-breakdown
   (planner) rubric per todo #1414 / PP-HERMES-EA-001.
4. Lower priority open threads from this session: #1405 (real fix for
   ebay_draft non-JSON dead-letters), #1406 (entity_id default, low pri),
   #1409 (pipeline stats date-scoping), #1411/#1412 (SKU migration tail +
   audit gap), #1408 (alt_text batch-path crash guard).

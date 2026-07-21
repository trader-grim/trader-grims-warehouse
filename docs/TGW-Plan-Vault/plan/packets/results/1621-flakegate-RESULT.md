# Result: 1621 flakegate
Status: done
Todo: #1621   PP: PP-FLAKEGATE-001

Files touched:
- `src/tgw/flake_gate.py` (new) — domain module: `request_push`, `request_switch`,
  `queue_table`, `show_job`, `mark_executed`, `audit`
- `src/tgw/queue/state_machine.py` — added `FLAKE_MUTATION_QUEUE` constant,
  `list_flake_mutation_jobs`, `get_flake_mutation_job`,
  `mark_flake_mutation_executed`, `list_executed_flake_push_shas`
- `src/tgw/api.py` — new `tgw flake` subcommand group (`request-push`,
  `request-switch`, `queue`, `show`, `mark-executed`, `audit`) + help-group entry
- `tests/test_flake_gate.py` (new) — 9 offline/mocked tests
- `.claude/agents/nix-flake-maintainer.md` — Step 2/5 rewritten to call
  `tgw flake request-push`/`request-switch` instead of executing `git push`/
  `nixos-rebuild switch`; batching guidance updated; new Constraints entries
  prohibiting direct push/switch and self-calling `mark-executed`
- `docs/TGW-Plan-Vault/reference/invariants.md` — new invariant E17
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1621-flake-push-gate.md` (new,
  breadcrumb)

## Live evidence (Prime Directive 4)

All against the real `state_machine` Postgres DB, `sudo -u tgw`, worktree
`PYTHONPATH`/`LD_LIBRARY_PATH` confirmed pointing at this branch's code
(`tgw.flake_gate.__file__` / `tgw.api.__file__` both resolved under
`/opt/TGW/var/worktrees/1621-flakegate/src/...` before testing).

**request-push / request-switch — real rows landed:**
```
$ tgw flake request-push --repo ~/tgw-flake --host tgw-prod --commit deadbeefTEST1621 --summary "..."
4a9b64bf-c947-4852-9b8f-35a88a714a47
$ tgw flake request-switch --host tgw-prod --commit deadbeefTEST1621 --summary "..."
80a59d66-275b-49f5-bd28-72c298cfda2d

$ psql -U tgw state_machine -c "SELECT job_id, queue_name, entity_type, entity_id, operation, dedupe_key, state, payload_json FROM queue_jobs WHERE queue_name='flake_mutation' ORDER BY created_at;"
                job_id                |   queue_name   | entity_type  |    entity_id     | operation |                   dedupe_key                    | state  | payload_json
--------------------------------------+----------------+--------------+------------------+-----------+-------------------------------------------------+--------+---------------------------------------------------------------------------------
 4a9b64bf-...-35a88a714a47            | flake_mutation | flake_commit | deadbeefTEST1621 | push      | flake_mutation:push:tgw-prod:deadbeefTEST1621   | queued | {"host":"tgw-prod","kind":"push","repo":"/home/db/tgw-flake","summary":"..."}
 80a59d66-...-72c298cfda2d            | flake_mutation | flake_commit | deadbeefTEST1621 | switch    | flake_mutation:switch:tgw-prod:deadbeefTEST1621 | queued | {"host":"tgw-prod","kind":"switch","summary":"..."}
(2 rows)
```

**queue / show:**
```
$ tgw flake queue
JOB_ID    KIND   HOST     COMMIT       REQUESTED_AT   SUMMARY
80a59d66  switch tgw-prod deadbeefTEST 2026-07-21 ... PP-FLAKEGATE-001 live acceptance test row
4a9b64bf  push   tgw-prod deadbeefTEST 2026-07-21 ... PP-FLAKEGATE-001 live acceptance test row

$ tgw flake show 4a9b64bf-...
{ "job_id": ..., "state": "queued", "dedupe_key": "flake_mutation:push:tgw-prod:deadbeefTEST1621", ... }
```

**mark-executed — real state transition + history capture:**
```
$ tgw flake mark-executed 4a9b64bf-... --by "Dave-test"
Marked executed: 4a9b64bf-... (state=succeeded)

$ psql -U tgw state_machine -c "SELECT job_id, old_state, new_state, transition, worker_id FROM queue_job_history WHERE job_id='4a9b64bf-...';"
 job_id  | old_state | new_state | transition           | worker_id
---------+-----------+-----------+-----------------------+-----------
 4a9b64bf| queued    | succeeded | flake_mark_executed   | Dave-test
(1 row)

$ tgw flake mark-executed 4a9b64bf-...   # second call, same job
Error: mark_flake_mutation_executed: no queued flake_mutation job found for job_id=... (already executed, cancelled, or does not exist)
exit=1
```

**audit — flags unmatched, passes matched, ignores pre-rollout — end to end
against a real disposable throwaway git repo** (NOT `~/tgw-flake` itself — see
Deviations below, todo #1623):
```
$ tgw flake audit --repo /opt/TGW/var/tmp/flakegate-audit-test/seed
Checked 2 commits on origin/master (.../seed).
FINDINGS: 1 commit(s) with no matching executed flake_mutation push record:
  8917de539b094846a8a53ec37fcb224d8ede438e  2026-07-22T10:00:00-07:00
  # (commit2, dated 2026-01-01, correctly excluded as pre-rollout)

# after tgw flake request-push + mark-executed for that same sha:
$ tgw flake audit --repo /opt/TGW/var/tmp/flakegate-audit-test/seed
Checked 2 commits on origin/master (.../seed).
No findings — every post-rollout commit has a matching executed push record.
```

**Cleanup performed** (all test rows removed, confirmed live):
```
$ psql -U tgw state_machine -c "DELETE FROM queue_jobs WHERE queue_name='flake_mutation' AND entity_id IN ('deadbeefTEST1621','8917de53...');"
DELETE 3
$ psql -U tgw state_machine -c "SELECT count(*) FROM queue_jobs WHERE queue_name='flake_mutation';"
 count
-------
     0
```
(queue_job_history rows for these test jobs cascade-deleted via the existing
`ON DELETE CASCADE` FK.) Scratch throwaway git repo at
`/opt/TGW/var/tmp/flakegate-audit-test/` removed.

**Tests:** `tests/test_flake_gate.py` (9 tests, offline/mocked) all pass.
Full suite: `pytest tests/ -q` → 2751 passed, 4 skipped, 2 pre-existing
failures unrelated to this packet (see Deviations).

## Deviations from spec

1. **Design-doc vs. dispatched-packet discrepancy, flagged not silently
   resolved (Prime Directive 3):** `TGW-Master-Plan.md`'s PP-FLAKEGATE-001
   section (written before this packet was dispatched) describes the closing
   command as `tgw flake push <id>` / `tgw flake switch <id>` — i.e. the tgw
   CLI itself executing the real push/switch on human approval. The packet
   actually dispatched to me for todo #1621 is explicit and more detailed:
   "Do NOT wire up any part of this to actually execute `git push` or
   `nixos-rebuild switch` from within the tgw CLI... `mark-executed`... does
   not execute anything, it just records that a human did." I followed the
   dispatched packet (built `mark-executed` as a pure record-only command,
   never executing anything) as the authoritative, more-recent, more-detailed
   spec — but this is a real divergence from the master-plan write-up that
   should be reconciled there (the master plan text is now stale relative to
   what got built and should be updated to match, or Dave should say which
   was actually intended and I'll adjust).
2. **`tgw flake audit`'s live acceptance test ran against a disposable
   throwaway git repo, not the real `~/tgw-flake` checkout** — see todo #1623:
   the `tgw` OS user (Postgres peer auth) cannot read `/home/db` (mode 700)
   where `~/tgw-flake` lives, and the `db` OS user (owner of `~/tgw-flake`)
   fails Postgres peer auth as `tgw`. Neither side of this split is something
   I resolved unilaterally (loosening `/home/db` permissions or adding a new
   DB auth path is a real infra decision, out of this packet's scope) — flagged
   as invariant E17's "Known gap," not silently worked around. The mechanism
   itself (git log parsing + DB comparison + rollout-date filtering) is fully
   live-verified against a real git repo + the real Postgres DB; only the
   specific target path (`~/tgw-flake`) is untested live, due to this
   permission split.
3. Everything else (table/column choice — reused `queue_jobs`/`queued->
   succeeded` per the packet's own guidance rather than inventing a new
   status field; CLI subcommand shape; dedupe_key format
   `flake_mutation:{push|switch}:{host}:{sha}`) matches the packet as given,
   no other deviations.

## Out-of-scope findings filed
- #1622 (pp_ref=PP-ADD-005): `test_invariant_c12_field_set_accessors.py`
  allowlist stale (line-number drift in `ai_identify.py`) — pre-existing
  failure, confirmed present on the base branch before my changes too (via
  `git stash`), unrelated to this packet.
- #1623 (pp_ref=PP-FLAKEGATE-001): `tgw flake audit --repo ~/tgw-flake`
  OS-user permission split (tgw vs. db) blocks live use on tgw-prod — see
  Deviations #2 above and invariant E17's "Known gap" section.

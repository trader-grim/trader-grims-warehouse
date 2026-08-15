# TGW day-two platform operations — v2 (2026-08-14)

**Owner:** shared

**Last verified:** 2026-08-14 against `tgw-prod` and `tgw-lib`

**Applies to:** the governed-platform installation at and after
`plan-fb9-afb8e703-20260814`

**Last drill:** 2026-08-14 authenticated HTTP verification, service/journal checks,
queue/item inspection, immutable-release verification, and maintenance SSH

## Operating model

`tgw-prod` hosts the production data plane and operator surfaces. `tgw-lib` is a
separate coding/execution host. The standalone Plan repository is
`/opt/TGW/library/plans`; the production flake checkout is
`/home/db/tgw-flake` on `tgw-prod`; the selected application source is
`/opt/TGW/current`; the executing Python package is in
`/opt/TGW/.venvironments/tgw`.

Do not substitute one path for another. In particular:

- `/opt/TGW/current` proves selected immutable source;
- the venv proves code loaded by HTTP/workers;
- `/run/current-system` proves the live NixOS generation;
- `/run/booted-system` proves what the machine booted;
- ItemData JSON is canonical item state;
- PostgreSQL is canonical queue/workflow/authority/effect history.

## Safe maintenance access

The least-disruptive maintenance path from `tgw-lib` is the dedicated local `db`
identity and the pinned host key. Verify access with a read-only command first:

```bash
sudo -n -u db -H ssh -F /dev/null \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o IdentityAgent=none \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/home/db/.ssh/known_hosts \
  -i /home/db/.ssh/id_ed25519_codex_maintenance_20260814 \
  db@192.168.60.100 'hostname; id; systemctl is-system-running'
```

Do not print the private key, copy it into a repository, relax host-key checking,
enable agent/default-key fallback, or replace this with an ambient `ssh tgw-prod`
command in an evidence-bearing procedure.

The A3 `codex` key/wrapper is a separately constrained observation/bootstrap path.
It is not the routine maintenance shell and must not be repurposed as one.

## Five-minute platform check

Run on `tgw-prod`:

```bash
hostname
readlink -f /run/current-system
readlink -f /run/booted-system
readlink -f /opt/TGW/current
systemctl --failed --no-pager
systemctl list-units 'tgw*' --type=service --all --no-pager
systemctl list-timers 'tgw*' --all --no-pager
sudo -u tgw /opt/TGW/.venvironments/tgw/bin/tgw health
```

Then inspect queue state:

```bash
sudo -u tgw psql state_machine -c "
  SELECT queue_name, state, count(*)
  FROM queue_jobs
  GROUP BY queue_name, state
  ORDER BY queue_name, state;"
```

Expected background units at the last verification:

- HTTP: `tgw-http.service`;
- MCP SSE: `tgw-mcp-sse.service`;
- sync/host protection: ItemData sync, cloud sync when scheduled, thermal watchdog;
- workers: `ai_identify`, `alt_text`, `bundle_intake`, `ebay_draft`,
  `ebay_legacy_sync`, `ebay_onboard_legacy_stage`, `ebay_price`, `ebay_publish`,
  `ebay_repush`, `ebay_stage`, `ebay_sync`, `ebay_upload`, `echo`, `multi_intake`,
  `normalize_condition`, `plan_render`, `token_refresh`, `workflow_evaluate`.

Timer-triggered backup/catalog/snapshot services are normally inactive between runs.
Judge their timer result and last journal, not whether the oneshot service is currently
running.

## Current identity check

```bash
readlink -f /opt/TGW/current
sudo -n /opt/tgw-installer/current/bin/tgw-release-install \
  --root /opt/TGW verify "$(basename "$(readlink -f /opt/TGW/current)")"

systemctl show tgw-http.service \
  -p ExecStart -p MainPID -p ExecMainStartTimestamp \
  -p ActiveState -p SubState -p FragmentPath

systemctl show tgw-worker@workflow_evaluate.service \
  -p ExecStart -p MainPID -p ExecMainStartTimestamp \
  -p ActiveState -p SubState -p FragmentPath
```

At last verification, the HTTP service used the Nix-store `tgw-launch` wrapper and
`/opt/TGW/.venvironments/tgw/bin/tgw`; the workflow worker used the same launcher
with `/opt/TGW/.venvironments/tgw/bin/tgw-workflow-evaluate-worker`.

An unchanged unit start time after selecting/installing a new source means the process
may still have old imports. Restart the exact affected units and verify again.

## HTTP and operator-console checks

The ordinary web endpoint is `http://tgw-prod:7373`. Browser form routes require a
login session; a Bearer token alone is not an equivalent test.

Read-only checks:

```text
GET /form/home
GET /form/items
GET /form/runs
GET /api/items/<SKU>/workflow       (authenticated)
GET /api/coding/access-status       (authenticated)
```

For a changed item page, verify the exact rendered action row and relevant content,
not only status 200. Do not click a provider-affecting action during installation
verification unless that exact effect is separately authorized.

## One-item workflow diagnosis

Start with:

```bash
sudo -u tgw /opt/TGW/.venvironments/tgw/bin/tgw get <SKU>

sudo -u tgw psql state_machine -x -c "
  SELECT job_id, queue_name, state, attempt_count, max_attempts,
         error_code, error_detail,
         payload_json->>'graph_id' AS graph_id,
         payload_json->>'object_generation' AS object_generation,
         payload_json->>'condition_hash' AS condition_hash,
         payload_json->'result' AS result,
         created_at, updated_at, finished_at
  FROM queue_jobs
  WHERE (entity_type='item' AND entity_id='<SKU>')
     OR payload_json->>'sku'='<SKU>'
  ORDER BY created_at DESC;"
```

The structured worker result is persisted under `payload_json.result`; it is not a
top-level database column. Read its evidence/reason code before deciding whether a
failure is retryable.

Never blindly requeue a row carrying graph/generation/condition identity. Use the
current Action Card and the PP-WORKFLOW-001 runbooks.

## Logs

```bash
journalctl -u tgw-http.service --since '-30 minutes' --no-pager
journalctl -u 'tgw-worker@*' --since '-30 minutes' -p warning --no-pager
journalctl -u tgw-mcp-sse.service --since '-30 minutes' --no-pager
journalctl -u <AFFECTED-UNIT> --since '<CHANGE-START>' --no-pager
```

Application file logs default to `/opt/TGW/var/log`. Test runs as `codex` must set a
private `TGW_LOG_ROOT`; otherwise tests which initialize workers will fail with a
permission error unrelated to the source under test.

Do not paste complete environment files, request bodies, credentials, or provider
payloads into a ticket. Use IDs, hashes, reason codes, timestamps, and bounded journal
excerpts.

## Restart discipline

- HTTP-only change: restart `tgw-http.service`.
- One worker change: restart only `tgw-worker@<queue>.service`.
- Shared queue/runtime/config change: inventory claimed work first, then restart the
  exact affected set.
- Nix unit definition change: use the registered Nix switch procedure and verify all
  units it reports.
- Never restart every worker merely to make an unknown state disappear.

```bash
sudo systemctl restart tgw-http.service
sudo systemctl restart tgw-worker@<queue>.service
systemctl is-active <UNIT>
systemctl show <UNIT> -p MainPID -p ExecMainStartTimestamp -p Result
```

For graph-bound/provider work, inspect active leases/effects before stopping a worker.
Preserve ambiguous and reconciliation-required attempts.

## Backups and timers

```bash
systemctl list-timers 'tgw*' --all --no-pager
journalctl -u tgw-db-backup.service -n 100 --no-pager
journalctl -u tgw-secrets-backup.service -n 100 --no-pager
journalctl -u tgw-snapshot.service -n 100 --no-pager
journalctl -u tgw-catalog-verify-nightly.service -n 100 --no-pager
```

A stale-backup health finding is not fixed by restarting unrelated services. Follow
the backup/DR procedure, identify the expected artifact and destination, and perform a
restore drill before calling the backup healthy.

## Flake state

```bash
sudo -u db -H bash -lc '
  set -eu
  cd /home/db/tgw-flake
  git branch --show-current
  git rev-parse HEAD HEAD^{tree}
  git status --short
  git log -5 --oneline
'
```

At last verification the branch was `master`, commit
`69044619318b7f3c2ccb66e6e6ba09dcba5c93e5`, and the worktree was clean. The old
`nix/tgw.nix.bak` obstruction was quarantined during installation and is no longer an
expected dirty-tree exception. If it or any other unknown file appears, attribute it;
do not delete it by habit.

## Coding-host status

Use the ordinary CLI subcommand:

```bash
sudo -u tgw /opt/TGW/.venvironments/tgw/bin/tgw coding access-status
```

At last verification it returned `coding_host=tgw-lib` but
`provider_status=unknown`, `receipt_source=unknown`, and `role=unknown`. On `tgw-lib`,
historical `tgw-coding-provision@*.service` instances remained failed. Therefore the
coding operator/API surface is installed, but automated coding provision must be
treated as not operational until a fresh request proves provider discovery, role,
receipt source, and one successful isolated run.

The standalone `tgw-coding` console script is currently broken because its entry
point targets a missing `coding_cli.main`. Use `tgw coding ...`; do not document the
broken wrapper as an operational path.

## Escalation bundle

Capture:

- host, current/booted closures, flake commit/status;
- selected source generation and selection receipt;
- installed changed-module hashes;
- affected unit contracts/start times;
- failed units and bounded journals;
- queue/action-card/effect/authority IDs and hashes;
- exact user-visible symptom and authenticated reproduction;
- changes/effects deliberately not performed.

This bundle supports diagnosis. It is not permission to retry, clean, reset, publish,
switch, or roll back.

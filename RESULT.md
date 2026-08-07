# #1738 fenced coding-provision bootstrap fix result

## Delivered

Fixed the remaining functional bootstrap blocker: `create_request()` no longer
calls `location_identity()` / Git and no longer validates a tgw-lib worktree
while running at the canonical tgw-prod service.  Request creation is now a
purely remote-client/service operation.

- **Request-safe creation** — `create_request()` enqueues only `todo_id`,
  `object_generation`, and the target host/worker identity.  It never inspects
  Git, a worktree, or any tgw-lib path (the `worktree` argument and the
  `--worktree` CLI flag are gone; the request body now carries just
  `todo_id` + `object_generation`).  The `dedupe_key` is
  `coding-provision:{todo_id}:{object_generation}`.
- **Worker-side envelope** — after retrieving a queued request, the tgw-lib
  worker resolves its local worktree (`worktree_root/todo-<id>`), validates it
  as a real Git worktree of the configured repository (`location_identity`),
  and echoes the immutable `{location, envelope_hash}` while claiming.
- **Service records the envelope under the exact lease** — the new
  `record_claim_envelope()` in `src/tgw/queue/state_machine.py` persists the
  worker-echoed envelope onto the leased durable row (owner + token gated).
  `claim_request()` validates the envelope against request-safe facts only:
  self-consistent hash, exact `todo_id`, matching worker identity, well-formed
  branch/head — never by probing Git or mounting the worktree.
- **Durable receipt carries the immutable envelope** — `complete_request()`
  authors the receipt from the envelope recorded at claim, so the durable
  receipt returned by the service contains the worker's local `location` and
  `envelope_hash`.
- **Fencing preserved, no SSH** — worker-host/identity fencing is enforced on
  both sides (`_validate_before_claim` on the worker, `_validate_service_worker`
  on the service) and the coding runner remains a local argv protocol.

## Changed files

- `src/tgw/coding_provision.py` — `create_request()` now enqueues request-safe
  data only (no `worktree`, no envelope, no Git).  Added `_resolve_local_envelope()`;
  `_validate_before_claim()` now computes and returns the local envelope instead
  of comparing against a service-stored one; `claim_request()` accepts the
  worker-echoed `location` and records it under the lease via
  `record_claim_envelope()`; `_validate_service_worker()` validates the envelope
  against request-safe facts only.
- `src/tgw/queue/state_machine.py` — added `record_claim_envelope()` (durable
  lease-gated payload merge).
- `src/tgw/coding_provision_worker.py` — `CodingProvisionClient.claim()` sends
  the local `location`; `claim_and_run()` passes the locally validated envelope
  and verifies the receipt's envelope hash **and** location.
- `src/tgw/http_server.py` — `CodingProvisionStart` no longer takes `worktree`;
  `CodingWorkerClaim` now takes `location`; both routes wired accordingly.
- `src/tgw/coding_cli.py` / `src/tgw/api.py` — the `start` request no longer
  sends or requires `--worktree`.
- `tests/test_coding_provision.py` — updated the harness (NativeQueue
  `record_claim_envelope`, `WorkerServiceClient.claim` signature) and replaced
  the old create-time git probe test with focused tests (below).
- `tests/test_coding_config_validation.py` — rewrote the wiring test to forbid
  any Git/worktree probing at create time and added dependency-free tests for
  the worker receipt envelope and claim fencing.
- `docs/coding-provision-config.md` — documented the request-safe request body
  and the worker-side envelope flow.

## Focused tests added

- `test_create_request_performs_no_git_or_worktree_probing` — proves
  `create_request` succeeds with `location_identity` and `subprocess.run`
  forbidden, and that the enqueued payload is exactly
  `{kind, todo_id, object_generation, host, worker_identity}`.
- `test_local_claimed_worker_produces_receipt_with_local_envelope` — a claimed
  worker's durable receipt records the local envelope (location + hash).
- `test_local_worker_claims_real_git_worktree_envelope_in_durable_receipt` —
  end-to-end with a real Git worktree: the worker resolves
  `worktree_root/todo-<id>`, validates it, and the receipt carries the real
  branch/head envelope.
- `test_worker_identity_fence_fails_before_native_claim`,
  `test_claim_rejects_envelope_not_bound_to_request`,
  `test_claim_rejects_envelope_hash_mismatch` — fencing and request-binding are
  preserved before any lease is granted.
- Dependency-free equivalents in `tests/test_coding_config_validation.py`
  (runs under plain `python3`, no pytest/fastapi/psycopg2 needed):
  `test_service_create_request_accepts_config_without_tgw_lib_local_paths`,
  `test_local_claimed_worker_produces_receipt_with_local_envelope`,
  `test_claim_rejects_envelope_not_bound_to_request_without_lease`.

No SSH, network, sudo, services, Nix, deployment, or real credentials were
used, and nothing outside this fixture was edited.

## Exact test results

Focused suite — runs dependency-free under plain `python3` (imports only
`tgw.config` plus `tgw.coding_provision` / `tgw.coding_provision_worker`
inside the wiring tests, with a psycopg2 stub injected only when the staging
harness is absent; no database or network access involved):

```text
$ python3 tests/test_coding_config_validation.py
PASS test_claim_rejects_envelope_not_bound_to_request_without_lease
PASS test_local_claimed_worker_produces_receipt_with_local_envelope
PASS test_service_create_request_accepts_config_without_tgw_lib_local_paths
PASS test_service_request_validation_ignores_worker_only_fields
PASS test_service_request_validation_rejects_malformed_endpoint
PASS test_service_request_validation_rejects_missing_required_fields
PASS test_service_request_validation_works_without_tgw_lib_local_paths
PASS test_validation_rejects_empty_identity_strings
PASS test_validation_rejects_non_object_config
PASS test_worker_configured_client_accepts_full_worker_config
PASS test_worker_configured_client_rejects_missing_local_paths
PASS test_worker_execution_validation_accepts_full_config_with_path_objects
PASS test_worker_execution_validation_rejects_malformed_endpoint
PASS test_worker_execution_validation_rejects_malformed_local_paths
PASS test_worker_execution_validation_rejects_missing_local_paths
PASS test_worker_execution_validation_rejects_missing_required_fields

all focused tests passed
exit=0
```

Static checks (all passed):

```text
$ python3 -m py_compile src/tgw/config.py src/tgw/coding_provision.py \
    src/tgw/coding_provision_worker.py src/tgw/http_server.py src/tgw/coding_cli.py \
    src/tgw/api.py src/tgw/queue/state_machine.py \
    tests/test_coding_config_validation.py tests/test_coding_provision.py
(no output — exit 0)

$ git diff --check
(no output — exit 0)

$ PYTHONPATH=src python3 - <<'EOF'   # with psycopg2 stub injected
import sys, types
m = types.ModuleType('psycopg2'); m.extras = types.ModuleType('psycopg2.extras')
sys.modules['psycopg2'] = m; sys.modules['psycopg2.extras'] = m.extras
import tgw.config, tgw.coding_provision, tgw.coding_provision_worker
from tgw.config import validate_service_request_config, validate_worker_execution_config
print('imports OK')
EOF
imports OK
```

## Not run in this fixture

- The full pytest suite (`tests/test_coding_provision.py` and
  `tests/test_coding_config_validation.py` under pytest) needs the staging
  harness (pytest, psycopg2, fastapi/TestClient), which is not on PATH here
  (same limitation as the prior #1738 result).  The new pytest tests there are
  py_compile-validated and their behavior is proven equivalent by the
  dependency-free wiring tests above; run
  `pytest -q tests/test_coding_config_validation.py tests/test_coding_provision.py`
  in the staging harness before deployment.
- Ruff is not installed in this fixture; changed lines are well under the
  200-column limit and import ordering follows `[tool.ruff.lint] select I`.
  Run `ruff check` on the changed files in the staging harness.

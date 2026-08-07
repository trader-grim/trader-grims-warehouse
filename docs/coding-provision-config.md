# Coding-provision API configuration

Coding-provision has two roles.  Catnanny/requester clients create and read
requests through the ordinary authenticated TGW API.  A tgw-lib worker is a
separate, HTTP-only consumer: it never needs a PostgreSQL DSN, local queue
database, SSH access, or a Python environment belonging to tgw-prod.

The service and worker each receive the same non-secret `coding` section.
The actual dedicated worker credential is supplied at runtime through the
environment variable named by `worker_credential_env`; do not put its value in
this JSON file.  The canonical service must expose the worker API at the
configured endpoint and set that environment variable for `tgw-http` too.

```json
{
  "coding": {
    "api_endpoint": "https://tgw-prod.example",
    "worker_api_endpoint": "https://tgw-prod.example",
    "host": "tgw-lib-01",
    "worker_identity": "tgw-coding-worker",
    "repository_root": "/srv/tgw/trader-grims-warehouse",
    "worktree_root": "/srv/tgw/worktrees",
    "worker_credential_env": "TGW_CODING_WORKER_API_KEY"
  }
}
```

Contract:

- Catnanny uses `api_endpoint` plus its ordinary TGW API credential to create,
  inspect, stop, and retrieve receipts from `/api/coding/requests`.  A request
  body carries only request-safe identity — `todo_id` and `object_generation`.
  The canonical tgw-prod service never receives or probes a tgw-lib worktree
  path and never inspects Git.
- Validation is split by role.  To accept a request, the canonical tgw-prod
  service requires only `api_endpoint`, `host`, and `worker_identity`; it
  must not require (or validate) the tgw-lib-local filesystem roots.  The
  local worker execution contract additionally requires `worker_api_endpoint`,
  `worker_credential_env`, `repository_root`, and `worktree_root`.
- tgw-lib uses `worker_api_endpoint`, `worker_identity`, and the credential
  named by `worker_credential_env` for
  `/api/coding/worker/requests/*`.  The credential is sent only as the
  `X-TGW-Worker-Authorization: Bearer …` header.
- `host`, `worker_identity`, `repository_root`, and `worktree_root` are part of
  the worker's fence.  After retrieving a request, the worker resolves its
  local worktree (`worktree_root/todo-<id>`), reprobes the Git identity there,
  and echoes the immutable envelope (location + hash) while claiming.  The
  service records that envelope under the exact lease — it never probes the
  worktree itself.
- The service alone owns claim/start/complete/fail queue transitions and
  service-authors the durable receipt from the recorded envelope.  The worker
  rejects a completed response unless its receipt source, identity, location,
  and envelope hash match the local envelope it validated.
- The coding runner remains a local argv protocol.  SSH is not a supported
  runner or worker transport.

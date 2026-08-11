# Packet #1697 — dedicated tgw-release executor + standalone Flake Release card

**pp_ref:** PP-FLAKEGATE-001
**Related:** #1621 (flake_mutation request gate, built+merged), #1625
(broader `tgw flake push`/`switch` human CLI — branch
`todo/1625-flake-push-switch` is stale/superseded, do not build on it or
merge it), #1698 (tgw-release Unix identity provisioning — NOT done yet,
out of scope here), #1699 (real master promotion — blocked on this
packet, do not attempt it here)
**Size:** M (one new narrow module, one new CLI script, one new HTTP
route + template, tests against a synthetic git fixture — no real push to
`~/tgw-flake`)

## Context budget

Read, in this order:
1. `src/tgw/flake_gate.py` in full (178 lines) — the existing
   request-only gate (`request_push`/`request_switch`/`mark_executed`/
   `audit`). This packet does NOT replace it — it adds a narrower,
   dedicated executor that *consumes* `flake_mutation` queue rows this
   module already creates.
2. `src/tgw/queue/state_machine.py` lines ~1339-1480 (the
   `flake_mutation` section: `FLAKE_MUTATION_QUEUE`,
   `list_flake_mutation_jobs`, `get_flake_mutation_job`,
   `mark_flake_mutation_executed`, `list_executed_flake_push_shas`).
3. `TGW-Master-Plan.md`'s full `PP-FLAKEGATE-001` section (the incident,
   the state-machine-gate design, and the 2026-07-26 "Operator-console
   flake-execution card" subsection) — this packet's spec below is
   already distilled from it, but read it once for the exact language
   Dave used ("fool-resistant", "typed master-only fast-forward
   promotion", "no Nix activation or generic shell").

Do not re-derive the incident or the state-machine-gate design from
scratch — both are settled. What's genuinely new in this packet: (a) an
executor that actually performs the push (existing code only records that
a human did it by hand), and (b) a UI surface over the queue.

## Background

PP-FLAKEGATE-001 exists because `nix-flake-maintainer` once pushed to
`origin/master` on `~/tgw-flake` without Dave's explicit confirmation
(todo #1620 incident, 2026-07-21). The fix so far: `flake_gate.py`
(#1621) turns push/switch into a `queue_jobs` row a human reviews; today,
closing that row (`mark_executed`) is pure record-keeping — a human
still runs the real `git push`/`nixos-rebuild switch` by hand, by typing
it themselves, then tells the system it happened. Dave found the fully
manual version "not very friendly" and, on 2026-07-26, asked for two
things to close the gap without weakening the actual decision gate:

1. **A dedicated `tgw-release` executor** that performs the real `git
   push` itself, but only after verifying (not trusting) that the
   local host and commit match the queued request exactly, and only for
   a hardcoded master-only fast-forward — no generic shell, no
   nixos-rebuild/switch capability at all (that stays fully manual, out
   of scope for this executor).
2. **A "Flake Release card"** so Dave doesn't have to discover the host/
   commit/exact CLI incantation himself — a page that shows each queued
   `flake_mutation` job with full evidence and a single "Confirm push"
   action.

**Scope decision, confirmed with Dave this session:** the plan's card
spec assumes a shared operator console (PP-GODCONSOLE-001/PP-OUTBOX-001)
that does not exist in code yet (checked: no console/card-rendering code
found anywhere in `src/tgw/`). Dave chose "standalone page now" — build
`/form/flake-release` as its own route today; folding it into a shared
console is future work, not this packet's job.

## Spec

### 1. `src/tgw/flake_release.py` (new module)

One function, `execute_push(job_id: str, confirm_sha: str, *, repo_path: Optional[Path] = None) -> Dict[str, Any]`:

- Look up the job via `state_machine.get_flake_mutation_job(job_id)`.
  `{"ok": False, "error": ...}` if missing, not `state='queued'`, or
  `operation != 'push'` (switch jobs are explicitly **not supported** —
  return a clear "this executor is push-only, no Nix activation" error,
  never attempt one).
- **Typed confirmation, fool-resistant:** `confirm_sha` must exactly
  equal the job's recorded `entity_id` (the full commit SHA) — reject on
  any mismatch, including short-SHA or case difference, with no side
  effect. This is the "typed" part of "typed master-only fast-forward
  promotion": the caller must reproduce the exact hash being promoted,
  not just answer yes/no.
- **Host check:** compare `socket.gethostname()` (or the existing
  hostname-check helper if `flake_gate.py`/state_machine already has one
  — reuse, don't reinvent) against the job's recorded `host`. Refuse on
  mismatch.
- **Repo resolution:** `repo_path` defaults to the job's recorded `repo`
  path (resolved, no traversal — reuse whatever path-safety helper the
  rest of `src/tgw` already uses for fenced paths, e.g. the pattern in
  `itemdata_scrub`'s fence-paths packet #1305, don't write a new one).
- **HEAD check:** `git rev-parse HEAD` in `repo_path` must exactly equal
  the job's commit. Refuse on mismatch (never push "whatever's checked
  out").
- **Master-only, hardcoded:** the push target is always
  `refs/heads/master` on `origin` — not a parameter, not read from the
  job payload, a literal in the code.
- **Fast-forward preflight:** `git fetch origin` (fixed argv, no shell),
  then `git merge-base --is-ancestor origin/master <commit>` — refuse if
  this fails (would be a non-fast-forward push).
- **Execute:** `git push origin <commit>:refs/heads/master` — fixed argv
  list passed to `subprocess.run` (`shell=False` always; no f-string
  building a shell command anywhere in this module).
- **Postflight:** re-fetch, confirm `origin/master`'s new SHA equals the
  pushed commit. Only on confirmed match: call
  `state_machine.mark_flake_mutation_executed(job_id, executed_by=...)`.
- **Append-only receipt:** on every call (success or failure), append one
  JSON line to `/opt/TGW/var/log/flake-release-receipts.jsonl` (open
  mode `"a"`, never truncate/rewrite) with `{job_id, commit, host, repo,
  result, error, timestamp}`. Follow the existing one-off-script
  announce convention (`tgw_logging.announce_script_run()`, invariant E9)
  if this ends up runnable standalone.
- On any failure, the job stays `queued` (never touched) — same
  leave-for-retry contract `mark_flake_mutation_executed` already has.

No other entrypoint in this module executes git/shell in any generic way
— no arbitrary command parameter, no `switch`/`nixos-rebuild` path at
all.

### 2. `bin/tgw-release` (new CLI script)

Thin wrapper, mirroring `bin/tgw-cloud-sync`'s style: `tgw-release
<job-id>` — prints the job's full evidence (host, repo, commit, summary)
and **prompts interactively for the operator to type the full commit SHA
to confirm** (no `--yes`-skips-typing shortcut — that's the point of
"typed"). Calls `flake_release.execute_push()` with what was typed. A
deliberately separate, narrower binary from any future `tgw flake push`
— never installed as MCP-callable, never referenced from any agent
profile's allowed-tools list.

### 3. `/form/flake-release` (new HTTP route, `http_server.py`)

- No Bearer auth required (matches `/form/items` — network-trust,
  session-cookie gated by the existing `_session_guard` middleware, same
  as every other `/form/*` route).
- Lists queued `flake_mutation` jobs via `flake_gate.queue_table()`: for
  each, show kind (push/switch), host, repo, commit (full, not
  truncated — the operator needs to read/type it), summary, linked PP/
  todo, state.
- **Switch-kind jobs:** render as read-only evidence with a visible "not
  supported — this executor is push-only, no Nix activation" label; no
  confirm control of any kind.
- **Push-kind jobs:** a form with a text input where Dave types the full
  commit SHA, POSTing to a new endpoint (e.g. `POST
  /form/flake-release/{job_id}/confirm`) that calls
  `flake_release.execute_push(job_id, confirm_sha)` directly (Python
  import, not shelling out to `bin/tgw-release`) and re-renders the page
  showing success + receipt link, or the specific rejection reason
  (host/HEAD/typed-SHA mismatch, non-fast-forward) with no side effect.
- This endpoint must not be reachable via any MCP tool (`tgw`
  MCP server's tool list) — check `src/tgw/mcp_server.py` and confirm
  this route has no corresponding tool wired, add a test asserting it.

### 4. Tests

New `tests/test_flake_release.py` using a synthetic git fixture (two
local bare/work repos standing in for "local checkout" and "origin" —
never `~/tgw-flake` itself, never a real push anywhere):

- Host mismatch → rejected, no push attempted, job stays queued.
- HEAD mismatch → rejected.
- Wrong/short/mismatched typed SHA → rejected (exact string, case-
  sensitive).
- Non-fast-forward (origin/master has a commit not an ancestor of the
  target) → rejected before any push attempt.
- `operation='switch'` job → rejected outright with the "push-only"
  error, no ancestor/HEAD checks even attempted.
- Happy path: all checks pass → push succeeds against the synthetic
  origin, `mark_flake_mutation_executed` called exactly once, one JSON
  line appended to the receipts file with `result: success`.
- A failed attempt appends a `result: failure` receipt line and does NOT
  call `mark_flake_mutation_executed`.
- `/form/flake-release` renders queued push and switch jobs correctly
  (switch shows the disabled/unsupported label, push shows the confirm
  form) — extend `tests/test_http_server.py`.
- Confirm the new route has no matching tool registration in
  `mcp_server.py`.

## Out of scope (do not touch)

- Any real push to `~/tgw-flake`'s actual `origin/master` — that's
  #1699, later, once this packet is reviewed and merged.
- `nixos-rebuild switch` execution of any kind — permanently out of
  scope for this executor, not just this packet.
- Provisioning the `tgw-release` Unix identity/credential/socket
  boundary — that's #1698. This packet's code runs under whatever
  identity invokes it today (`db`/`tgw`, same as everything else) —
  don't invent a fake identity switch to simulate #1698.
- Folding the card into PP-GODCONSOLE-001/PP-OUTBOX-001's shared console
  — that console doesn't exist yet; this page stands alone.
- Rewriting or merging `todo/1625-flake-push-switch` — treat it as
  abandoned/superseded reference only.
- Any change to `flake_gate.py`'s existing `request_push`/
  `request_switch`/`audit` behavior.

## Acceptance

- `pytest -q` (full suite) and `ruff check` clean.
- Live evidence pasted in the result manifest: the synthetic-fixture
  happy-path test's actual output (push succeeded, receipt line content,
  `mark_flake_mutation_executed` call confirmed), plus one rejected-case
  output (e.g. HEAD mismatch) showing no side effect occurred.
- No production action taken — no push to the real `tgw-flake` repo, no
  systemd/service change, no reference to a specific host beyond what's
  parameterized/tested against the synthetic fixture.
- Produce `docs/TGW-Plan-Vault/plan/packets/results/1697-RESULT.md`
  following the existing convention (see `1638-1639-RESULT.md` for
  format).

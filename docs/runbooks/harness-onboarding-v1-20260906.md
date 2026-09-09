# Harness onboarding (tgw-lib)

**Owner:** Dave
**Applies to:** tgw-lib only; never tgw-prod
  (`host_policy.production_installs_catalog = false`)
**Status:** current canonical procedure for LEAF-11-9 (W0–W7 landed). Supersedes
  the implicit "operating without the orchestrator" checklist in the plan
  runbook `actor-mcp-onboarding.md` — archive that section on the next plan pass.

This is the whole onboarding procedure. There is no hand-wiring runbook — the
first end-to-end run (Todo 1987) needed six ad-hoc fix commits precisely because
the steps below were done by hand, one discovery at a time. If a step here is
not enough, the gap is a bug in `--repair harness`, not a note to add to a
checklist.

## What onboarding produces

A harness that takes a task from "the continual-harness code is on `main`" to
"a canary task landed on `refs/heads/main` through the real orchestrator". It is
done **only** when the offline canary lands — that is a mechanical postcondition,
not a judgement call.

"Working system" is reachable with **no functioning paid LLM and no network**:
onboarding exists because the model executors are not yet wired or
authenticated, so it cannot require them.

## The one operation

```text
# once, so the root-owned launcher carries the `harness` repair target:
sudo -n /usr/local/sbin/tgw-coding-bootstrap --commit <sha>
# then, the onboarding operation itself:
sudo -n /usr/local/sbin/tgw-coding-bootstrap --repair harness --commit <sha>
```

`<sha>` is the exact clean `refs/heads/main` commit you are onboarding. The
first line is the ordinary full coding bootstrap (it reinstalls
`/usr/local/sbin/tgw-coding-bootstrap` from the commit tree); skip it only if
`tgw-coding-bootstrap --repair harness --help` already lists `harness`. The
`--repair harness` run is idempotent. It:

1. ensures the `tgw-harness` (uid 981, login shell, publisher) and `tgw-coder`
   (uid 980, nologin, confined, non-publisher) identities, their `tgw-coders`
   membership, and the `tgw-harness` `pg_ident` entry — materialised from the
   commit tree, exactly like `--repair database`;
2. makes `tgw.development.*` importable by the controller venv from any cwd with
   no `PYTHONPATH` (W5);
3. for each executor marked `enabled` in the executor catalogue
   (`environment.catalog.actors` in
   `/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json`, W1): installs its
   binary at the catalogue path, verifies its runtime deps, and records its
   credential slot as **ready** or **unavailable**. A missing or broken
   credential is a **WARN**, never a FAIL — that executor is simply marked
   unavailable and the chain skips it (`SessionUnavailable`);
4. runs the canary (below) and emits a repair receipt alongside the other
   `--repair` targets.

## The two operator-applied inputs

Both stay operator-applied, but neither is hand-authored any more.

### 1. The sudoers fragment

Canonical source, checked in and reviewed:
`config/environment/sudoers.d/tgw-harness`.

Install it verbatim — do not retype it:

```text
sudo visudo -cf /opt/TGW/tgw-lib/src/trader-grims-warehouse/config/environment/sudoers.d/tgw-harness
sudo install -o root -g root -m 0440 \
  /opt/TGW/tgw-lib/src/trader-grims-warehouse/config/environment/sudoers.d/tgw-harness \
  /etc/sudoers.d/tgw-harness
```

The fragment grants the only privilege boundary the identity model has:
`claude` → `tgw-harness` → `tgw-coder`. Doctor's `access.harness-sudoers` check
compares the installed file byte-for-byte against the canonical source and
reports drift or absence as a named **FAIL** with the exact expected content.
(If the installed file is `0440` and you run `tgw doctor check` as an ordinary
user, the check reports **UNKNOWN** for content and points you here; the
byte comparison then runs under `--repair harness` or `sudo tgw doctor check`.)

The **operator effect-envelope** is a second, separate fragment —
`config/environment/sudoers.d/tgw-operators` (PP-ROLES-001 WU-9): one
`tgw-operators` group + one curated `%tgw-operators ALL=(root) NOPASSWD:` grant
(manage/observe the `tgw-*` units, run `tgw-coding-bootstrap`) that replaces the
`(db) NOPASSWD: ALL` proxy in `/etc/sudoers.d/90-db-nopasswd`. Install it the
same way (its own header lists the `groupadd` / `gpasswd` / proxy-removal
steps). Doctor's **`access.operator-sudoers`** check mirrors
`access.harness-sudoers` once the group exists; until the operator ratifies WU-9
it reports **UNKNOWN**, never FAIL.

### 2. The provider keys

`/opt/TGW/secrets/tgw.env` (`0640 root:tgw-coders`). The operator pastes the
model-provider keys (`CLAUDE_CODE_OAUTH_TOKEN`, `OPENROUTER_API_KEY`, …); the
repair only **verifies** them. Slot names come from the executor catalogue
(W1); values come from `tgw.env` today and from the Todo #1253 credential
broker later. This is a single host-wide store — tgw-lib holds model-provider
keys only.

## Reading the canary result

Three tiers, tried cheapest-first. `--repair harness` succeeds as soon as one
lands.

| Tier | When it runs | Meaning of the result |
| --- | --- | --- |
| **OFFLINE stub** | always — this is the gate | A synthetic task runs through the built-in deterministic `stub` executor (no binary, no network, no credential) → worktree → `pytest_gate` → `harness_git` squash+FF → `main_ref_guard` → ledger. Must reach `outcome == landed` (the stub writes only a dedicated canary path). `--repair harness` **exits non-zero** unless this lands. This tier alone means "onboarded" — every non-LLM piece is proven. |
| **FREE-MODEL** | default, when a free route is reachable | The same dispatch through a real model at zero cost (a free model via opencode, Groq's free tier, or an OpenRouter `:free` model), selected by the model-currency tool (W7). Proves executor → model → receipt without a paid credential. **SKIP** (with a note), not FAIL, if no free route is reachable. |
| **LIVE / PAID** | opt-in: `--repair harness --live-canary` | A real trivial Todo through a configured paid executor. **SKIP** (with a note) unless a paid credential verifies. Only an explicit `--live-canary` with a verified credential lets a genuine model failure fail the run. |

After the run, `tgw doctor check` must report every `harness.*` and
`database.local-coding*` check green, and the receipt records which canary
tiers landed or skipped.

## Adding an executor

`dsh` (deepseek), `agy`, `gemini`, `opencode` are ready to wire. Each is:

1. one `actors` entry in the executor catalogue — `{name, enabled, binary,
   install source + path, runtime dep + verification command, credential slot
   name(s), any ~/.<tool>/auth.json path}`;
2. its credential slot pasted into `/opt/TGW/secrets/tgw.env` (optional — a
   missing credential just marks it unavailable);
3. one re-run of `sudo -n /usr/local/sbin/tgw-coding-bootstrap --repair harness
   --commit <sha>`.

No commit to harness code. If wiring a new executor needs a code change, that
is a defect in the catalogue contract (W1).

## Work-unit status (LEAF-11-9)

| Step | Backed by | Landed |
| --- | --- | --- |
| stub executor / offline canary | W0 | yes (`732a1d098`) |
| executor catalogue contract | W1 | yes (`cc80112b2`) |
| controller-venv import path | W5 | yes (`b8ef1ed01`) |
| model-currency tool | W7 | yes (`9da097144`) |
| sudoers canonical + `access.harness-sudoers` drift check | W4 | yes |
| this document | W6 | yes |
| `--repair harness` Doctor op (`repair_harness`) | W2 | yes |
| 3-tier canary dispatch (`tgw.development.harness_canary`) | W3 | yes |

`doctor check` gains three standing checks after onboarding — `harness.identities`
and `harness.import-path` (repair postconditions) and `harness.executors`
(advisory: a missing model credential is a WARN, never a gate). The offline
canary landing is enforced inside `repair_harness` itself: the operation exits
non-zero if it does not land.

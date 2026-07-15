# Packet: install tgw-worker@alt_text.service — clear the stuck alt_text queue

Todo: #1108   PP: PP-DATALEARN-001   Track: infra (single packet, not a batch)

## Context budget (ALL the model may load)
This packet + `~/tgw-flake/nix/tgw.nix` (whole file, ~300 lines) +
`~/tgw-flake/nix/hosts/tgw-prod.nix` (whole file) +
`~/tgw-flake/nix/CLAUDE-NIX.md` (eval-and-fix workflow section) +
`src/tgw/workers/alt_text.py` (whole file, confirm it's complete/correct —
don't assume) + `pyproject.toml` lines ~40-80 (console_scripts section,
already confirmed to contain `tgw-alt-text-worker = "tgw.workers.alt_text:main"`).

## Verified live before this packet was written
- `alt_text` queue: 5 jobs, all state `queued`, `lease_owner` NULL, oldest
  `created_at` 2026-06-26, newest includes `tgw202605051936445`
  (job `b61135e8-1634-4c69-8829-1393eea03ee7`, created 2026-07-02).
- `systemctl list-units 'tgw-worker@*' --all` — no `alt_text` unit loaded;
  14 other queue workers are.
- `src/tgw/workers/alt_text.py` exists, `QUEUE_NAME = "alt_text"`, has a
  working `cmd_alt_text()` entry.
- `src/tgw/workers/ai_identify.py` lines 428-444 actively enqueue into this
  queue after every run (`dedupe_key=f"alt_text:{sku}"`) — this is a live,
  working producer with zero consumer, not a dead code path.
- `pyproject.toml` already has the `tgw-alt-text-worker` console-script
  entry point wired to `tgw.workers.alt_text:main` — nothing to add there.
- `~/tgw-flake/nix/tgw.nix`'s `workerScripts` attrset (queue name → console
  script) does NOT have an `alt_text` entry — this is the actual gap.
- `~/tgw-flake/nix/hosts/tgw-prod.nix`'s `services.tgw.workers` list (the
  per-host enabled-queue list) does NOT include `"alt_text"`.
- This is pre-existing todo #1108 (PP-DATALEARN-001), already on record
  with three options (install worker / route through AI-Studio batch path
  per #144 / stop enqueueing). Dave, 2026-07-14: "yes same process" —
  confirmed go-ahead to write this packet and dispatch via the same
  process as the PP-DEADLETTER-001 batch, without picking an option
  himself first. Given the entry point already exists and this is a
  working producer/consumer pair just missing the unit declaration,
  option 1 (install the worker) is the evidence-supported smallest fix —
  build that, don't re-litigate the three options from scratch.

## Spec
1. In `~/tgw-flake/nix/tgw.nix`, add `alt_text = "tgw-alt-text-worker";`
   to the `workerScripts` attrset (alphabetical-ish placement near
   `ai_identify`/`bundle_intake` is fine, match existing style — the map
   is not alphabetized, just match the loose grouping already there).
2. In `~/tgw-flake/nix/hosts/tgw-prod.nix`, add `"alt_text"` to the
   `services.tgw.workers` list. Read the comment block right above that
   list first (lines ~110-121) — it documents *why* several workers are
   deliberately excluded (stopped/redesign/defused). Add a short comment
   next to `alt_text` explaining why it's newly enabled (todo #1108, real
   consumer for `ai_identify`'s existing enqueue, was simply never wired).
3. Follow `nix/CLAUDE-NIX.md`'s eval-and-fix workflow: run whatever local
   eval/build check that doc specifies BEFORE proposing a live
   `nixos-rebuild switch` — do not skip straight to switch.
4. Do NOT run `nixos-rebuild switch` yourself without flagging it clearly
   in the result manifest as the final live step — this is a system-level
   service change on tgw-prod (the live production host). If your agent
   harness allows you to run it after a clean eval, do so and report the
   exact command + output; if you are not confident it is safe (e.g. eval
   surfaces unrelated unrelated errors), stop and report status instead of
   forcing it.
5. After the unit exists and is active (`systemctl status
   tgw-worker@alt_text.service`), confirm the 5 stuck jobs actually drain
   — check `queue_jobs` state for the `alt_text` queue before/after, or
   watch `journalctl -u tgw-worker@alt_text.service -f` briefly. Do not
   force-requeue anything — they're still `queued`, not `dead_letter`, so
   the worker should just pick them up once it's running.
6. Run the full offline suite before finishing — zero regressions.

## Out of scope
- Any change to `alt_text.py`'s own logic/prompt — this packet is purely
  "give the existing consumer a systemd unit," not a behavior change.
- The AI-Studio batch-path alternative (#144) — not being built here,
  Dave chose to install a real worker per the evidence above.
- Any other queue/worker.
- a1131's flake (this is tgw-prod-only; do not touch `hosts/a1131.nix`
  unless you find alt_text is also expected to run there — check first,
  don't assume symmetry).

## Dataset
No dataset/schema changes. This only lets already-queued, already-durable
jobs get consumed — no data is discarded, overwritten, or newly generated
by this packet itself (the LLM calls happen inside `alt_text.py`, which
already exists and is out of scope here).

## Acceptance (live)
1. `workerScripts` and `services.tgw.workers` diffs shown in the result
   manifest.
2. Eval/build check result shown (command + output, per CLAUDE-NIX.md).
3. If `nixos-rebuild switch` was run: exact command + confirmation
   `tgw-worker@alt_text.service` is `active (running)`.
4. Queue state before/after for the `alt_text` queue (job counts by
   state) — show the 5 stuck jobs actually moved off `queued`.
5. Full offline suite result — zero regressions.
6. If the switch was NOT run (agent judged it unsafe to do unattended):
   say so plainly, explain why, and leave the flake changes staged/
   committed on the task branch for Dave to review and switch manually.

## Quota/risk
Low for the flake/systemd change itself (declarative, revertible via
flake generation rollback). The 5 alt_text jobs will each make one LLM
vision call once the worker starts — negligible quota impact (5 calls).
The only real risk is a bad `nixos-rebuild switch` on tgw-prod, the live
host — this is why step 4 requires a clean eval first and permits
stopping short of switch if anything looks off.

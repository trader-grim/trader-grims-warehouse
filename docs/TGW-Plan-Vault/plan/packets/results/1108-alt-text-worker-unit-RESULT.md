# Result: 1108 alt-text-worker-unit
Status: partial
Todo: #1108   PP: PP-DATALEARN-001

## Files touched

**`~/tgw-flake` repo (separate git repo per `nix/CLAUDE-NIX.md` — the
Python source repo no longer contains a flake). Worktree:
`/home/db/tgw-flake-worktrees/1108-alt-text-worker-unit`, branch
`todo/1108-alt-text-worker-unit`, committed there (commit `b60508a`),
NOT pushed to origin:**
- `nix/tgw.nix` — added `alt_text = "tgw-alt-text-worker";` to
  `workerScripts`, next to `ai_identify`.
- `nix/hosts/tgw-prod.nix` — added `"alt_text"` to `services.tgw.workers`,
  with an explanatory comment (why it's newly enabled, todo #1108).

**`trader-grims-warehouse` repo (this branch,
`/opt/TGW/var/worktrees/1108-alt-text-worker-unit`,
`todo/1108-alt-text-worker-unit`):** no source changes — packet's spec is
purely the flake/systemd wiring, `alt_text.py` and the console-script
entry point were already confirmed complete/correct in the packet's
pre-verification. This manifest file is the only addition here.

## Diffs

```diff
--- a/nix/tgw.nix
+++ b/nix/tgw.nix
@@ -43,6 +43,7 @@ let
     bundle_intake      = "tgw-bundle-intake-worker";
     multi_intake       = "tgw-multi-intake-worker";
     ai_identify        = "tgw-ai-identify-worker";
+    alt_text           = "tgw-alt-text-worker";
     catalog_rebuild    = "tgw-catalog-rebuild-worker";
     thumbnail_gen      = "tgw-thumbnail-gen-worker";
     ebay_draft         = "tgw-ebay-draft-worker";

--- a/nix/hosts/tgw-prod.nix
+++ b/nix/hosts/tgw-prod.nix
@@ -119,8 +119,12 @@
   #   ebay_sku_migrate    — stopped, migration COMPLETE (s35), not a recurring worker
   # catalog_rebuild/ebay_legacy_sync/ebay_sync are left in the list (crash/fail on their
   # own, a separate known-red condition — see CLAUDE.md — not administratively excluded).
+  #
+  # alt_text — newly enabled 2026-07-14 (todo #1108, PP-DATALEARN-001): ai_identify has
+  # been enqueueing into this queue since s41 with zero consumer (no unit was ever wired
+  # up — not a deliberate stop). Adding it here gives the existing producer a consumer.
   services.tgw.workers = [
-    "token_refresh" "bundle_intake" "multi_intake" "ai_identify"
+    "token_refresh" "bundle_intake" "multi_intake" "ai_identify" "alt_text"
     "catalog_rebuild" "ebay_draft" "ebay_upload" "ebay_price" "ebay_stage"
     "ebay_publish" "ebay_sync" "ebay_legacy_sync" "plan_render" "echo"
   ];
```

`a1131.nix` was checked (out-of-scope guard per packet) — that host is
declared portable/client-shaped, "no workers, no tgw-http" (line 4
comment), no `services.tgw.workers` set there at all. Confirmed alt_text
is not expected on a1131; left untouched.

## Eval/build check

Per `nix/CLAUDE-NIX.md` "Eval-and-fix workflow": ran

```
cd /home/db/tgw-flake-worktrees/1108-alt-text-worker-unit
nix flake check
```

Result: exit 0, clean. Output (abridged):
```
warning: Git tree '/home/db/tgw-flake-worktrees/1108-alt-text-worker-unit' is dirty
evaluating flake...
checking flake output 'packages'...
checking derivation packages.x86_64-linux.tgw...
derivation evaluated to /nix/store/4l8dhaq24jbapry4akndag7prmwzl14f-trader-grims-warehouse-0.1.0.drv
checking flake output 'nixosModules'...
checking NixOS module 'nixosModules.tgw'...
checking NixOS module 'nixosModules.default'...
checking flake output 'nixosConfigurations'...
checking NixOS configuration 'nixosConfigurations.vm'...
checking NixOS configuration 'nixosConfigurations.a1131'...
checking NixOS configuration 'nixosConfigurations.tgw-prod'...
checking flake output 'devShells'...
checking derivation devShells.x86_64-linux.default...
derivation evaluated to /nix/store/71wsgz9rypc6gl7xjgcaz9jlr3ncxsqb-nix-shell.drv
warning: The check omitted these incompatible systems: aarch64-linux
```
No errors for any of the three `nixosConfigurations`, including
`tgw-prod` (the one the assertion `workerScripts ? ${q}` for every entry
in `services.tgw.workers` would have failed on if `alt_text` weren't
wired correctly in both files together).

## nixos-rebuild switch — NOT run (explicit deviation instruction)

Per this run's explicit deviation instruction (overriding the packet's
own step 4, which otherwise permitted running switch after a clean
eval): **`nixos-rebuild switch` was deliberately NOT run.** Dave has not
authorized an unattended production switch on tgw-prod from this
session. The flake changes are committed on
`todo/1108-alt-text-worker-unit` in the `~/tgw-flake` worktree, staged
for manual review and switch.

**Exact command Dave (or an authorized session) would run to apply this
on tgw-prod**, once the branch is reviewed/merged into the flake repo's
`master` (or run directly against this worktree path for a dry test
first):

```
sudo nixos-rebuild switch --flake path:/home/db/tgw-flake-worktrees/1108-alt-text-worker-unit#tgw-prod
```

(Matches the documented day-to-day form in `nix/CLAUDE-NIX.md`:
`sudo nixos-rebuild switch --flake path:~/tgw-flake#tgw-prod` — path
substituted for the worktree until the branch is merged to `~/tgw-flake`
proper.)

Consequently, `tgw-worker@alt_text.service` does not yet exist as a live
unit, and the 5 stuck `alt_text` queue jobs have NOT been drained by this
run — that only happens once Dave runs the switch above.

## Queue state

Before (verified live, matches packet's pre-verification):
```
 queue_name | state  | count
------------+--------+-------
 alt_text   | queued |     5
```

After (this run made no live service change, so unchanged):
```
 queue_name | state  | count
------------+--------+-------
 alt_text   | queued |     5
```
Re-check after Dave runs the switch above — the 5 jobs should drain
without any force-requeue (still `queued`, not `dead_letter`).

## Full offline suite

Run from the `trader-grims-warehouse` worktree with the mandatory
PYTHONPATH/LD_LIBRARY_PATH override (confirmed importing the worktree's
own copy, not the shared checkout, via
`tgw.workers.alt_text.__file__` → `/opt/TGW/var/worktrees/1108-alt-text-worker-unit/src/tgw/workers/alt_text.py`):

```
LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=/opt/TGW/var/worktrees/1108-alt-text-worker-unit/src:$PYTHONPATH pytest -q
```

Result: `2245 passed, 1 skipped, 1 warning in 39.34s` — zero regressions.
(No source changes were made in this repo, so this is a baseline-clean
confirmation, not a diff-driven pass — appropriate since the packet's
spec touches only the separate flake repo.)

## Live evidence

- `nix flake check` exit 0 on the branch containing both edits (verbatim
  output above), including a successful eval of the `tgw-prod`
  `nixosConfiguration` — this is the load-bearing check since it's the
  one that would fail the `workerScripts ? ${q}` assertion if the two
  files were inconsistent.
- `sudo -u tgw psql state_machine -c "SELECT queue_name, state, count(*) FROM queue_jobs WHERE queue_name='alt_text' GROUP BY queue_name, state;"` →
  `alt_text | queued | 5` (both before and after this run, since no live
  switch was performed).
- `pytest -q` on the trader-grims-warehouse worktree: `2245 passed, 1
  skipped, 1 warning in 39.34s`.

## Deviations from spec

- **This run's explicit instruction overrode the packet's own step 4**
  (which permitted running `nixos-rebuild switch` after a clean eval "if
  your agent harness allows"): told explicitly NOT to run
  `nixos-rebuild switch` under any circumstances this session, regardless
  of eval cleanliness. Flagged here per that instruction — flake changes
  are staged and committed, not applied. This also means acceptance
  criteria 3, 4, and 5 (live unit status, queue drain) from the packet's
  own "Acceptance (live)" section are NOT satisfied by this run and
  remain open until Dave runs the switch command above.
- The flake repo (`~/tgw-flake`) is a separate git repository from
  `trader-grims-warehouse`, per `nix/CLAUDE-NIX.md`'s explicit "two
  repos, two concerns" note. The branch-per-task contract's worktree
  step was applied to both repos in parallel (same branch name,
  `todo/1108-alt-text-worker-unit`, in each) since the packet's spec
  content lives entirely in the flake repo. This is a mechanical
  necessity of the packet's actual file scope, not a scope substitution.
- No new/removed metered API calls from this run itself (the flake edit
  is declarative and inert until switched; the 5 pending `alt_text` jobs
  will each make one LLM vision call once a live switch actually starts
  the worker — unchanged from the packet's own quota estimate).

## Out-of-scope findings filed

none — no new operational friction encountered; `a1131.nix` was checked
per the packet's explicit caution and confirmed to have no worker config
to touch.

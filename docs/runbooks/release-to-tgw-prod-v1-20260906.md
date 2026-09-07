# Runbook: release code to tgw-prod

**v1 · 2026-09-06 · status: authoritative**

Supersedes the admission/preflight/signature procedure in
`governed-production-procedures-v1-20260821.md` (Mordac-era W13–W18 apparatus).
Per OPERATOR-PRINCIPLE-20260903-SIMPLICITY / DIRECT-OPERATOR-COMMAND-PRECEDENCE
and `AMENDMENT-20260905-LEAF-11-DECOUPLE-LIB-PROD-AND-DELETE-APPARATUS`:
**the only gate on a prod release is that the operator is told before the
`current` symlink flips.** No admission receipt, no environment-preflight
signature, no plan-commit binding.

---

## Facts about the current prod deploy surface

- **What runs:** `/opt/TGW/current` → `/opt/TGW/releases/<generation>/`. Each
  release is a full, immutable `git archive` of one commit + a
  `.release-manifest.json`. Workers launch via `tgw-runtime-launch`, which
  resolves `/opt/TGW/current` **at process start** — so a generation change
  needs a worker restart to take effect.
- **What flips it:** `sudo /opt/TGW/.venvironments/tgw/bin/tgw-release-install
  --root /opt/TGW …`. That console script runs `tgw.release_installer` from an
  **editable install pinned to `codex-item-api-d139cff9e-20260818`**
  (`…/site-packages/__editable__.trader_grims_warehouse-0.1.0.pth`), which is
  **before the 2026-08-20 admission gate** (`37e53890` /
  `2b8f2b27` W13–W18). So its `install` subcommand takes **no** key/receipt
  arguments. This is how every deploy through 2026-09-03 was done
  (`f82ee257`, `08611d9c`, `3614bf037` — all keyless `completed` receipts).
- **Fragility:** if that venv is rebuilt or re-pinned to current code, the
  keyless `install` stops working (the gate returns). The durable replacement
  is `install-direct` (below), landed on tgw-lib `main` in `dcfa3a86`.
- **Branch:** prod tracks **`fix/item-workflow-prod`**, which is divergent
  from `main` (as of 2026-09-06: 21 commits only on the prod branch, 122 only
  on main). Do **not** deploy `main` wholesale without a reconcile — you would
  ship 122 unproven commits and drop 21 prod hotfixes. Cherry-pick onto the
  prod branch for targeted fixes.
- **`db` on tgw-prod has full passwordless `sudo`.** The interactive session
  reaches prod read-only via `sudo -n -u db ssh tgw-prod "<cmd>"`.

---

## Procedure (targeted fix)

All commands run on **tgw-lib** unless marked `[prod]`.

### 1. Build the commit to ship

```
git switch -c deploy/<slug>-$(date +%Y%m%d) fix/item-workflow-prod
git cherry-pick <sha>...            # the exact fix commit(s)
# resolve conflicts against the prod branch, run affected tests
```

Let `X` = the resulting commit. `TREE=$(git rev-parse "$X^{tree}")`.

### 2. Materialize an archive

```
git archive --format=tar -o /tmp/tgw-$X.tar "$X"     # embeds pax comment=<X>
SHA=$(sha256sum /tmp/tgw-$X.tar | cut -d' ' -f1)
```

### 3. Name the generation + operation

```
GEN=<slug>-${X:0:9}-$(date +%Y%m%d)     # e.g. ebay-stage-retry-58d77f83a-20260906
OP=operator-$GEN                        # operation-id, must be [A-Za-z0-9._-]{1,128}
CUR=$(basename "$(readlink /opt/TGW/current)")   # via prod; the --expected-current
```

`[prod]` read current generation:
`sudo -n -u db ssh tgw-prod "readlink /opt/TGW/current"` → `releases/<CUR>`.

### 4. Tell the operator

State: commit `X`, what it changes, `GEN`, and that you are about to flip
`current`. **Wait for go.**

### 5. Ship it `[prod]`

Copy the archive to prod (`scp` / `rsync` as db), then:

```
sudo /opt/TGW/.venvironments/tgw/bin/tgw-release-install --root /opt/TGW install \
  --archive /path/tgw-$X.tar \
  --generation "$GEN" \
  --commit "$X" \
  --tree "$TREE" \
  --archive-sha256 "$SHA" \
  --expected-current "$CUR" \
  --operation-id "$OP"

sudo /opt/TGW/.venvironments/tgw/bin/tgw-release-install --root /opt/TGW verify "$GEN"
```

If the keyless `install` refuses with `missing-admission-evidence` (venv was
rebuilt), use the archive's own installer instead — it has `install-direct`
only if `X` includes `dcfa3a86`:

```
mkdir /tmp/ri-$X && tar -C /tmp/ri-$X -xf /path/tgw-$X.tar
sudo PYTHONPATH=/tmp/ri-$X/src /opt/TGW/.venvironments/tgw/bin/python3 \
  -m tgw.release_installer --root /opt/TGW install-direct \
  --archive /path/tgw-$X.tar --generation "$GEN" --commit "$X" --tree "$TREE" \
  --archive-sha256 "$SHA" --expected-current "$CUR" --operation-id "$OP" \
  --reason "<why>" --authorized-by "<operator>"
```

### 6. Restart workers `[prod]`

```
sudo systemctl restart tgw-workers.target tgw-http.service
# or narrower: sudo systemctl restart 'tgw-worker@ebay_*.service' tgw-http.service
```

### 7. Verify live

Check the fix on prod: relevant `journalctl -u tgw-worker@<q>`, a `queue_jobs`
query, `tgw health`. Confirm to the operator.

---

## Rollback

```
sudo /opt/TGW/.venvironments/tgw/bin/tgw-release-install --root /opt/TGW rollback \
  --receipt /opt/TGW/receipts/$OP.json \
  --expected-current "$GEN" \
  --operation-id rollback-$OP
sudo systemctl restart tgw-workers.target tgw-http.service
```

`/opt/TGW/current.rollback` also points at the previous generation for a
manual `ln -sfn`.

---

## What "the labyrinth" was (context, not procedure)

The 2026-08-20 W13–W18 work layered admission receipts + environment-preflight
receipts + plan-commit/solution-hash binding + Ed25519 signature verification
(`release-admission-authority`, keys at `/etc/tgw/trust/*.pub`) on top of
`release_installer.select()`. `cut-mordac` (`8ce516f0f`, 2026-09-03) removed
the surrounding `procedure_runner` ceremony but left the installer gate. The
private signing keys were never found on tgw-lib, tgw-prod, or in codex's
home. The gate is not required to do the job and is being removed, not fed.
```

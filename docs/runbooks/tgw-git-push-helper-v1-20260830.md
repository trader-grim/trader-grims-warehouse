# tgw-git-push — narrow source-publication helper (v1, 2026-08-30)

Draft artifact for review. Kills the "copy-paste bridge" on the push side: any
tgw-coders harness pushes its own branch with one command, as `tgw-release`
(which owns the GitHub agent). Private key stays ONLY in the agent — never on
an actor account (same rule as the deployment-approval key).

## Install (operator, one time)

1. Copy to `/usr/local/sbin/tgw-git-push` (root:root, 0755) on tgw-lib.
2. `/etc/sudoers.d/tgw-git-push`:

   ```
   %tgw-coders ALL=(tgw-release) NOPASSWD: /usr/local/sbin/tgw-git-push
   ```

   Names the script and the role group — never actor names.

## Script (`/usr/local/sbin/tgw-git-push`)

```bash
#!/usr/bin/env bash
# stdlib-only; runs as tgw-release (sudoers pins the identity).
set -euo pipefail
if [ "$(id -u)" -ne "$(id -u tgw-release)" ] || [ "$(id -un)" != "tgw-release" ]; then
  echo "tgw-git-push: must run as tgw-release" >&2; exit 3
fi
[ "$#" -eq 2 ] || { echo "usage: tgw-git-push <repo> <branch>" >&2; exit 2; }
repo="$1"; branch="$2"
case "$repo" in
  /opt/TGW/var/worktrees/*|/opt/TGW/tgw-lib/src/trader-grims-warehouse|/opt/TGW/tgw-lib/src/tgw-flake) ;;
  *) echo "tgw-git-push: repo not allowed: $repo" >&2; exit 3 ;;
esac
case "$branch" in
  coding/*|todo/*|integrate/*|fix/*|w[0-9]*/*) ;;
  *) echo "tgw-git-push: branch not allowed: $branch" >&2; exit 3 ;;
esac
[ -d "$repo/.git" ] || { echo "tgw-git-push: not a repo: $repo" >&2; exit 3; }
git -C "$repo" rev-parse --verify "refs/heads/$branch" >/dev/null 2>&1 || { echo "tgw-git-push: no such branch: $branch" >&2; exit 3; }
before="$(git -C "$repo" rev-parse "$branch")"
out="$(git -C "$repo" push -u origin "$branch" 2>&1)" || { echo "$out" >&2; exit 1; }
after="$(git -C "$repo" rev-parse "$branch")"
mkdir -p /opt/TGW/var/git-push-receipts
receipt="/opt/TGW/var/git-push-receipts/$(date -u +%Y%m%dT%H%M%SZ)-$(basename "$repo" | cut -c1-24).json"
printf '{"schema":"tgw-git-push-receipt/v1","actor":"%s","repo":"%s","branch":"%s","commit_before":"%s","commit_after":"%s","ts":"%s"}\n' \
  "${SUDO_USER:-tgw-release}" "$repo" "$branch" "$before" "$after" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$receipt"
chmod 0640 "$receipt"
echo "tgw-git-push: pushed $branch ($after) — receipt $receipt"
```

## Usage (any tgw-coders harness, e.g. the deepseek session)

```bash
sudo -u tgw-release /usr/local/sbin/tgw-git-push \
  /opt/TGW/var/worktrees/todo-1931-item-category coding/codex/todo-1931-item-category
```

Receipts at `/opt/TGW/var/git-push-receipts/` (tgw-git-push-receipt/v1): role,
repo, branch, before/after commit — the audit trail the operator wanted
("final commit/push automatic, commits never pile up").

## Refusals (by design)

- Wrong identity, repo, or branch → exit 3, nothing pushed.
- A push error (auth, non-fast-forward) → exit 1 with stderr, no receipt.
- `main` / `production` excluded — deliberate; canonical pushes stay a separate,
  operator-visible decision (or a future explicit allowlist extension).

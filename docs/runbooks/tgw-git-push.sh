#!/usr/bin/env bash
# tgw-git-push v1 (2026-08-30) — narrow source-publication helper.
# Runs as tgw-release (sudoers pins the identity). stdlib-only.
set -euo pipefail
[ "$(id -un)" = "tgw-release" ] || { echo "tgw-git-push: must run as tgw-release" >&2; exit 3; }
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
export SSH_AUTH_SOCK=/run/tgw-github-agent/agent.sock
export HOME=/var/lib/tgw-release
[ -d "$repo/.git" ] || [ -f "$repo/.git" ] || { echo "tgw-git-push: not a repo: $repo" >&2; exit 3; }
git -c safe.directory="$repo" -C "$repo" rev-parse --verify "refs/heads/$branch" >/dev/null 2>&1 || { echo "tgw-git-push: no such branch: $branch" >&2; exit 3; }
before="$(git -c safe.directory="$repo" -C "$repo" rev-parse "$branch")"
out="$(git -c safe.directory="$repo" -C "$repo" push -u origin "$branch" 2>&1)" || { echo "$out" >&2; exit 1; }
after="$(git -c safe.directory="$repo" -C "$repo" rev-parse "$branch")"
mkdir -p /opt/TGW/var/git-push-receipts
receipt="/opt/TGW/var/git-push-receipts/$(date -u +%Y%m%dT%H%M%SZ)-$(basename "$repo" | cut -c1-24).json"
printf '{"schema":"tgw-git-push-receipt/v1","actor":"%s","repo":"%s","branch":"%s","commit_before":"%s","commit_after":"%s","ts":"%s"}\n' \
  "${SUDO_USER:-tgw-release}" "$repo" "$branch" "$before" "$after" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$receipt"
chmod 0640 "$receipt"
echo "tgw-git-push: pushed $branch ($after) — receipt $receipt"

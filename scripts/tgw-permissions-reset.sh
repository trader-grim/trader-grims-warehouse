#!/usr/bin/env bash
#
# tgw-permissions-reset.sh — reset / repair / audit TGW filesystem permissions.
#
# What's new vs the original (session 15):
#   * SECRETS section — /opt/TGW/secrets is now handled: dir 0700, files 0600.
#     (The original never touched secrets, which let a credential drift to 0664.)
#   * --check  — read-only audit: report any permission drift and exit 1 if found.
#                Runs as any user. This is the "what's wrong?" companion to the fix.
#   * Non-root chmod fallback — if not root, fix modes (chmod) and skip ownership
#     (chown), since the owner can repair mode drift without sudo. The common mess
#     (a file going group/world-readable) is fixed by a plain `tgw` run.
#   * --data   — opt-in deep sweep of /opt/TGW/data (55K+ item dirs; slow), left
#                out of the default run so the everyday repair stays quick.
#
# Policy (updated — never world-readable; db user in tgw group needs write on src/bin/docs):
#   App trees (src, bin, docs): dirs 2770, files 0660; bin scripts 0750
#   Config (config):           dirs 2750, files 0640   (group read-only; workers don't write)
#   Writable (var, backups):   dirs 2770, files 0660
#   Secrets (secrets):         dir  0700, files 0600   <-- security-critical
#   Data (data, with --data):  dirs 2775, files 0644   (public catalog, not secret)
#
set -euo pipefail

TGW_ROOT="/opt/TGW"
OWNER="tgw"
GROUP="tgw"
SECRETS_DIR=""
BACKUP_ROOT="/var/backups/trader_grims_warehouse"
LOG_ROOT="/opt/TGW/var/log"
USE_ACL=0
RESET_ACL=0
DRY_RUN=0
VERBOSE=0
CHECK=0
DEEP_DATA=0

usage() {
  cat <<'EOF'
Usage: tgw-permissions-reset.sh [options]

Modes:
  --check               Audit only — report permission drift, change nothing,
                        exit 1 if any drift is found (good for `tgw health`/cron).
  --dry-run             Show the commands an apply run would execute.
  (default)             Apply the permission policy (repair).

Options:
  --root PATH           TGW root, default: /opt/TGW
  --owner USER          Owner, default: tgw
  --group GROUP         Group, default: tgw
  --secrets-dir PATH    Secrets dir, default: <root>/secrets
  --backup-root PATH    Backup root, default: /var/backups/trader_grims_warehouse
  --log-root PATH       Log root, default: /opt/TGW/var/log
  --data                Also deep-sweep <root>/data (slow; 55K+ item dirs)
  --use-acl             Apply default ACLs on writable trees
  --reset-acl           Remove existing ACLs before applying new defaults
  --verbose             Print each command
  --help                Show this help

Notes:
  Ownership (chown) needs root. Mode fixes (chmod) of files you already own do
  not — so a plain `tgw` run repairs the common case (a secret/file going
  group- or world-readable) without sudo.
EOF
}

run() {
  if [[ "$VERBOSE" -eq 1 || "$DRY_RUN" -eq 1 ]]; then
    printf '+'; for arg in "$@"; do printf ' %q' "$arg"; done; printf '\n'
  fi
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

DRIFT=0
report_drift() { echo "DRIFT: $*" >&2; DRIFT=1; }

# Octal mode of a path, no leading zeros (e.g. "700", "2700", "664").
modeof() { stat -c '%a' "$1"; }

# True if a file mode grants any group/other access (the real secret-leak test).
group_or_other_accessible() {
  local m="$1"
  (( (8#$m) & 8#077 ))
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) TGW_ROOT="$2"; shift 2 ;;
    --owner) OWNER="$2"; shift 2 ;;
    --group) GROUP="$2"; shift 2 ;;
    --secrets-dir) SECRETS_DIR="$2"; shift 2 ;;
    --backup-root) BACKUP_ROOT="$2"; shift 2 ;;
    --log-root) LOG_ROOT="$2"; shift 2 ;;
    --data) DEEP_DATA=1; shift ;;
    --use-acl) USE_ACL=1; shift ;;
    --reset-acl) RESET_ACL=1; shift ;;
    --check) CHECK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --verbose) VERBOSE=1; shift ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

need_cmd chmod
need_cmd find
need_cmd stat
if [[ "$CHECK" -eq 0 ]]; then
  need_cmd install
fi
if [[ "$USE_ACL" -eq 1 || "$RESET_ACL" -eq 1 ]]; then
  need_cmd setfacl
fi

CAN_CHOWN=0
[[ $EUID -eq 0 ]] && CAN_CHOWN=1
if [[ "$CHECK" -eq 0 && "$CAN_CHOWN" -eq 0 ]]; then
  echo "Note: not root — fixing modes (chmod) only, skipping ownership (chown)." >&2
fi

SRC_ROOT="$TGW_ROOT/src"
BIN_ROOT="$TGW_ROOT/bin"
DOCS_ROOT="$TGW_ROOT/docs"
CONFIG_ROOT="$TGW_ROOT/config"
VAR_ROOT="$TGW_ROOT/var"
DATA_ROOT="$TGW_ROOT/data"
SECRETS_DIR="${SECRETS_DIR:-$TGW_ROOT/secrets}"

# ---------------------------------------------------------------------------
# CHECK mode — audit only, no changes
# ---------------------------------------------------------------------------
if [[ "$CHECK" -eq 1 ]]; then
  echo "Auditing TGW permissions under $TGW_ROOT ..."

  # Secrets are the security-critical surface: dir must deny group/other,
  # files must be owner-only (no group/other access at all).
  if [[ -d "$SECRETS_DIR" ]]; then
    dmode="$(modeof "$SECRETS_DIR")"
    if group_or_other_accessible "$dmode"; then
      report_drift "$SECRETS_DIR mode $dmode is group/other-accessible (want 0700)"
    fi
    while IFS= read -r f; do
      fmode="$(modeof "$f")"
      if group_or_other_accessible "$fmode"; then
        report_drift "$f mode $fmode is group/other-accessible (want 0600)"
      fi
    done < <(find "$SECRETS_DIR" -maxdepth 1 -type f)
  fi

  # Flag files that are TOO OPEN, never stricter-than-policy (owner-only source
  # is fine — do not false-positive on it). World-writable is always wrong;
  # config must not be world-readable (it carries policy IDs).
  audit_world_writable() { # <dir> <label>
    [[ -d "$1" ]] || return 0
    local n
    n="$(find "$1" -type f -perm -0002 2>/dev/null | wc -l)"
    if [[ "$n" -gt 0 ]]; then
      report_drift "$1: $n world-writable file(s) ($2)"
    fi
    return 0
  }
  # Fast surface by default: bin + config (the small control/credential trees).
  # src and var are large (source tree, logs, backups) — only scanned with --data
  # so the everyday audit stays quick.
  audit_world_writable "$BIN_ROOT" "world-writable"
  audit_world_writable "$CONFIG_ROOT" "world-writable"
  if [[ "$DEEP_DATA" -eq 1 ]]; then
    audit_world_writable "$SRC_ROOT" "world-writable"
    audit_world_writable "$VAR_ROOT" "world-writable"
    audit_world_writable "$DATA_ROOT" "world-writable"
  fi
  if [[ -d "$CONFIG_ROOT" ]]; then
    nwr="$(find "$CONFIG_ROOT" -maxdepth 2 -type f -perm -0004 2>/dev/null | wc -l)"
    if [[ "$nwr" -gt 0 ]]; then
      report_drift "$CONFIG_ROOT: $nwr world-readable config file(s)"
    fi
  fi

  if [[ "$DRIFT" -eq 0 ]]; then
    echo "OK — no permission drift found."
    exit 0
  fi
  echo "Permission drift found (run without --check to repair)." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# APPLY mode — repair ownership + permissions
# ---------------------------------------------------------------------------
chown_r() { [[ "$CAN_CHOWN" -eq 1 && -e "$1" ]] && run chown -R "$OWNER:$GROUP" "$1"; }

# Directory skeleton (install -d is a no-op if present, but normalizes mode).
if [[ "$CAN_CHOWN" -eq 1 ]]; then
  run install -d -m 2750 -o "$OWNER" -g "$GROUP" "$TGW_ROOT"
  run install -d -m 2770 -o "$OWNER" -g "$GROUP" "$SRC_ROOT"
  run install -d -m 2770 -o "$OWNER" -g "$GROUP" "$BIN_ROOT"
  run install -d -m 2770 -o "$OWNER" -g "$GROUP" "$DOCS_ROOT"
  run install -d -m 2750 -o "$OWNER" -g "$GROUP" "$CONFIG_ROOT"
  run install -d -m 2770 -o "$OWNER" -g "$GROUP" "$VAR_ROOT"
  run install -d -m 2770 -o "$OWNER" -g "$GROUP" "$LOG_ROOT"
  run install -d -m 2770 -o "$OWNER" -g "$GROUP" "$BACKUP_ROOT"
fi

for path in "$SRC_ROOT" "$BIN_ROOT" "$DOCS_ROOT" "$CONFIG_ROOT" "$VAR_ROOT" "$BACKUP_ROOT"; do
  chown_r "$path"
done

if [[ -d "$SRC_ROOT" ]]; then
  run find "$SRC_ROOT" -type d -not -path '*/.git/*' -exec chmod 2770 {} +
  run find "$SRC_ROOT" -type f -not -path '*/.git/*' -exec chmod 0660 {} +
  # Flutter SDK — all scripts/binaries need execute bit preserved.
  FLUTTER_SDK="$SRC_ROOT/trader-grims-warehouse/flutter"
  if [[ -d "$FLUTTER_SDK" ]]; then
    find "$FLUTTER_SDK/bin" -type f -exec chmod 0750 {} +
    find "$FLUTTER_SDK" -name "*.sh" -exec chmod 0750 {} +
  fi

  # Flutter Linux bundle — binary and shared libs need execute bit.
  FLUTTER_BUNDLE="$SRC_ROOT/trader-grims-warehouse/apps/tgw_app/build/linux/x64/release/bundle"
  if [[ -d "$FLUTTER_BUNDLE" ]]; then
    [[ -f "$FLUTTER_BUNDLE/tgw_app" ]] && run chmod 0750 "$FLUTTER_BUNDLE/tgw_app"
    find "$FLUTTER_BUNDLE/lib" -name "*.so*" 2>/dev/null | while read -r f; do
      run chmod 0750 "$f"
    done
  fi
fi

if [[ -d "$BIN_ROOT" ]]; then
  run find "$BIN_ROOT" -type d -exec chmod 2770 {} +
  run find "$BIN_ROOT" -type f -exec chmod 0660 {} +
  run find "$BIN_ROOT" -type f \( -name '*.sh' -o -name '*.bash' -o -name '*.py' -o -name '*.pl' \) -exec chmod 0750 {} +
fi

if [[ -d "$DOCS_ROOT" ]]; then
  run find "$DOCS_ROOT" -type d -exec chmod 2770 {} +
  run find "$DOCS_ROOT" -type f -exec chmod 0660 {} +
fi

if [[ -d "$CONFIG_ROOT" ]]; then
  run find "$CONFIG_ROOT" -type d -exec chmod 2750 {} +
  run find "$CONFIG_ROOT" -type f -exec chmod 0640 {} +
fi

if [[ -d "$VAR_ROOT" ]]; then
  run find "$VAR_ROOT" -type d -exec chmod 2770 {} +
  run find "$VAR_ROOT" -type f -exec chmod 0660 {} +
fi

if [[ -d "$BACKUP_ROOT" ]]; then
  run find "$BACKUP_ROOT" -type d -exec chmod 2770 {} +
  run find "$BACKUP_ROOT" -type f -exec chmod 0660 {} +
fi

# Secrets — the security-critical fix. Owner-only dir + files; no group/other.
if [[ -d "$SECRETS_DIR" ]]; then
  chown_r "$SECRETS_DIR"
  run chmod 0700 "$SECRETS_DIR"
  run find "$SECRETS_DIR" -maxdepth 1 -type f -exec chmod 0600 {} +
fi

# Data tree — opt-in (large). Items are world-readable catalog data, not secrets.
if [[ "$DEEP_DATA" -eq 1 && -d "$DATA_ROOT" ]]; then
  chown_r "$DATA_ROOT"
  run find "$DATA_ROOT" -type d -exec chmod 2775 {} +
  run find "$DATA_ROOT" -type f -exec chmod 0644 {} +
fi

if [[ "$RESET_ACL" -eq 1 ]]; then
  for path in "$LOG_ROOT" "$BACKUP_ROOT"; do
    [[ -d "$path" ]] && { run setfacl -Rb "$path"; run setfacl -Rk "$path"; }
  done
fi

if [[ "$USE_ACL" -eq 1 ]]; then
  for path in "$LOG_ROOT" "$BACKUP_ROOT"; do
    [[ -d "$path" ]] && {
      run setfacl -R -m "u::rwx,g::rwx,o::---" "$path"
      run setfacl -R -d -m "u::rwx,g::rwx,o::---" "$path"
    }
  done
fi

cat <<EOF
Permission reset complete.
Secrets normalized: $SECRETS_DIR (dir 0700, files 0600)
Recommended shell umask for TGW maintenance: 0027
Recommended systemd service UMask for writable TGW services: 0007
Tip: run 'tgw-permissions-reset.sh --check' any time to audit for drift.
EOF

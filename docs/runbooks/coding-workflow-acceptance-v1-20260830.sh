#!/usr/bin/env bash
# coding-workflow acceptance card v1 (2026-08-30, operator acceptance model).
# Functional acceptance for non-operator-facing coding-workflow components IS
# the component's live verification. This card is the live check — runnable by
# EACH supported harness (deepseek, codex, claude, hermes, ...) from a fresh
# ordinary tgw-coders session. Operator readback only confirms the card passed.
#
# Sequence (operator-specified): fresh session -> onboarding/Context ->
# tgw coding access-status -> tgw coding status -> bounded start/resume or
# Doctor checks -> verify durable receipts and clean recovery.
set -uo pipefail
PASS=0; FAIL=0
check() { # check <name> <rc> <detail>
  if [ "$2" -eq 0 ]; then PASS=$((PASS+1)); echo "PASS: $1"
  else FAIL=$((FAIL+1)); echo "FAIL: $1 -- ${3:-}"; fi
}
actor="$(id -un)"
echo "== coding-workflow acceptance card v1 -- actor=$actor ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
# 1. fresh-session onboarding / Context inputs are present and current
check "context-input current-task.json" "$(test -f /opt/TGW/tgw-lib/context-input/current-task.json; echo $?)" "missing"
check "plan-cycle cursor" "$(test -f /opt/TGW/tgw-lib/context-input/plan-cycle-cursor.json; echo $?)" "missing"
# 2. tgw coding access-status -- proves the local Unix/group binding
acc="$(tgw coding access-status 2>/dev/null)"
check "access-status" "$(echo "$acc" | grep -q '"actor"'; echo $?)" "$acc"
echo "$acc" | grep -q "$actor" && check "access-status actor=$actor" 0 "" || check "access-status actor=$actor" 1 "unexpected actor"
# 3. tgw coding status -- lifecycle surface readable
check "status surface" "$(tgw coding status 1930 >/dev/null 2>&1; echo $?)" "lifecycle store unreadable"
# 4. bounded start/resume or Doctor checks
doc="$(tgw doctor check --json 2>/dev/null || tgw doctor check 2>/dev/null)"
check "doctor check" "$(echo "$doc" | grep -qiE 'ok|pass|healthy|0 failed'; echo $?)" "$(echo "$doc" | head -c 200)"
# 5. durable receipts -- a completed lifecycle's journal + root-effect response
check "durable journal (1930)" "$(test -f /opt/TGW/var/tgw-coders/coding-lifecycles/579955542b0f69561d97a87208194428f059fcc2f02538c936e8ac3dda7852e2.json; echo $?)" "missing"
check "root-effect response (1930)" "$(test -f /opt/TGW/var/tgw-coders/coding-root-effects/579955542b0f69561d97a87208194428f059fcc2f02538c936e8ac3dda7852e2.response.json; echo $?)" "missing"
# 6. clean recovery -- the refusal mechanism + service stability
check "refusal recovery artifact" "$(test -f /opt/TGW/var/tgw-coders/coding-root-effects/5d3787f895a5b1a8d7a340e15b2970b674ec4db3bbe04974c749580128f98ba5.request.refusal.json; echo $?)" "missing"
check "root-effect service active" "$(systemctl is-active --quiet tgw-coding-root-effect.service; echo $?)" "not active"
check "supervisor service active" "$(systemctl is-active --quiet tgw-coding-lifecycle-supervisor.service; echo $?)" "not active"
echo "== ACCEPTANCE: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

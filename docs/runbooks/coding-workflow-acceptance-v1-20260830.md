# Coding-workflow acceptance card — v1 (2026-08-30)

## Model (operator policy, ACCEPTANCE_MODEL on #1916)

- Non-operator-facing components: **functional acceptance = the component's live
  verification**, not a special reviewer.
- `tgw-review` = independent diagnostic review of the exact source candidate.
- Focused tests / Doctor = functional verification of the installed component
  (CLI, database binding, workers, services, cold-session MCP, restart/recovery).
- Operator acceptance applies **only** where there is an operator-facing outcome;
  for infrastructure it is an explicit operator readback that the stated live
  checks passed — not a UI test.

## The card

`docs/runbooks/coding-workflow-acceptance-v1-20260830.sh` — runnable by **each
supported harness** (deepseek, codex, claude, hermes, ...) from a fresh ordinary
`tgw-coders` session. It checks, in the operator-specified order:

1. Fresh-session onboarding / Context inputs (current-task, plan-cycle cursor)
2. `tgw coding access-status` — local Unix/group binding, actor identity
3. `tgw coding status` — lifecycle surface readable
4. Doctor checks — installed-component functional verification
5. Durable receipts — a completed lifecycle's journal + root-effect response
6. Clean recovery — refusal mechanism artifact + root-effect/supervisor active

## Usage (any harness)

```bash
bash docs/runbooks/coding-workflow-acceptance-v1-20260830.sh
# expect: ACCEPTANCE: N passed, 0 failed  (exit 0)
```

First run (deepseek harness, 2026-08-30): **11 passed, 0 failed**.

## Operator readback

After the card passes, the operator records readback for the round:

```bash
tgw coding readback coding:<root_id>    # from the operator_notification receipt
```

Readback is confirmation only — the design never lets the ordinary surface
record acceptance itself (`record_operator_readback` keeps acceptance PENDING;
the operator console surface owns acceptance per PP-OPERATORCONSOLE).

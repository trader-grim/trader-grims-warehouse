# RESEARCH-1143: apis/ebay/ subsystem cohesion+correctness audit

Part of todo #1143 (full-codebase cohesion+correctness audit, staged per-subsystem).
This slice: `src/tgw/apis/ebay/`, 11 files / 2,219 lines. Second subsystem after `workers/`
(see RESEARCH-1143-workers-audit.md).

## Method

Workflow tool, 4 file-groups run in parallel (Find phase), each candidate finding then
adversarially verified by 3 independent agents against the real code (Verify phase,
2-of-3 survival bar to confirm). 37 agents total, ~1.35M subagent tokens, ~7 minutes wall.

- Group A-core: client.py, catalog.py, promotions.py
- Group B-auth: get_access_token.py, refresh_access_token.py, notifications.py
- Group C-aspects: specifics.py, conditions.py
- Group D-taxonomy-trading: taxonomy.py, trading.py

## Result: 11/11 candidate findings confirmed, 0 refuted

All 11 findings survived all 3 adversarial refutation attempts (unanimous, not just
2-of-3) — unusually clean pass. Filed as individual todos:

| Todo | Severity | File:line | Summary |
|------|----------|-----------|---------|
| #1174 | **security** | notifications.py | `verify_notification_signature()` fails OPEN on missing header/signature/parse-exception on the public unauthenticated webhook — attacker can forge a sold-notification for any known listing_id to corrupt inventory |
| #1173 | invariant | catalog.py:61 | `lookup_epid` swallows `QuotaBudgetExceeded` via bare except, defeats quota-halt/requeue pattern |
| #1175 | correctness | get_access_token.py:119 | imports nonexistent module `tgw_ebay_token_manager_refresh_access_token_v1` — auto-refresh silently falls through to manual browser OAuth |
| #1177 | invariant | refresh_access_token.py + get_access_token.py | `save_token_state()` non-atomic write of the sole ebay-token.json, no backup |
| #1178 | correctness | conditions.py:280 | `best_condition_for_enum` uses MIN (best) rank across an enum's source conditionIds — can silently upgrade item condition on category change |
| #1179 | invariant | specifics.py:190 | `get_aspects()` disk-cache read-modify-write unlocked+non-atomic, shared across 3 processes — races drop cached entries |
| #1180 | invariant | taxonomy.py:86 | category tree caches written via plain `write_text`, no atomic tmp+rename/flock |
| #1176 | cohesion | get_access_token.py | `load_config()` never applies `sandbox_` credential prefix — sandbox runs silently auth with prod credentials |
| #1181 | correctness | taxonomy.py:117 | `best_category()` doesn't catch per-query exceptions — first-query failure aborts the whole fallback chain |
| #1182 | cohesion (batched) | conditions.py:177, trading.py:445 | no memoization on 2.7MB policy file re-read every call; 429-retry logic missing from sibling paginated generators |

**Priority note:** #1174 is a real security gap on a production-facing webhook (inventory
corruption via unsigned forged notification) — filed p5, above the rest of this batch.

## Remaining subsystems queue

http_server.py, queue/state-machine, scripts/, nix flake.

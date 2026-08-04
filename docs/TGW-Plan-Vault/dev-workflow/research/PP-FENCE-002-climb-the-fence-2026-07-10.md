# PP-FENCE-002 (proposed): "Don't climb the fence, use the gate"

**Source:** full-codebase cohesion audit 2026-07-10 (workflow review, 6 subsystems x 3
dimensions, adversarial-verified). 54 candidates, 49 confirmed, 45 filed as todos
#1273-#1317 (source=cohesion-audit-2026-07-10). See memory
`project-cohesion-audit-2026-07-10.md` for the full run record.

## The core problem

The audit's single biggest pattern, across nearly every subsystem, was code that
reaches around `tgw-api`/`items.py` instead of through it: constructing ItemData paths
inline, calling `atomic_write_json()` directly without `archive_root`, or writing media
files with plain `shutil.copy2` instead of the fence's atomic-write + archive pattern.
This isn't one bug, it's a structural habit that keeps recurring in new code — the fence
exists, but nothing stops a new writer from climbing over it instead of going through
the gate.

Dave's framing (2026-07-10): "Maybe we need an explicit don't climb the fence, use the
gate" — i.e. this needs to become an enforced rule, not a documented convention.

## What's already in invariants.md (existing gap classes, audit found new instances of)

| Invariant | Current status | New offenders found this pass |
|---|---|---|
| A4 — paths only via `config.sku_dir/sku_json/location_dir` | ⚠️ known gap, only 3 offenders previously named | `ready.py`, `mcp_server.py` (`tgw_get_item`/`tgw_enqueue`), `http_server.py create_item_endpoint` |
| E5 — no delete/overwrite without archiving first | ❌ gap, doc says "not exhaustively audited" | 7+ `api.py` CLI commands, `revision.py`, `scrub.py`'s 3 bulk passes, `migrate-unblock`/`migrate-restore` |
| A8 — media writes need archive-before-modify | ⚠️ gap, only alt-text implements it | `photo_history_recovery.py ensure_copy()` |
| C11 — skip/guard is a durable finding, not a log line | ✅ enforced on some paths | `ebay/pull.py` orphaned-listing discard, `ebay_upload.py` no-photos skip, `multi_intake.py` collision guard, `offers.py` unresolvable-SKU drop |
| E9 — one-off scripts announce themselves | ⚠️ partial | `photo_history_recovery.py` |
| E2 — secrets, single facility | ✅ enforced 2026-07-09 | `aider_mcp_server.py`, `health.py` quota check still read pre-migration paths (regression, not a new gap) |

Both A4 and E5's own "how to test" sections already proposed the fix: **a CI grep audit**
(`itemdata_root.*\.json` outside `config.py`/`items.py`; `atomic_write_json` without
`archive_root`) — never implemented. This pass is the second time the same gap class has
surfaced (first was the initial 2026-06-10 review, then audit#1143, now this).

## Net-new territory (not covered by any current invariant letter)

- **Path-input validation / traversal** — `config.py`'s `sku_dir()`/`sku_json()`/
  `location_dir()` do raw path-join with zero validation; `http_server.py` PATCH and
  `sku_migration.py rename_sku()` both build filesystem paths from unsanitized
  network/item-field input (`location`). Stricter than A4 (A4 is "who owns the path
  formula," this is "is the input to that formula ever checked").
- **Untrusted content reaching a live external write unescaped** — LLM/lookup text into
  eBay listing HTML (`ebay/description.py`), Trading API XML built via raw f-string
  interpolation (`apis/ebay/trading.py`), item fields into `readiness_html()` — no
  invariant currently covers "content leaving the system must be escaped for its
  target format."
- **Auth/secrets hygiene outside E2's scope** — non-constant-time bearer-token compare
  in `http_server.py`; clipboard-history SQLite db (plaintext, default 644 perms, no
  TTL) in `clip.py`/`clipd.py`.

## Proposed for the planning session

1. **New invariant A9** (or fold into A4): all ItemData path construction AND all
   path-bearing item fields (`location`, filenames) must be validated/sanitized before
   use — not just "which module owns the formula" but "is the input trusted."
2. **New invariant F1**: untrusted/derived content (LLM output, lookup API responses,
   operator-entered item fields) must never reach a live external write (eBay XML/HTML,
   rendered HTML) without format-appropriate escaping.
3. **Actually build the CI grep audit** A4/E5 already specified in 2026-06-10 and never
   implemented — this is the third audit pass to rediscover the same gap class by hand.
   A pre-commit or CI check would catch new offenders at write time instead of at the
   next full-codebase audit.
4. Triage the 45 todos (#1273-#1317) into a work track — security-tagged ones (p35) are
   the ones with real exploit shape (SSRF, injection, traversal); correctness ones (p40)
   are mostly isolated bugs; invariant ones (p45) are individually small but are the
   evidence base for #1-3 above.

Not incorporated into the master plan yet — dropped here per the inbox workflow for the
next planning pass to process (PP number to be confirmed/assigned then; using
PP-FENCE-002 as a placeholder since PP-FENCE-001 already exists per E5's history).

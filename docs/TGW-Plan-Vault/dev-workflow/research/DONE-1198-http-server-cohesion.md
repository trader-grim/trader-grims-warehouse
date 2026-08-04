# DONE — todo #1198: http_server.py cohesion findings (audit#1143)

## Shipped

All 4 findings in `src/tgw/http_server.py`:

1. **`set_photo_order` (was :2167, now :2262)** — replaced the inline
   duplicated `state_machine.enqueue_job(...)` block with a call to the
   existing `_enqueue_catalog_rebuild(f"photo_order:{sku}")` helper (same
   helper every other write path already uses).

2. **`get_hint_trail` (:2369) / `intake_form` (:3029)** — both built the
   ItemData path from a raw `sku` path param with no `'..'` traversal guard,
   unlike sibling media routes (`get_media`, `get_thumb_noauth` both check
   `if ".." in sku`). Added the same guard to both, returning 400 before any
   filesystem access.

3. **Store-category dropdown build (was :4922, now ~:4978)** — removed a
   genuinely dead no-op `.replace(x, x)` block that did nothing before being
   immediately overwritten, and replaced the fragile pattern where the
   secondary dropdown's build silently depended on `_sc_list` surviving out
   of the primary dropdown's separate `try/except` (a real bug upstream
   there would have thrown `NameError`, swallowed by the second `except
   Exception: pass`, and silently produced an empty secondary dropdown).
   Consolidated into one `_sc_list` build + one `_store_cat_options_html()`
   helper used for both dropdowns. While fixing this I found the primary
   lookup's `_cg_path_sc.exists()` call assumed `category_groups_path` was
   already a `Path` — sibling routes (`list_category_groups`, `intake_form`)
   wrap it in `Path(...)` first; this block didn't, so the whole lookup was
   silently no-op'ing whenever the value was a plain string. Fixed to match
   the sibling pattern — same "fragile silent-fail" finding class, not a
   separate bug, so fixed in scope rather than filed separately.

4. **`_safe_price` / `_fmt_price` (:4263)** — confirmed byte-identical
   (both nested closures inside `_render_item_detail_html`). Removed
   `_fmt_price` and repointed its 3 call sites at `_safe_price`.

## Live evidence

- `pytest -q tests/test_http_server.py` — 256 passed (was 250 before; added
  6 new tests below).
- `pytest -q` (full suite, as `db` — same `tgw`-user `nix`-symlink
  permission issue as #1182, pre-existing/unrelated) — 2029 passed, 1
  skipped, 2 pre-existing failures in `tests/test_invariants_pricing.py`
  (unrelated `ebay_price.py` bug, confirmed present before this change too).
- `ruff check src/tgw/http_server.py` — all checks passed.
- `tgw health` — 3 pre-existing unrelated failures (`backups`, `nats`,
  `ebay_sync_fallback`); nothing new introduced by this change.
- New tests added:
  - `test_photo_order_enqueues_via_shared_helper` — asserts exactly one
    `enqueue_job` call via the shared helper, correct dedupe key/reason.
  - `test_hint_trail_returns_history` / `test_hint_trail_rejects_path_traversal_sku`
  - `test_intake_form_rejects_path_traversal_sku`
  - `test_item_detail_store_category_dropdowns_populate_and_select` — writes
    a real category-groups.json + draft_listing with both primary and
    secondary store_category_id set, confirms both dropdowns populate and
    mark the correct option `selected`. This test caught the `Path(...)`
    wrap bug above — it failed until that was fixed, i.e. it's a real
    regression guard, not just a smoke test.

## Note

`tgw-http.service` is live and running (port 7373) but was NOT restarted —
this is a code change to a running production HTTP service, which I'm
flagging rather than restarting unprompted. Restart when convenient:
`systemctl restart tgw-http.service`.

## Out of scope (not fixed, noted only)

`_cfmt` (currently ~:4400, inside the same `_render_item_detail_html`
function) is a third functionally-identical price formatter (same
try/float/except pattern) that the packet didn't name. Left alone to stay
in scope; worth folding into `_safe_price` too in a future pass if that
function ever gets touched again.

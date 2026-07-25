# Independent review — 2026-07-25 source fixes and reconciliation boundary

**Reviewer:** Hermes/Tigwa (independent of the original Claude implementation)
**Status:** evidence-backed review finding; no source mutation performed

## Scope checked

- Current tgw-prod working tree at `0fe6da0b1fd444a0059ef31bcca89dca4a38c8fe`.
- Yesterday-visible source/test changes in `src/tgw/ebay/upload.py`, `src/tgw/http_server.py`, and their focused tests.
- Plan Vault deletion/relocation evidence.

## Critical — item detail can crash on mixed timestamp forms

`src/tgw/http_server.py` adds `_superseded_by_success()`, which compares terminal timestamps using Python `datetime` objects. It accepts both offset-aware timestamps (for example `2026-07-25T00:29:18+00:00`) and offset-naive timestamps (for example `2026-07-25T00:32:25`) but compares them directly.

Python raises `TypeError: can't compare offset-naive and offset-aware datetimes`. This was reproduced against the current working tree by calling `_render_item_detail_html()` with an earlier dead-letter timestamp with `+00:00` and a later successful timestamp with no offset. The render aborted at `other_at > failed_at`.

**Impact:** a mixed historical `queue_jobs` timestamp representation can turn an item detail page into a server error instead of presenting the listing/retry state.

**Required repair:** normalize all parsed queue timestamps to one timezone representation before comparison (or explicitly classify unparseable/mixed timestamps as non-superseding), catch comparison errors in the supersession predicate, and add both mixed-direction regression tests. The existing new test covers only two UTC-aware timestamps.

**Do not mark the listing-action fix ready to deploy until this is repaired and verified.**

## Nix/package-manager test-environment finding

The project source checkout's own `nix flake show` is currently blocked by an unresolved path reference (`Path 'home' does not exist in Git repository`). The separate owner flake at `/home/db/tgw-flake` evaluates successfully and supplies the dev shell.

Focused test outcomes through that declared dev shell:

- `tests/test_ebay_upload_dimension_limit.py`: **6 passed**.
- New listing-action regression test alone: **1 passed, 335 deselected**.
- Whole `tests/test_http_server.py`, after temporarily adding `python-multipart` only to the test invocation: **328 passed, 8 failed**. The eight failures are existing `/docs` tests blocked by missing `mistune` in the declared dev shell.
- Without temporary `python-multipart`, collection fails before any tests because that package is also missing from the declared dev shell.

The temporary package additions were ephemeral audit inputs only; no flake or production configuration was changed. The missing test dependencies and source-checkout flake path failure belong in the Portable Catalog/Nix package-manager-change process, with reproducible flake-owned fixes and `nix develop ... -c pytest` evidence.

## Positive evidence

- The photo-dimension focused suite passed. The new width-plus-height boundary behavior preserves raw files and leaves one-pixel headroom below the stated 15,000-pixel total.
- The intended listing action behavior passes when both job timestamps are UTC-aware.
- All four deleted `inbox/claude` documents have byte-identical archived successors, so those deletions are verified archival relocations, not content loss.

## Remaining reconciliation classification

The five old `docs/TGW-Plan-Vault/pp/PP-*.md` files are not byte-identical to their `plan/pp/` successors. Their changes are documented reconciliations/consolidations and their old copies have preserved `ARCHIVED-...` successors, but each remains a content review item—not a safe mechanical rename.

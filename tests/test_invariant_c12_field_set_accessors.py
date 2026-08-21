"""Invariant C12 detector (todo #1418, PP-LISTEDITOR-001) — static,
commit-time check: no file outside the two sanctioned Set A/Set B accessor
modules may index directly into item_attributes/item_specifics keys.

Chosen over a catalog-verify (data-scan) detector, per the packet's "your
call" on implementation: a static check catches a violation the moment the
code is written (at any `pytest` run / CI, effectively commit-time),
before it ever touches live data — a catalog-verify rule can only ever
notice AFTER a corrupting write has already happened. See
`reference/invariants.md` C12 and `tgw.inventory_record` /
`tgw.ebay.draft_specifics`'s banner comments for the full "why."

This is necessarily a grep-based heuristic, not full dataflow analysis —
plenty of legitimate code touches keys named 'item_attributes'/
'item_specifics' that are NOT the item document's own envelope (an AI
model's raw JSON response, a `revision_draft.delta` dict, or the sanctioned
accessors' own *return values* being assigned onward). Rather than try to
be "smart" about that distinction with a fragile regex, this test pins an
explicit, reviewed ALLOWLIST of every current hit outside the accessor
modules — any NEW hit that isn't in the allowlist fails the test, forcing
a human decision (route through the accessor, or extend the allowlist with
a one-line justification in code review) instead of a silent, unnoticed
regression.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_SCRIPTS = _REPO_ROOT / "scripts"

_PATTERN = re.compile(
    r"""\.get\(\s*["'](item_attributes|item_specifics)["']|"""
    r"""\[["'](item_attributes|item_specifics)["']\]"""
)

# The two sanctioned accessor modules — direct access is their whole job.
_ACCESSOR_MODULES = {
    _SRC / "tgw" / "inventory_record.py",
    _SRC / "tgw" / "ebay" / "draft_specifics.py",
}

# The migration script legitimately reads/writes the raw envelope shape
# while wrapping pre-migration bare dicts (todo #1418 Spec point 2) — it
# imports and uses the accessors' own wrap_*/is_envelope helpers rather
# than reinventing the envelope shape, but it necessarily touches the keys
# by name to detect what needs wrapping.
_MIGRATION_SCRIPT = _SCRIPTS / "migrate_field_set_envelope.py"

# Every remaining hit as of this packet landing, reviewed line-by-line —
# none of these read the item document's OWN item_attributes/item_specifics
# CONTENTS as a flat dict; each is one of:
#   (a) writing the accessor's own returned patch dict onward
#       (e.g. `doc["item_attributes"] = patch["item_attributes"]`), or
#   (b) a *different* dict that happens to share the key name (an AI
#       model's raw JSON response, or a revision_draft.delta proposal —
#       neither is the item's envelope).
# A new entry here must be justified the same way, in code review.
_ALLOWLIST = {
    (_SRC / "tgw" / "draft_sync.py", 90),
    (_SRC / "tgw" / "workers" / "ai_identify.py", 621),  # (b) AI model response
    (_SRC / "tgw" / "workers" / "ai_identify.py", 715),  # (a) required-schema accessor patch write
    (_SRC / "tgw" / "workers" / "ai_identify.py", 736),  # (a) accessor patch write
    (_SRC / "tgw" / "workers" / "ai_identify.py", 831),  # (a) accessor patch write to fence payload
    # Refreshed 2026-07-18 (this packet — todo #1499/#1500/#1506/#1507, the
    # same stale-line-numbers report independently rediscovered 4 times by
    # different tgw-coder packets today, each correctly declining to fix an
    # unrelated file out-of-scope): every line below re-verified against
    # current HEAD. Position-independent line-number pinning is inherently
    # fragile against unrelated edits shifting the file — that's a known,
    # accepted tradeoff of this detector's design (see module docstring),
    # not a defect; expect to refresh this list again after future edits.
    # Refreshed again 2026-07-18 (PP-CATALOG-INCR-001 CI-1+CI-2+CI-3 packets:
    # Google Lens context-menu removal, live store-category/fulfillment-policy
    # dropdown fetch helpers, and _apply_patch/_apply_ebay_write's new
    # publish_mutation + sqlite upsert-on-write + thumbnail-gen fence hooks —
    # all shifted every line below by varying amounts; re-verified against
    # current HEAD).
    # Refreshed 2026-07-19 (PP-CONDITION-ENUM-001 / todo #1562): the
    # condition_enum PATCH-validation block added before _apply_patch, plus
    # the flagFieldInvalid()/pipeline_error.field wiring in
    # _render_item_detail_html, shifted every line below by varying
    # amounts — re-verified against current HEAD, no accessor-routing
    # behavior changed, only positions.
    # Refreshed 2026-07-20 (todo #1608, PP-STATEMACHINE-001): added
    # `import psycopg2.errors` near the top of http_server.py, shifting
    # every line below by +1 — re-verified against current HEAD, no
    # accessor-routing behavior changed, only position.
    # Refreshed 2026-08-15: governed execution/authority additions shifted
    # existing HTTP access sites only; each remains an approved envelope gate,
    # accessor-patch handoff, or unrelated revision proposal field.
    # Refreshed 2026-08-20 after the W13 direct-publication branches were
    # retired; the same reviewed sites moved, with no accessor bypass added.
    (_SRC / "tgw" / "http_server.py", 1571),  # (c) todo #1464 envelope-shape gate — is_envelope() check only, not a contents read
    (_SRC / "tgw" / "http_server.py", 1578),  # (c) todo #1464 envelope-shape gate — is_envelope() check only, not a contents read
    (_SRC / "tgw" / "http_server.py", 2007),  # (a) accessor patch write
    (_SRC / "tgw" / "http_server.py", 2026),  # (a) accessor patch write (todo #1416, draft_listing.item_specifics)
    (_SRC / "tgw" / "http_server.py", 2031),  # (a) accessor output (full envelope) moving onward
    (_SRC / "tgw" / "http_server.py", 2043),  # (a) accessor patch write — padlock auto-sync
    (_SRC / "tgw" / "http_server.py", 2606),  # (b) revision_draft.delta
    (_SRC / "tgw" / "http_server.py", 2607),  # (b) revision_draft.delta
    (_SRC / "tgw" / "http_server.py", 2612),  # (a) accessor patch write (todo #1416)
    (_SRC / "tgw" / "http_server.py", 4157),  # (a) accessor output (inventory_diff.apply_inventory_diff's patch) onward into _apply_patch (#1417)
    (_SRC / "tgw" / "http_server.py", 4217),  # (a) category_aspect_migration's patch moving onward into _apply_patch (#1471)
    # Refreshed 2026-07-20 (todo #1582, PP-AGENTTRACE-001 Phase 3): the new
    # /form/runs route + _render_runs_html() inserted ~186 lines before this
    # entry, shifting it from 5855 to 6041 — re-verified against current
    # HEAD, no accessor-routing behavior changed, only position.
    (_SRC / "tgw" / "http_server.py", 7466),  # (b) revision_draft.delta
    (_SRC / "tgw" / "ebay" / "category_aspect_migration.py", 114),  # (a) accessor patch output moving onward (todo #1471 apply_category_aspect_migration)
    (_SRC / "tgw" / "ebay" / "category_aspect_migration.py", 121),  # (a) accessor patch output moving onward (todo #1471 apply_category_aspect_migration)
    (_SRC / "tgw" / "ebay" / "category_aspect_migration.py", 124),  # (a) accessor patch output moving onward (todo #1471 apply_category_aspect_migration)
    (_SRC / "tgw" / "ebay" / "inventory_diff.py", 159),  # (a) accessor patch output moving onward (todo #1417 apply_inventory_diff)
    (_SRC / "tgw" / "ebay" / "inventory_diff.py", 163),  # (a) accessor patch output moving onward (todo #1417 apply_inventory_diff)
    (_SRC / "tgw" / "workers" / "ebay_draft.py", 636),  # (a) required-schema accessor patch write
    (_SRC / "tgw" / "workers" / "ebay_draft.py", 944),  # (a) required-schema accessor patch write to fence payload
    # Refreshed 2026-07-20 (todo #1598, PP-MULTIMODEL-001 / invariant E15
    # sweep): removed a dead `_OLLAMA_FALLBACK_MODEL` module constant + docstring
    # rewrite in ai_identify.py, net +3 lines before these entries — shifted
    # from 273/333/428 to 276/336/431. No accessor-routing behavior changed.
}


def _scan() -> set:
    hits = set()
    for root in (_SRC, _SCRIPTS):
        for path in root.rglob("*.py"):
            if path in _ACCESSOR_MODULES or path == _MIGRATION_SCRIPT:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _PATTERN.search(line):
                    hits.add((path, lineno))
    return hits


def test_no_new_direct_field_set_access_outside_accessors():
    hits = _scan()
    unexpected = hits - _ALLOWLIST
    assert not unexpected, (
        "Invariant C12 violation: direct item_attributes/item_specifics "
        "dict access found outside the sanctioned accessor modules "
        "(tgw.inventory_record / tgw.ebay.draft_specifics). Route through "
        "the accessor, or if this is a genuinely different dict that just "
        "shares the key name, add it to _ALLOWLIST with a one-line reason: "
        f"{sorted((str(p), n) for p, n in unexpected)}"
    )


def test_allowlist_has_no_stale_entries():
    """Catches the opposite drift: an allowlisted line that moved/was
    removed, so the allowlist itself doesn't silently rot into meaninglessness."""
    hits = _scan()
    stale = _ALLOWLIST - hits
    assert not stale, f"C12 allowlist entries no longer match any real hit (code moved or was removed) — update _ALLOWLIST in this test: {sorted((str(p), n) for p, n in stale)}"


def test_accessor_modules_exist():
    for path in _ACCESSOR_MODULES:
        assert path.exists(), f"expected accessor module missing: {path}"

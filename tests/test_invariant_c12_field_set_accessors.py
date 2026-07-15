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
    r'''\.get\(\s*["'](item_attributes|item_specifics)["']|'''
    r'''\[["'](item_attributes|item_specifics)["']\]'''
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
    (_SRC / "tgw" / "workers" / "ai_identify.py", 273),   # (b) AI model response
    (_SRC / "tgw" / "workers" / "ai_identify.py", 333),   # (a) accessor patch write
    (_SRC / "tgw" / "workers" / "ai_identify.py", 428),   # (a) accessor patch write
    (_SRC / "tgw" / "http_server.py", 992),               # (a) accessor patch write
    (_SRC / "tgw" / "http_server.py", 1013),              # (a) accessor patch write (todo #1416, draft_listing.item_specifics)
    (_SRC / "tgw" / "http_server.py", 1018),              # (a) accessor output (full envelope) moving onward
    (_SRC / "tgw" / "http_server.py", 1506),              # (b) revision_draft.delta
    (_SRC / "tgw" / "http_server.py", 1508),              # (b) revision_draft.delta
    (_SRC / "tgw" / "http_server.py", 1514),              # (a) accessor patch write (todo #1416)
    (_SRC / "tgw" / "http_server.py", 5035),              # (b) revision_draft.delta
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
    assert not stale, (
        f"C12 allowlist entries no longer match any real hit (code moved or "
        f"was removed) — update _ALLOWLIST in this test: {sorted((str(p), n) for p, n in stale)}"
    )


def test_accessor_modules_exist():
    for path in _ACCESSOR_MODULES:
        assert path.exists(), f"expected accessor module missing: {path}"

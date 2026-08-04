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

This is necessarily a heuristic, not full dataflow analysis — plenty of
legitimate code touches keys named 'item_attributes'/'item_specifics' that
are NOT the item document's own envelope (an AI model's raw JSON response,
a `revision_draft.delta` dict, or the sanctioned accessors' own *return
values* being assigned onward). Rather than try to be "smart" about that
distinction, this test pins an explicit, reviewed ALLOWLIST of every
current hit outside the accessor modules — any NEW hit that isn't in the
allowlist fails the test, forcing a human decision (route through the
accessor, or extend the allowlist with a one-line justification in code
review) instead of a silent, unnoticed regression.

Todo #1706 (this revision): the allowlist identity used to be raw
`(path, line_number)`. That made the whole suite brittle to any unrelated
edit anywhere earlier in the file shifting every line below it — refreshed
5+ times already (2026-07-18 x2, 07-19, 07-20 x2), each refresh itself a
"trust me, I re-verified" manual step. This revision replaces line-number
identity with a *semantic* identity parsed from the AST:
(repo-relative path, enclosing scope, access kind, key, normalized
containing-expression) — see `_extract_hits` below. Ordinary comments,
blank lines, imports, or unrelated statements inserted before a reviewed
hit no longer change its identity (spec point 7). Multiplicity is
preserved via `Counter`, not `set` — two syntactically-identical allowed
accesses in the same scope are two separate items, and a THIRD copy is a
detected violation, not silently collapsed (spec point 3).
"""

import ast
from collections import Counter
from pathlib import Path
from typing import List, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_SCRIPTS = _REPO_ROOT / "scripts"

_KEYS = {"item_attributes", "item_specifics"}

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

# A single semantic identity: (repo-relative path, enclosing scope,
# access kind ["get" | "subscript"], key, normalized expression).
_Identity = Tuple[str, str, str, str, str]


class MalformedSourceError(Exception):
    """Raised when a scanned file fails to parse as Python — fail closed
    (spec point 6d) rather than silently skip an unscannable file."""


class _FieldSetAccessVisitor(ast.NodeVisitor):
    """Walks a parsed module, recording every direct item_attributes/
    item_specifics `.get(...)` call or `[...]` subscript, as a semantic
    identity: enclosing function/class scope + access kind + key + a
    normalized unparse of the whole access expression. Deliberately
    excludes line/column so unrelated line insertions elsewhere in the
    file never change an existing hit's identity."""

    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self._scope_stack: List[str] = ["<module>"]
        self.hits: List[_Identity] = []

    def _scope(self) -> str:
        return ".".join(self._scope_stack)

    def _visit_scoped(self, node) -> None:
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _KEYS
        ):
            self.hits.append((
                self.rel_path,
                self._scope(),
                "get",
                node.args[0].value,
                ast.unparse(node),
            ))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        sl = node.slice
        if isinstance(sl, ast.Constant) and sl.value in _KEYS:
            self.hits.append((
                self.rel_path,
                self._scope(),
                "subscript",
                sl.value,
                ast.unparse(node),
            ))
        self.generic_visit(node)


def _extract_hits(text: str, rel_path: str) -> List[_Identity]:
    """Parse `text` (a Python module's source) and return every direct
    item_attributes/item_specifics access as a semantic identity tuple.
    Raises MalformedSourceError on invalid Python — never silently skips
    (spec point 6d)."""
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError as exc:
        raise MalformedSourceError(
            f"C12 detector: {rel_path} failed to parse as Python — fix the "
            f"file, it cannot be scanned for field-set access: {exc}"
        ) from exc
    visitor = _FieldSetAccessVisitor(rel_path)
    visitor.visit(tree)
    return visitor.hits


def _diff_against_allowlist(
    hits: Counter, allowlist: Counter
) -> Tuple[Counter, Counter]:
    """Multiset-aware comparison (spec point 3/6):
    - `extra`  = hits present beyond what the allowlist sanctions — a new
      unauthorized access, OR an extra duplicate of an already-allowed one.
    - `missing` = allowlisted entries no longer matched by any real hit —
      the access was removed, or its expression/scope changed underneath
      the allowlist entry (a semantic change), so it no longer matches.
    `Counter` subtraction is itself multiset-aware (keeps only positive
    remainders), which is exactly the count-aware comparison spec point 3
    asks for."""
    extra = hits - allowlist
    missing = allowlist - hits
    return extra, missing


def _scan() -> Counter:
    counter: Counter = Counter()
    for root in (_SRC, _SCRIPTS):
        for path in sorted(root.rglob("*.py")):
            if path in _ACCESSOR_MODULES or path == _MIGRATION_SCRIPT:
                continue
            rel = str(path.relative_to(_REPO_ROOT))
            text = path.read_text(encoding="utf-8", errors="replace")
            counter.update(_extract_hits(text, rel))
    return counter


def _fmt(counter: Counter) -> str:
    return ", ".join(
        f"{path}::{scope} {kind}[{key}] `{expr}`" + (f" x{n}" if n > 1 else "")
        for (path, scope, kind, key, expr), n in sorted(counter.items())
    )


# Every remaining hit as of this packet (todo #1706), reviewed line-by-line
# and re-derived directly from the semantic scanner above — none of these
# read the item document's OWN item_attributes/item_specifics CONTENTS as
# a flat dict; each is one of:
#   (a) writing the accessor's own returned patch dict onward
#       (e.g. `doc["item_attributes"] = patch["item_attributes"]`);
#   (b) a *different* dict that happens to share the key name (an AI
#       model's raw JSON response, or a revision_draft.delta proposal —
#       neither is the item's envelope); or
#   (c) an envelope-SHAPE gate (is_envelope() check) that never reads the
#       envelope's contents.
# Counts >1 are real: the same normalized expression appears more than
# once in the same scope (e.g. once assembling a working dict, once in the
# return value) — each occurrence still requires this same review, per
# spec point 3, so it is counted rather than collapsed.
# A new entry here must be justified the same way, in code review.
_ALLOWLIST: Counter = Counter({
    ("src/tgw/draft_sync.py", "<module>.pin_draft_to_live", "subscript",
     "item_specifics", "dl['item_specifics']"): 1,
    # (a) full re-pin of the Set B envelope to the eBay live mirror
    # (M4/S1) via wrap_ebay_specifics — the live mirror IS the new
    # baseline, no accessor diff needed.

    ("src/tgw/ebay/category_aspect_migration.py",
     "<module>.apply_category_aspect_migration", "subscript",
     "item_attributes", "working_item['item_attributes']"): 1,
    # (a) accessor patch output moving onward into working_item, so
    # remove_ebay_aspects() sees Set A's already-applied change.

    ("src/tgw/ebay/category_aspect_migration.py",
     "<module>.apply_category_aspect_migration", "subscript",
     "item_attributes", "inv_patch['item_attributes']"): 2,
    # (a) accessor patch output — once assembled into working_item, once
    # returned to the caller.

    ("src/tgw/ebay/category_aspect_migration.py",
     "<module>.apply_category_aspect_migration", "subscript",
     "item_specifics", "ebay_patch['item_specifics']"): 1,
    # (a) accessor patch output (remove_ebay_aspects' return value) moving
    # onward into the function's own return dict.

    ("src/tgw/ebay/inventory_diff.py", "<module>.apply_inventory_diff",
     "subscript", "item_attributes", "working_item['item_attributes']"): 2,
    # (a) accessor patch output re-fed into the next diff-source-group's
    # input, and returned to the caller.

    ("src/tgw/ebay/inventory_diff.py", "<module>.apply_inventory_diff",
     "subscript", "item_attributes", "patch['item_attributes']"): 1,
    # (a) accessor patch output moving onward into working_item.

    ("src/tgw/http_server.py", "<module>.patch_item", "get",
     "item_attributes", "body.fields.get('item_attributes')"): 1,
    # (c) todo #1464 envelope-shape gate — is_envelope() check only, not a
    # contents read.

    ("src/tgw/http_server.py", "<module>.patch_item", "get",
     "item_specifics", "_dl_for_gate.get('item_specifics')"): 1,
    # (c) todo #1464 envelope-shape gate — is_envelope() check only, not a
    # contents read.

    ("src/tgw/http_server.py", "<module>._apply_patch", "subscript",
     "item_attributes", "doc['item_attributes']"): 2,
    # (a) accessor patch write — once for the plain-dict (non-envelope)
    # branch, once for the padlock auto-sync branch (todo #1406/07-18).

    ("src/tgw/http_server.py", "<module>._apply_patch", "subscript",
     "item_attributes", "patch['item_attributes']"): 1,
    # (a) accessor patch write (inventory_record.set_inventory_fields'
    # patch, non-envelope branch).

    ("src/tgw/http_server.py", "<module>._apply_patch", "subscript",
     "item_specifics", "existing['item_specifics']"): 2,
    # (a) accessor patch write into the draft_listing envelope — the
    # sanctioned Set B accessor's branch, and the already-envelope
    # passthrough branch (todo #1416 point 3).

    ("src/tgw/http_server.py", "<module>._apply_patch", "subscript",
     "item_specifics", "sp_patch['item_specifics']"): 1,
    # (a) accessor patch write (set_ebay_aspects' patch, todo #1416).

    ("src/tgw/http_server.py", "<module>._apply_patch", "subscript",
     "item_attributes", "_ia_sync['item_attributes']"): 1,
    # (a) accessor patch write — padlock auto-sync
    # (inventory_record.sync_from_draft's patch, todo #1406/2026-07-18).

    ("src/tgw/http_server.py", "<module>.item_action", "subscript",
     "item_specifics", "delta['item_specifics']"): 2,
    # (b) revision_draft.delta — presence check and value read, both for
    # accept_proposals (todo #1416 point 4).

    ("src/tgw/http_server.py", "<module>.item_action", "subscript",
     "item_specifics", "dl2['item_specifics']"): 1,
    # (a) accessor patch write onto the draft_listing dict being staged
    # (todo #1416).

    ("src/tgw/http_server.py", "<module>.item_action", "subscript",
     "item_specifics", "dl_patch['item_specifics']"): 1,
    # (a) accessor output (set_ebay_aspects' patch) moving onward.

    ("src/tgw/http_server.py", "<module>.apply_inventory_diff_endpoint",
     "subscript", "item_attributes", "patch['item_attributes']"): 1,
    # (a) accessor output (apply_inventory_diff's patch) moving onward
    # into _apply_patch (#1417).

    ("src/tgw/http_server.py",
     "<module>.apply_category_aspect_migration_endpoint", "subscript",
     "item_attributes", "patch['item_attributes']"): 1,
    # (a) category_aspect_migration's patch moving onward into
    # _apply_patch (#1471).

    ("src/tgw/http_server.py", "<module>._render_item_detail_html", "get",
     "item_specifics", "_rev_delta.get('item_specifics')"): 1,
    # (b) revision_draft.delta, read-only prefill of proposed aspects.

    ("src/tgw/workers/ai_identify.py",
     "<module>.AIIdentifyWorker.handle", "get", "item_specifics",
     "result.get('item_specifics')"): 1,
    # (b) AI model's raw JSON response, not the item's own envelope.

    ("src/tgw/workers/ai_identify.py",
     "<module>.AIIdentifyWorker.handle", "subscript", "item_attributes",
     "item['item_attributes']"): 1,
    # (a) accessor patch write onto the in-memory item dict.

    ("src/tgw/workers/ai_identify.py",
     "<module>.AIIdentifyWorker.handle", "subscript", "item_attributes",
     "_ia_patch['item_attributes']"): 2,
    # (a) accessor patch output — written onto `item`, and forwarded into
    # fence_fields for the fence PATCH call.

    ("src/tgw/workers/ai_identify.py",
     "<module>.AIIdentifyWorker.handle", "subscript", "item_attributes",
     "fence_fields['item_attributes']"): 1,
    # (a) accessor patch output forwarded through the fence write.
})


def test_no_new_direct_field_set_access_outside_accessors():
    hits = _scan()
    extra, _missing = _diff_against_allowlist(hits, _ALLOWLIST)
    assert not extra, (
        "Invariant C12 violation: direct item_attributes/item_specifics "
        "access found outside the sanctioned accessor modules "
        "(tgw.inventory_record / tgw.ebay.draft_specifics) beyond what's "
        "reviewed — either a brand-new unauthorized access, or an extra "
        "duplicate of an already-allowlisted one. Route through the "
        "accessor, or if this is a genuinely different dict that just "
        "shares the key name, add/increment it in _ALLOWLIST with a "
        f"one-line reason: {_fmt(extra)}"
    )


def test_allowlist_has_no_stale_entries():
    """Catches the opposite drift: an allowlisted access that moved,
    changed, or was removed, so the allowlist itself doesn't silently rot
    into meaninglessness."""
    hits = _scan()
    _extra, missing = _diff_against_allowlist(hits, _ALLOWLIST)
    assert not missing, (
        "C12 allowlist entries no longer match any real hit — code was "
        "removed, or an allowlisted access's expression/scope changed "
        f"underneath it — update _ALLOWLIST in this test: {_fmt(missing)}"
    )


def test_accessor_modules_exist():
    for path in _ACCESSOR_MODULES:
        assert path.exists(), f"expected accessor module missing: {path}"


# --- Regression tests (todo #1706) ------------------------------------------


def _make_source_with_prefix(prefix_lines: int) -> str:
    prefix = "\n".join(f"# unrelated comment {i}" for i in range(prefix_lines))
    return f'''{prefix}

def handler(item):
    return item.get("item_specifics")
'''


def test_semantic_identity_stable_across_unrelated_line_insertion():
    """Spec point 7: comments/blank lines/imports inserted before a
    reviewed hit must not change its identity."""
    base_hits = _extract_hits(_make_source_with_prefix(0), "synthetic.py")
    shifted_hits = _extract_hits(_make_source_with_prefix(7), "synthetic.py")
    assert base_hits, "expected at least one hit in the synthetic source"
    assert base_hits == shifted_hits


def test_unauthorized_new_access_is_flagged():
    hits = _extract_hits(
        'def f(item):\n    return item["item_attributes"]\n', "synthetic.py"
    )
    extra, missing = _diff_against_allowlist(Counter(hits), Counter())
    assert extra
    assert not missing


def test_stale_allowed_access_is_flagged():
    identity = ("synthetic.py", "<module>.f", "subscript",
                "item_attributes", "item['item_attributes']")
    allowlist = Counter({identity: 1})
    extra, missing = _diff_against_allowlist(Counter(), allowlist)
    assert missing
    assert not extra


def test_duplicate_access_beyond_allowed_count_is_flagged():
    """Spec point 3/6c: a second, otherwise-identical allowed access in
    the same scope must be caught, not collapsed by a set."""
    src = (
        'def f(item):\n'
        '    a = item["item_attributes"]\n'
        '    b = item["item_attributes"]\n'
    )
    hits = _extract_hits(src, "synthetic.py")
    identity = ("synthetic.py", "<module>.f", "subscript",
                "item_attributes", "item['item_attributes']")
    allowlist = Counter({identity: 1})
    extra, missing = _diff_against_allowlist(Counter(hits), allowlist)
    assert extra
    assert not missing


def test_duplicate_access_matching_allowed_count_is_not_flagged():
    """Sanity complement: exactly as many occurrences as allowlisted is
    fine — only an EXTRA duplicate beyond the reviewed count is a
    violation."""
    src = (
        'def f(item):\n'
        '    a = item["item_attributes"]\n'
        '    b = item["item_attributes"]\n'
    )
    hits = _extract_hits(src, "synthetic.py")
    identity = ("synthetic.py", "<module>.f", "subscript",
                "item_attributes", "item['item_attributes']")
    allowlist = Counter({identity: 2})
    extra, missing = _diff_against_allowlist(Counter(hits), allowlist)
    assert not extra
    assert not missing


def test_malformed_python_fails_closed():
    """Spec point 6d: malformed Python must raise, never be silently
    skipped."""
    import pytest

    with pytest.raises(MalformedSourceError):
        _extract_hits("def broken(:\n    pass\n", "synthetic.py")

"""Invariant C10 detector (PP-PHOTOSYNC-001 P3, todo #1118).

Closes the 🔶 in invariants.md C10: a source-scan (fence-grep-audit pattern,
see test_invariants_items_fence.py) over every `state_machine.enqueue_job(`
call site in http_server.py. Each site must either stamp
`origin="operator"` in its payload, or enqueue the `catalog_rebuild` queue
(the one allowlisted background-only queue — coalesced rebuilds never carry
an operator origin, by design). Anything else is a new operator-adjacent
endpoint that forgot the stamp.
"""

from __future__ import annotations

import pathlib
import re

_CALL_MARKER = "state_machine.enqueue_job("
_ALLOWLIST_QUEUES = frozenset({"catalog_rebuild"})


def _extract_call_blocks(source: str) -> list[str]:
    """Return the full text of every `state_machine.enqueue_job(...)` call,
    from the marker through its balanced closing paren."""
    blocks = []
    start = 0
    while True:
        idx = source.find(_CALL_MARKER, start)
        if idx == -1:
            break
        depth = 0
        i = idx + len(_CALL_MARKER) - 1  # position of the opening '('
        end = None
        for j in range(i, len(source)):
            if source[j] == "(":
                depth += 1
            elif source[j] == ")":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        end = end if end is not None else len(source)
        blocks.append(source[idx:end + 1])
        start = end + 1
    return blocks


def _preceding_function_body(source: str, call_start_idx: int) -> str:
    """Slice from the nearest enclosing `def ` above the call back to the call
    itself — used to find an out-of-line `payload["origin"] = "operator"`
    stamp when payload is passed as a bare variable, not a dict literal."""
    def_idx = source.rfind("\ndef ", 0, call_start_idx)
    return source[def_idx if def_idx != -1 else 0:call_start_idx]


def find_unstamped_enqueue_sites(source: str) -> list[str]:
    """Return one description string per call site that neither stamps
    origin='operator' nor targets an allowlisted queue."""
    violations = []
    start = 0
    while True:
        idx = source.find(_CALL_MARKER, start)
        if idx == -1:
            break
        depth = 0
        i = idx + len(_CALL_MARKER) - 1
        end = None
        for j in range(i, len(source)):
            if source[j] == "(":
                depth += 1
            elif source[j] == ")":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        end = end if end is not None else len(source)
        block = source[idx:end + 1]
        start = end + 1
        lineno = source.count("\n", 0, idx) + 1

        qname_match = re.search(r'queue_name\s*=\s*"([^"]+)"', block)
        if qname_match and qname_match.group(1) in _ALLOWLIST_QUEUES:
            continue

        if re.search(r'["\']origin["\']\s*:\s*["\']operator["\']', block):
            continue

        payload_var = re.search(r'payload\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*,', block)
        if payload_var:
            var = payload_var.group(1)
            preceding = _preceding_function_body(source, idx)
            stamp_pattern = re.compile(
                re.escape(var) + r'\s*\[\s*["\']origin["\']\s*\]\s*=\s*["\']operator["\']'
            )
            if stamp_pattern.search(preceding):
                continue

        violations.append(f"line {lineno}: {block[:80]!r}")
    return violations


def test_current_http_server_has_no_unstamped_operator_enqueue():
    repo = pathlib.Path(__file__).parents[1]
    source = (repo / "src" / "tgw" / "http_server.py").read_text(encoding="utf-8")
    violations = find_unstamped_enqueue_sites(source)
    assert not violations, (
        "enqueue_job site(s) without origin='operator' and not on the "
        "catalog_rebuild allowlist — stamp the origin or add the queue to "
        "_ALLOWLIST_QUEUES with a reason:\n" + "\n".join(violations)
    )


def test_detector_flags_a_deliberately_unstamped_site():
    poisoned = '''
def handle_new_operator_button(sku):
    job_id = state_machine.enqueue_job(
        queue_name="ebay_upload",
        payload={"sku": sku},
        max_attempts=5,
    )
'''
    violations = find_unstamped_enqueue_sites(poisoned)
    assert len(violations) == 1
    assert "ebay_upload" not in _ALLOWLIST_QUEUES  # sanity: not accidentally allowlisted


def test_detector_allows_catalog_rebuild_without_origin():
    clean = '''
def _enqueue_catalog_rebuild(reason):
    state_machine.enqueue_job(
        queue_name="catalog_rebuild",
        payload={"reason": reason},
        dedupe_key="catalog_rebuild:pending",
        max_attempts=3,
    )
'''
    assert find_unstamped_enqueue_sites(clean) == []


def test_detector_allows_out_of_line_origin_stamp():
    clean = '''
def retry_dead_letter(job_id, sku, payload):
    payload["origin"] = "operator"
    new_job_id = state_machine.enqueue_job(
        queue_name=row["queue_name"],
        payload=payload,
        dedupe_key=new_dedupe,
        max_attempts=3,
    )
'''
    assert find_unstamped_enqueue_sites(clean) == []

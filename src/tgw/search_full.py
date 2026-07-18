"""
tgw.search_full — full-text search over the recoll index (PP-KNOWLEDGE-001
Track R2, todo #1147).

Wraps the `recollq` CLI (Recoll's scriptable query binary — the "recoll
Python API" named in the design doc turned out, live-verified, to not be
what's actually installed on this system; `recollq` is the correct real
invocation, see result manifest deviation note) against the live index at
`/opt/TGW/.recoll` (441K+ docs: ItemData/ItemArchive/ItemCatalog/plan vault
+ mounted drives, per CLAUDE.md's key-paths table).

Output contract: every call returns `{ok, ...}` per settled architecture.
Never touches ItemData directly — this is a read-only index query, no
tgw-api fence involvement (nothing here writes).
"""

from __future__ import annotations

import base64
import subprocess
import time
from typing import Any, Dict, List

RECOLL_CONFDIR = "/opt/TGW/.recoll"
RECOLLQ_BIN = "recollq"
DEFAULT_LIMIT = 20
MAX_LIMIT = 200
TIMEOUT_SECONDS = 20

_FIELDS = ["url", "title", "mtype", "fbytes", "abstract"]


def _decode_field(raw: str) -> str:
    """recollq -F output is base64-encoded per field, space-separated."""
    if not raw:
        return ""
    try:
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except Exception:
        return raw


def _parse_recollq_output(stdout: str) -> List[Dict[str, str]]:
    """Parse `recollq -F "url title mtype fbytes abstract"` output — one
    result per line, fields base64-encoded and space-separated, per
    recollq -h's documented "recommended format for use by other programs".

    recollq -F still prints two informational header lines to stdout ahead
    of the data (verified live, 2026-07-18): "Recoll query: Query(...)" and
    "N results (printing M max):" — the ":3:../common/rclinit.cpp..."
    startup banner is stderr and never reaches this function. Both stdout
    header lines are skipped explicitly rather than relying on field-count
    alone, since a data row with several empty fields can also produce a
    short token count."""
    results: List[Dict[str, str]] = []
    for line in stdout.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("Recoll query:"):
            continue
        if line.rstrip().endswith(":") and "results" in line and "printing" in line:
            continue
        parts = line.split(" ")
        # -F output is exactly len(_FIELDS) space-separated base64 tokens
        # (trailing empty tokens for absent fields are common — recollq
        # still emits a placeholder).
        if len(parts) < len(_FIELDS):
            continue
        decoded = [_decode_field(p) for p in parts[: len(_FIELDS)]]
        row = dict(zip(_FIELDS, decoded))
        if not row.get("url"):
            continue
        results.append(row)
    return results


def run_full_text_search(query: str, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """Run a full-text query against the live recoll index via recollq.

    Query language: recoll's own (implicit AND, `-term` exclusion,
    `field:term`, quoted phrases, `OR`) — passed through verbatim, this
    function does not reinterpret it. `-a` (ALL TERMS / AND mode) is used
    to match the GUI's default simple-search behavior, matching the "six
    hours-to-seconds queries" acceptance framing (recovery/audit lookups,
    not fuzzy ranking).

    Returns {ok, query, count, elapsed_ms, results:[{url,title,mtype,
    fbytes,abstract}, ...]}. Never raises — `ok:False` + `error` on any
    failure (missing binary, timeout, bad query), same contract as every
    other tgw-api-adjacent call in this codebase.
    """
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query"}

    limit = max(1, min(int(limit if limit is not None else DEFAULT_LIMIT), MAX_LIMIT))

    cmd = [
        RECOLLQ_BIN,
        "-a",
        "-c", RECOLL_CONFDIR,
        "-n", str(limit),
        "-F", " ".join(_FIELDS),
        query,
    ]

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"{RECOLLQ_BIN} not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"recollq timed out after {TIMEOUT_SECONDS}s"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": str(exc)}

    elapsed_ms = (time.time() - t0) * 1000

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"recollq exited {proc.returncode}: {proc.stderr.strip()[:500]}",
        }

    results = _parse_recollq_output(proc.stdout)

    return {
        "ok": True,
        "query": query,
        "count": len(results),
        "elapsed_ms": round(elapsed_ms, 1),
        "results": results,
    }


def format_results_text(result: Dict[str, Any]) -> str:
    """Plain-text rendering for CLI output."""
    if not result.get("ok"):
        return f"error: {result.get('error', 'unknown error')}"
    lines = [
        f"{result['count']} result(s) for {result['query']!r} "
        f"({result['elapsed_ms']:.0f} ms)"
    ]
    for row in result.get("results", []):
        title = row.get("title") or row.get("url", "").rsplit("/", 1)[-1]
        size = row.get("fbytes", "")
        size_str = f" ({size} bytes)" if size else ""
        lines.append(f"  [{row.get('mtype', '?')}] {title}{size_str}")
        lines.append(f"      {row.get('url', '')}")
    return "\n".join(lines)

"""todo #1406 / PP-DEADLETTER-001 — entity_id passthrough regression guard.

`state_machine.enqueue_job()`'s `entity_id` kwarg silently defaults to
`queue_name` when omitted (see the docstring on `enqueue_job` for why). That
made `queue_jobs.entity_id` == `queue_name` for ~300k historical rows because
every internal pipeline cross-enqueue call (ebay_draft->ebay_upload,
ebay_price->ebay_stage, ebay_stage->ebay_publish, etc.) forgot to pass it —
breaking `tgw queue-history --sku <sku>` (job_history()'s
`WHERE entity_id = %s`) for almost every item, with no error anywhere.

This test statically scans every `enqueue_job(...)` call site in
`src/tgw/workers/*.py` via AST: any call whose `payload=` dict literal
contains a `'sku'` key MUST also pass an `entity_id=` keyword argument. This
is a lint-style guard, not a runtime test — it exists so a new pipeline
worker (or a new cross-enqueue call in an existing one) can't reintroduce
the bug by silently relying on the queue_name fallback again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_WORKERS_DIR = Path(__file__).resolve().parents[1] / "src" / "tgw" / "workers"


def _payload_has_sku_key(node: ast.AST) -> bool:
    """True if this AST node is a dict literal (or **-merge of dict literals)
    containing a 'sku' string key anywhere in it."""
    if isinstance(node, ast.Dict):
        for key in node.keys:
            if key is not None and isinstance(key, ast.Constant) and key.value == "sku":
                return True
        return False
    return False


def _find_bad_enqueue_calls(path: Path) -> list[int]:
    """Return line numbers of enqueue_job(...) calls with a sku-bearing
    payload but no entity_id kwarg."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad_lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "enqueue_job":
            continue
        kwarg_names = {kw.arg for kw in node.keywords if kw.arg is not None}
        payload_kwarg = next((kw for kw in node.keywords if kw.arg == "payload"), None)
        if payload_kwarg is None:
            continue
        if not _payload_has_sku_key(payload_kwarg.value):
            continue
        if "entity_id" not in kwarg_names:
            bad_lines.append(node.lineno)
    return bad_lines


@pytest.mark.parametrize(
    "worker_file",
    sorted(p.name for p in _WORKERS_DIR.glob("*.py")),
)
def test_sku_payload_enqueue_calls_pass_entity_id(worker_file):
    path = _WORKERS_DIR / worker_file
    bad_lines = _find_bad_enqueue_calls(path)
    assert bad_lines == [], (
        f"{worker_file}: enqueue_job() call(s) at line(s) {bad_lines} pass a "
        "payload containing 'sku' but no entity_id= kwarg — this regresses "
        "todo #1406/PP-DEADLETTER-001 (queue_jobs.entity_id silently falls "
        "back to queue_name, breaking `tgw queue-history --sku`). Pass "
        "entity_id=sku explicitly."
    )

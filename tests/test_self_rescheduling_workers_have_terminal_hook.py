"""audit#1143 #1234 follow-up (todo #1242) — code review on the original fix
found it only covered 2 of 8 self-rescheduling workers, leaving 6 with the
exact same bug: a dead-lettered job silently ends the recurring chain
forever. That was closed by hand-writing an identical _on_terminal_failure
override into all 8 worker files, guarded by an AST scan requiring the
override to exist.

audit#1143 #1244 follow-up (later code review): generalized instead —
QueueWorker._on_terminal_failure() now auto-detects a no-arg self._reschedule
and calls it, so a *future* self-rescheduling worker of that common shape
needs no override at all and can't reintroduce the gap. Only a worker whose
_reschedule() requires an argument (ebay_sku_migrate: needs interval_hours
recomputed from config) must still override _on_terminal_failure explicitly
— the structural guard below now targets exactly that narrower case.
"""

import ast
from pathlib import Path

import tgw.queue.worker_base as worker_base
from tgw.workers import ebay_sku_migrate

WORKERS_DIR = Path(__file__).resolve().parents[1] / 'src' / 'tgw' / 'workers'


def _class_methods(tree: ast.Module) -> dict[str, set[str]]:
    """Return {class_name: {method_names}} for every class in the module."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            out[node.name] = {
                n.name for n in node.body if isinstance(n, ast.FunctionDef)
            }
    return out


def _reschedule_requires_arg(cls_methods_src: str) -> bool:
    """Best-effort: does this class's _reschedule take a required arg besides self?"""
    tree = ast.parse(cls_methods_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_reschedule':
            args = node.args.args[1:]  # drop self
            defaults = len(node.args.defaults)
            required = len(args) - defaults
            return required > 0
    return False


def test_only_arg_requiring_reschedule_workers_override_terminal_failure_hook():
    """Any worker whose _reschedule() needs an argument must keep its own
    _on_terminal_failure override — the base class default can't know what
    to pass. Workers with a plain no-arg _reschedule must NOT hand-roll an
    override anymore; the base class already covers them (collapsing back
    to per-file overrides would reintroduce the exact duplication #1244
    removed)."""
    violations = []
    for py in sorted(WORKERS_DIR.glob('*.py')):
        text = py.read_text(encoding='utf-8')
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
            if '_reschedule' not in methods:
                continue
            cls_src = ast.get_source_segment(text, node) or ''
            needs_arg = _reschedule_requires_arg(cls_src)
            has_override = '_on_terminal_failure' in methods
            if needs_arg and not has_override:
                violations.append(
                    f'{py.name}:{node.name} — _reschedule() requires an arg '
                    f'but has no _on_terminal_failure override'
                )
            if not needs_arg and has_override:
                violations.append(
                    f'{py.name}:{node.name} — has a redundant hand-written '
                    f'_on_terminal_failure override for a no-arg _reschedule() '
                    f'that worker_base.QueueWorker already handles automatically'
                )

    assert not violations, "\n".join(violations)


def test_base_class_auto_reschedules_no_arg_workers_on_terminal_failure():
    """The generalized mechanism itself: worker_base.QueueWorker's default
    _on_terminal_failure must call a subclass's no-arg _reschedule()."""
    calls = []

    class _W(worker_base.QueueWorker):
        def __init__(self):
            self.queue_name = 'fake_queue'

        def handle(self, job):
            raise NotImplementedError

        def _reschedule(self):
            calls.append(1)

    _W()._on_terminal_failure({'job_id': 'j1'}, 'boom')
    assert calls == [1]


def test_base_class_skips_reschedule_that_requires_an_argument():
    """A subclass with an arg-requiring _reschedule() and no override must
    not have the base class blow up trying to call it with no arguments."""
    calls = []

    class _W(worker_base.QueueWorker):
        def __init__(self):
            self.queue_name = 'fake_queue'

        def handle(self, job):
            raise NotImplementedError

        def _reschedule(self, interval_hours):
            calls.append(interval_hours)

    _W()._on_terminal_failure({'job_id': 'j1'}, 'boom')  # must not raise
    assert calls == []


def test_ebay_sku_migrate_still_reschedules_via_its_own_override():
    """The one legitimate manual override: confirm it still fires on
    terminal failure and passes a real interval_hours value."""
    calls = []
    worker = ebay_sku_migrate.EbaySkuMigrateWorker.__new__(
        ebay_sku_migrate.EbaySkuMigrateWorker)
    worker.queue_name = 'ebay_sku_migrate'
    worker.config = {'ebay_sku_migrate': {'interval_hours': 2.5}}
    worker._reschedule = lambda interval_hours: calls.append(interval_hours)

    worker._on_terminal_failure({'job_id': 'j1'}, 'boom')

    assert calls == [2.5]

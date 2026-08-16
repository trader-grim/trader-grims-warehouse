"""todo #1366 (PP-HERMES-EA-001): scripts/check_review_md.py is the
mechanical pre-stitch gate that catches a missing `-REVIEW.md` before a
todo's branch is merged -- the exact failure mode that let 6 of 7
concurrent-batch-stitched todos (#1280/#1282/#1284/#1288/#1291/#1297)
silently skip tgw-runner-review's mandated REVIEW.md write in one session,
only caught and backfilled after the fact (2026-07-13).

All filesystem interaction is monkeypatched to a tmp_path fixture standing
in for `/opt/TGW/library/plans/plan/packets/results/` -- no real Plan path
is touched. Fully offline.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'check_review_md.py'
_spec = importlib.util.spec_from_file_location('check_review_md', _SCRIPT_PATH)
check_review_md = importlib.util.module_from_spec(_spec)
sys.modules['check_review_md'] = check_review_md
_spec.loader.exec_module(check_review_md)


def _use_results_dir(monkeypatch, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(check_review_md, 'RESULTS_DIR', path)


def test_find_review_md_missing_returns_none(tmp_path, monkeypatch):
    _use_results_dir(monkeypatch, tmp_path)
    assert check_review_md.find_review_md('1366') is None


def test_review_gate_default_is_not_the_source_plan_vault():
    assert 'TGW-Plan-Vault' not in str(check_review_md.RESULTS_DIR)
    assert check_review_md.RESULTS_DIR.is_absolute()


def test_find_review_md_single_id_match(tmp_path, monkeypatch):
    _use_results_dir(monkeypatch, tmp_path)
    (tmp_path / '1366-REVIEW.md').write_text('status: cleared\n', encoding='utf-8')
    found = check_review_md.find_review_md('1366')
    assert found is not None
    assert found.name == '1366-REVIEW.md'


def test_find_review_md_multi_id_batch_filename(tmp_path, monkeypatch):
    _use_results_dir(monkeypatch, tmp_path)
    # Matches the real naming convention observed in results/, e.g.
    # 1292-1293-clipd-rofi-picker-REVIEW.md and 1278-1279-REVIEW.md.
    (tmp_path / '1292-1293-clipd-rofi-picker-REVIEW.md').write_text('x', encoding='utf-8')
    assert check_review_md.find_review_md('1292') is not None
    assert check_review_md.find_review_md('1293') is not None
    assert check_review_md.find_review_md('1294') is None


def test_check_ids_fails_when_review_md_missing(tmp_path, monkeypatch, capsys):
    _use_results_dir(monkeypatch, tmp_path)
    (tmp_path / '1280-REVIEW.md').write_text('x', encoding='utf-8')
    # Simulates the real incident: 1280 present, the rest silently missing.
    rc = check_review_md.check_ids(['1280', '1282', '1284', '1288', '1291', '1297'])
    out = capsys.readouterr()
    assert rc == 1
    assert 'OK   #1280' in out.out
    assert 'MISS #1282' in out.out
    assert 'BLOCKED' in out.err
    assert '1282' in out.err and '1297' in out.err


def test_check_ids_passes_when_all_present(tmp_path, monkeypatch, capsys):
    _use_results_dir(monkeypatch, tmp_path)
    for todo_id in ('1280', '1282'):
        (tmp_path / f'{todo_id}-REVIEW.md').write_text('x', encoding='utf-8')
    rc = check_review_md.check_ids(['1280', '1282'])
    out = capsys.readouterr()
    assert rc == 0
    assert 'CLEAR' in out.out


def test_main_exits_nonzero_on_missing(tmp_path, monkeypatch):
    _use_results_dir(monkeypatch, tmp_path)
    rc = check_review_md.main(['9999'])
    assert rc == 1


def test_main_exits_zero_when_present(tmp_path, monkeypatch):
    _use_results_dir(monkeypatch, tmp_path)
    (tmp_path / '9999-REVIEW.md').write_text('x', encoding='utf-8')
    rc = check_review_md.main(['9999'])
    assert rc == 0

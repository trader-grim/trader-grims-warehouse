"""todo #1250 — unit tests for the invariant-E9 detector
(scripts/check_announce_script_run.py): flags scripts/*.py files that
define main() but never call tgw.logging.announce_script_run().
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'check_announce_script_run.py'
_spec = importlib.util.spec_from_file_location('check_announce_script_run', _SCRIPT_PATH)
checker = importlib.util.module_from_spec(_spec)
sys.modules['check_announce_script_run'] = checker
_spec.loader.exec_module(checker)


def _write(dir_path: Path, name: str, content: str) -> Path:
    p = dir_path / name
    p.write_text(content)
    return p


class TestFindOffenders:
    def test_script_with_main_and_announce_is_clean(self, tmp_path):
        _write(tmp_path, 'good.py', (
            "from tgw.logging import announce_script_run\n"
            "def main():\n"
            "    announce_script_run('good.py', 'does a thing')\n"
        ))
        assert checker.find_offenders(tmp_path) == []

    def test_script_with_main_and_no_announce_is_flagged(self, tmp_path):
        offender = _write(tmp_path, 'bad.py', (
            "def main():\n"
            "    print('does a thing, never announces')\n"
        ))
        assert checker.find_offenders(tmp_path) == [offender]

    def test_script_with_no_main_is_not_flagged_even_without_announce(self, tmp_path):
        _write(tmp_path, 'helper.py', (
            "def helper_fn():\n"
            "    return 1\n"
        ))
        assert checker.find_offenders(tmp_path) == []

    def test_mixed_directory_flags_only_the_offender(self, tmp_path):
        _write(tmp_path, 'good.py', (
            "from tgw.logging import announce_script_run\n"
            "def main():\n"
            "    announce_script_run('good.py', 'ok')\n"
        ))
        offender = _write(tmp_path, 'bad.py', "def main():\n    pass\n")
        _write(tmp_path, 'not_a_script.py', "def helper():\n    pass\n")

        assert checker.find_offenders(tmp_path) == [offender]

    def test_exempt_files_are_never_flagged(self, tmp_path):
        _write(tmp_path, 'check_announce_script_run.py', "def main():\n    pass\n")
        assert checker.find_offenders(tmp_path) == []


class TestMainExitCode:
    def test_exits_zero_when_clean(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path, 'good.py', (
            "from tgw.logging import announce_script_run\n"
            "def main():\n"
            "    announce_script_run('good.py', 'ok')\n"
        ))
        monkeypatch.setattr(sys, 'argv', ['prog', '--scripts-dir', str(tmp_path)])
        rc = checker.main()
        assert rc == 0
        assert 'OK' in capsys.readouterr().out

    def test_exits_nonzero_and_lists_offenders_when_dirty(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path, 'bad.py', "def main():\n    pass\n")
        monkeypatch.setattr(sys, 'argv', ['prog', '--scripts-dir', str(tmp_path)])
        rc = checker.main()
        assert rc == 1
        out = capsys.readouterr().out
        assert 'bad.py' in out
        assert 'invariant E9' in out


class TestRealScriptsDirIsClean:
    """Acceptance evidence: after the retrofit, the real scripts/ directory
    (the detector's default target) has zero offenders."""

    def test_real_scripts_dir_passes(self):
        real_scripts_dir = Path(__file__).resolve().parents[1] / 'scripts'
        offenders = checker.find_offenders(real_scripts_dir)
        assert offenders == [], f'scripts missing announce_script_run(): {offenders}'

    def test_stripping_and_restoring_the_announce_call_is_detected(self, tmp_path):
        # Deliberately strip the announce call from a copy of a real script,
        # confirm the detector flags it, then confirm the untouched original
        # still passes (proves the check is live, not vacuously true).
        real_script = (Path(__file__).resolve().parents[1] / 'scripts'
                        / 'requeue_ebay_draft_402_dead_letters.py')
        original_text = real_script.read_text(encoding='utf-8')
        assert 'announce_script_run(' in original_text

        stripped_dir = tmp_path / 'stripped'
        stripped_dir.mkdir()
        stripped_text = original_text.replace(
            "announce_script_run(\n"
            "        'requeue_ebay_draft_402_dead_letters.py',",
            "pass  # announce_script_run call removed for this test\n    if False:\n        (",
        )
        # Fall back to a blunt strip if the exact multi-line pattern above
        # doesn't match (keeps the test robust to minor reformatting).
        if 'announce_script_run(' in stripped_text:
            import re
            stripped_text = re.sub(r'announce_script_run\s*\([^)]*\)', 'pass', stripped_text)
        assert 'announce_script_run(' not in stripped_text

        _write(stripped_dir, 'requeue_ebay_draft_402_dead_letters.py', stripped_text)
        offenders = checker.find_offenders(stripped_dir)
        assert len(offenders) == 1
        assert offenders[0].name == 'requeue_ebay_draft_402_dead_letters.py'

        # The real, untouched script is unaffected by the copy above.
        assert checker.find_offenders(real_script.parent) == []

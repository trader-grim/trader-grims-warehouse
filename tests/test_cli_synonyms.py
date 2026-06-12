"""Tests for tgw CLI command synonyms (task 76).

Covers: health=status aliases, tgw help subcommand, -help → --help rewrite.
"""

import sys

import pytest

from tgw.api import _build_parser


def test_status_is_alias_for_health():
    """tgw status and tgw health both parse successfully."""
    p = _build_parser()
    args_h = p.parse_args(["health"])
    args_s = p.parse_args(["status"])
    assert args_h.op == "health"
    assert args_s.op == "status"
    # Both support the same flags
    args_no = p.parse_args(["status", "--no-ollama"])
    assert args_no.no_ollama is True


def test_help_subcommand_prints_and_exits(capsys):
    """tgw help prints the top-level help text and returns 0."""
    p = _build_parser()
    args = p.parse_args(["help"])
    assert args.op == "help"
    # Printing help must not raise
    p.print_help()
    out = capsys.readouterr().out
    assert "tgw" in out


def test_minus_help_rewritten_to_double_dash(monkeypatch):
    """-help in argv is rewritten to --help before parse_args."""
    monkeypatch.setattr(sys, "argv", ["tgw", "-help"])
    from tgw.api import main
    with pytest.raises(SystemExit) as exc:
        main()
    # --help exits with code 0
    assert exc.value.code == 0

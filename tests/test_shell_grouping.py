"""PP-SHELL-001 Tier 3 — grouped tgw --help and requeue → requeue-identify rename."""

import io

from tgw.api import _HELP_GROUPS, _build_parser

# ---------------------------------------------------------------------------
# requeue-identify rename
# ---------------------------------------------------------------------------

def test_requeue_identify_canonical_parse():
    parser = _build_parser()
    args = parser.parse_args(["requeue-identify", "--limit", "0"])
    assert args.op == "requeue-identify"


def test_requeue_deprecated_alias_parse():
    parser = _build_parser()
    args = parser.parse_args(["requeue", "--limit", "0"])
    assert args.op == "requeue"


def test_requeue_deprecated_alias_has_deprecated_in_help():
    parser = _build_parser()
    sub = next(
        a for a in parser._subparsers._group_actions
        if hasattr(a, '_choices_actions')
    )
    action = next(a for a in sub._choices_actions if a.dest == "requeue")
    assert "deprecated" in (action.help or "").lower()


def test_requeue_identify_not_deprecated():
    parser = _build_parser()
    sub = next(
        a for a in parser._subparsers._group_actions
        if hasattr(a, '_choices_actions')
    )
    action = next(a for a in sub._choices_actions if a.dest == "requeue-identify")
    assert "deprecated" not in (action.help or "").lower()


def test_requeue_and_requeue_identify_accept_same_args():
    parser = _build_parser()
    a1 = parser.parse_args(["requeue-identify", "--no-title", "--run"])
    a2 = parser.parse_args(["requeue", "--no-title", "--run"])
    assert a1.no_title == a2.no_title
    assert a1.run == a2.run


# ---------------------------------------------------------------------------
# grouped --help output
# ---------------------------------------------------------------------------

def _get_help(parser) -> str:
    buf = io.StringIO()
    parser.print_help(buf)
    return buf.getvalue()


def test_help_contains_all_group_names():
    parser = _build_parser()
    output = _get_help(parser)
    for group_name, _ in _HELP_GROUPS:
        assert group_name in output, f"group {group_name!r} missing from --help"


def test_help_contains_requeue_identify():
    parser = _build_parser()
    assert "requeue-identify" in _get_help(parser)


def test_help_does_not_list_deprecated_aliases():
    parser = _build_parser()
    output = _get_help(parser)
    # Deprecated aliases must not appear in the grouped section
    for alias in ("titleupdate", "locationupdate", "verifiedupdate",
                  "statusupdate", "setshipping", "whispertosuggest"):
        assert alias not in output, f"deprecated alias {alias!r} should not appear in grouped help"


def test_help_suppresses_flat_subcommand_listing():
    parser = _build_parser()
    output = _get_help(parser)
    # The flat {get,list,...} positionals block must not appear — the formatter suppresses it
    assert "{get," not in output
    assert "{get,list" not in output


def test_usage_line_uses_command_metavar():
    parser = _build_parser()
    output = _get_help(parser)
    # usage should show COMMAND, not the giant {get,list,...} enumeration
    assert "COMMAND" in output.split("\n")[0]


# Deprecated aliases, meta commands, and shorthand aliases intentionally absent
# from _HELP_GROUPS (they still work; they just don't appear in the grouped help).
_UNGROUPED_INTENTIONAL: frozenset[str] = frozenset({
    "help", "catlocmvall",                                          # meta / deprecated
    "titleupdate", "locationupdate", "verifiedupdate",              # deprecated aliases
    "statusupdate", "setshipping", "whispertosuggest", "requeue",   # deprecated aliases
    "status",                                                       # alias for health
    "note", "btw",                                                  # shorthands for suggest
})


def test_help_groups_cover_canonical_commands():
    """Every command in _HELP_GROUPS must be a real registered subcommand."""
    parser = _build_parser()
    sub = next(a for a in parser._subparsers._group_actions if hasattr(a, 'choices'))
    # Use the public `choices` dict — maps every registered name to its parser.
    all_names = set(sub.choices.keys())
    for _group_name, commands in _HELP_GROUPS:
        for cmd in commands:
            assert cmd in all_names, f"{cmd!r} in _HELP_GROUPS but not registered as a subcommand"


def test_all_grouped_commands_reachable():
    """Every registered non-deprecated command must appear in _HELP_GROUPS or the allowlist.

    Catches the case where a new subcommand is added to _build_parser() without
    also being added to _HELP_GROUPS, which would silently omit it from tgw --help.
    """
    parser = _build_parser()
    sub = next(a for a in parser._subparsers._group_actions if hasattr(a, 'choices'))
    all_names = set(sub.choices.keys())
    grouped = {cmd for _, cmds in _HELP_GROUPS for cmd in cmds}
    unaccounted = all_names - grouped - _UNGROUPED_INTENTIONAL
    assert not unaccounted, (
        f"Subcommands registered but missing from _HELP_GROUPS and _UNGROUPED_INTENTIONAL: "
        f"{sorted(unaccounted)}"
    )

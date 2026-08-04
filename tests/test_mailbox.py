"""PP-RUNNERCOMMS-001 — tests for tgw.api.cmd_mailbox_send and the `tgw
mailbox send` CLI wiring.

Naming/header convention here mirrors real notes already living in
docs/TGW-Plan-Vault/inbox/{claude,tigwa,dave}/ (reverse-engineered, not
invented): `<FROM-ACTOR>-<TYPE>-<slug>-<date>.md`, with a `# <Type>: <title>`
header and `**From:**`/`**To:**`/`**Date:**` metadata lines.
"""

from __future__ import annotations

from pathlib import Path

from tgw.api import _build_parser, cmd_mailbox_send


def _cfg(tmp_path: Path):
    inbox = tmp_path / "inbox"
    for actor in ("claude", "tigwa", "dave"):
        (inbox / actor).mkdir(parents=True, exist_ok=True)
    (inbox / "archive").mkdir(parents=True, exist_ok=True)
    (inbox / "queued").mkdir(parents=True, exist_ok=True)
    return {"plan_vault_path": tmp_path, "plan_inbox_path": inbox}


def test_send_writes_correctly_named_file_with_expected_header(tmp_path):
    cfg = _cfg(tmp_path)
    result = cmd_mailbox_send(
        cfg, "tigwa", "please review the wiring", from_actor="claude",
        msg_type="request", subject="MCP wiring review",
    )
    assert result["ok"] is True
    dest = Path(result["file"])
    assert dest.parent == cfg["plan_inbox_path"] / "tigwa"
    assert dest.name.startswith("CLAUDE-REQUEST-mcp-wiring-review-")
    assert dest.name.endswith(".md")

    text = dest.read_text(encoding="utf-8")
    assert text.startswith("# Request: MCP wiring review")
    assert "**From:** claude" in text
    assert "**To:** tigwa" in text
    assert "**Date:**" in text
    assert "please review the wiring" in text


def test_send_derives_slug_from_text_when_no_subject(tmp_path):
    cfg = _cfg(tmp_path)
    result = cmd_mailbox_send(cfg, "dave", "the pump is broken again please look", from_actor="tigwa")
    assert result["ok"] is True
    dest = Path(result["file"])
    assert dest.name.startswith("TIGWA-NOTE-the-pump-is-broken-again-please-")


def test_send_avoids_filename_collision(tmp_path):
    cfg = _cfg(tmp_path)
    first = cmd_mailbox_send(cfg, "claude", "hello", from_actor="dave", subject="hi", msg_type="note")
    second = cmd_mailbox_send(cfg, "claude", "hello again", from_actor="dave", subject="hi", msg_type="note")
    assert first["ok"] is True and second["ok"] is True
    assert first["file"] != second["file"]
    assert Path(first["file"]).exists()
    assert Path(second["file"]).exists()


def test_send_records_todo_id_in_header(tmp_path):
    cfg = _cfg(tmp_path)
    result = cmd_mailbox_send(cfg, "tigwa", "blocked, need a decision", from_actor="claude", todo_id=1484)
    text = Path(result["file"]).read_text(encoding="utf-8")
    assert "**Todo:** #1484" in text


def test_send_rejects_reserved_holding_dirs(tmp_path):
    cfg = _cfg(tmp_path)
    for reserved in ("archive", "queued"):
        result = cmd_mailbox_send(cfg, reserved, "hello")
        assert result["ok"] is False
        assert "holding area" in result["error"]


def test_send_rejects_empty_text(tmp_path):
    cfg = _cfg(tmp_path)
    result = cmd_mailbox_send(cfg, "claude", "   ")
    assert result["ok"] is False


def test_send_rejects_empty_to_actor(tmp_path):
    cfg = _cfg(tmp_path)
    result = cmd_mailbox_send(cfg, "", "hello")
    assert result["ok"] is False


def test_send_creates_mailbox_for_brand_new_addressable_actor(tmp_path):
    cfg = _cfg(tmp_path)
    result = cmd_mailbox_send(cfg, "leotha", "welcome aboard", from_actor="claude")
    # not a known actor and no pre-existing dir -- rejected with guidance,
    # NOT silently created, unless the dir already exists (see PP note: a
    # brand-new addressable *persona*, not a typo, should have its dir made
    # deliberately first).
    assert result["ok"] is False
    assert "leotha" in result["error"]

    # Once the dir exists (the deliberate "this actor is real now" step),
    # sends to it succeed like any other actor.
    (cfg["plan_inbox_path"] / "leotha").mkdir(parents=True)
    result2 = cmd_mailbox_send(cfg, "leotha", "welcome aboard", from_actor="claude")
    assert result2["ok"] is True


def test_cli_parses_mailbox_send():
    p = _build_parser()
    args = p.parse_args([
        "mailbox", "send", "tigwa", "hello there",
        "--from", "claude", "--type", "note", "--subject", "hi", "--todo", "1484",
    ])
    assert args.op == "mailbox"
    assert args.mailbox_op == "send"
    assert args.to_actor == "tigwa"
    assert args.text == "hello there"
    assert args.from_actor == "claude"
    assert args.msg_type == "note"
    assert args.subject == "hi"
    assert args.todo_id == 1484


def test_cli_defaults_from_actor_to_claude():
    p = _build_parser()
    args = p.parse_args(["mailbox", "send", "dave", "hi"])
    assert args.from_actor == "claude"
    assert args.msg_type == "NOTE"
    assert args.todo_id is None

"""PP-EMAIL-001 — tests for the notify.py SMTP backend.

stdlib smtplib is stubbed; no real mail is sent. Verifies the backend is
fail-soft (no-op without config), kept out of default backends, and builds a
correct message when configured.
"""

import smtplib

import pytest

import tgw.notify as notify


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.tls = False
        self.login_args = None
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, pw):
        self.login_args = (user, pw)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)


def test_smtp_noop_without_host():
    notify._backend_smtp("t", "m", "info", {})
    assert _FakeSMTP.instances == []


def test_smtp_noop_without_recipient():
    notify._backend_smtp("t", "m", "info", {"smtp_host": "mail.example.com"})
    assert _FakeSMTP.instances == []  # no smtp_to / smtp_from


def test_smtp_sends_with_full_config():
    cfg = {
        "smtp_host": "mail.example.com",
        "smtp_port": 587,
        "smtp_from": "tgw@example.com",
        "smtp_to": "ops@example.com",
        "smtp_username": "tgw@example.com",
        "smtp_password": "app-pw",
    }
    notify._backend_smtp("Worker failed", "ebay_draft crashed", "error", cfg)
    assert len(_FakeSMTP.instances) == 1
    s = _FakeSMTP.instances[0]
    assert (s.host, s.port) == ("mail.example.com", 587)
    assert s.tls is True
    assert s.login_args == ("tgw@example.com", "app-pw")
    msg = s.sent[0]
    assert msg["Subject"] == "TGW [error]: Worker failed"
    assert msg["From"] == "tgw@example.com"
    assert msg["To"] == "ops@example.com"
    assert "ebay_draft crashed" in msg.get_content()


def test_smtp_skips_login_without_creds():
    cfg = {"smtp_host": "mail.example.com", "smtp_to": "ops@example.com",
           "smtp_from": "tgw@example.com", "smtp_use_tls": False}
    notify._backend_smtp("t", "m", "info", cfg)
    s = _FakeSMTP.instances[0]
    assert s.tls is False
    assert s.login_args is None


def test_smtp_failure_is_swallowed(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    # Must not raise.
    notify._backend_smtp("t", "m", "info",
                         {"smtp_host": "x", "smtp_to": "a@b.c"})


def test_smtp_not_in_default_backends():
    n = notify.Notifier()
    assert "smtp" not in n._backends   # default is log+file only


def test_smtp_registered_in_backend_table():
    assert notify._BACKENDS["smtp"] is notify._backend_smtp
    assert notify._BACKENDS["email"] is notify._backend_smtp

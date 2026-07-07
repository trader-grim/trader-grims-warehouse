"""audit#1143 / todo #1235 (merged #1162+#1177) — save_token_state() in both
get_access_token.py and refresh_access_token.py wrote straight to TOKEN_PATH
with plain write_text(); a crash mid-write corrupts the sole copy of the
eBay refresh token, forcing full browser re-consent. Now atomic tmp+rename
via the shared tgw.apis.ebay._token_io.atomic_write_token_json() helper
(audit#1143 #1243 follow-up: the two near-identical inline blocks were
deduplicated into one, always chmod 0600 — never reuse items.atomic_write_text
here, which preserves/defaults to 0o660 group-writable, wrong for a secret).
"""

import json
import stat
import tempfile
from pathlib import Path

import tgw.apis.ebay.get_access_token as gat
import tgw.apis.ebay.refresh_access_token as rat
from tgw.apis.ebay._token_io import atomic_write_token_json


def _no_leftover_tmp_files(parent: Path, keep: str) -> bool:
    return all(p.name == keep for p in parent.iterdir())


def test_atomic_write_token_json_always_0600_even_if_dir_is_group_writable():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'ebay-token.json'
        atomic_write_token_json(path, '{"a": 1}\n')
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert json.loads(path.read_text()) == {'a': 1}


def test_get_access_token_save_token_state_atomic(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        token_path = Path(d) / 'ebay-token.json'
        monkeypatch.setattr(gat, 'TOKEN_PATH', token_path)

        gat.save_token_state({'access_token': 'abc', 'expiry': 123})

        assert json.loads(token_path.read_text()) == {'access_token': 'abc', 'expiry': 123}
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
        assert _no_leftover_tmp_files(token_path.parent, 'ebay-token.json')


def test_refresh_access_token_save_token_state_atomic(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        token_path = Path(d) / 'ebay-token.json'
        monkeypatch.setattr(rat, 'TOKEN_PATH', token_path)

        rat.save_token_state({'access_token': 'xyz', 'expiry': 456})

        assert json.loads(token_path.read_text()) == {'access_token': 'xyz', 'expiry': 456}
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
        assert _no_leftover_tmp_files(token_path.parent, 'ebay-token.json')

"""load_config() must tolerate a secrets_root it can't even stat() (PP-CLIP-001
Phase 2, todo #1055 discovery): /opt/TGW/secrets is 700 tgw:tgw, and `tgw clip`
runs as the operator's own user, not tgw (nix/tgw/home.nix's fish wrapper).
Before the fix, _api_key_path.exists() raised PermissionError uncaught."""

from pathlib import Path
from unittest import mock

from tgw.config import load_config


def test_load_config_tolerates_permission_denied_on_api_key_path(tmp_path):
    cfg_path = tmp_path / "tgw-api-config.json"
    cfg_path.write_text("{}")

    real_exists = Path.exists

    def _raise_for_secrets(self):
        if self.name == "tgw-api-key.json":
            raise PermissionError(13, "Permission denied")
        return real_exists(self)

    with mock.patch.object(Path, "exists", _raise_for_secrets):
        cfg = load_config(cfg_path)

    assert cfg is not None


def test_load_config_still_reads_api_key_when_accessible(tmp_path):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "tgw-api-key.json").write_text('{"api_key": "test-key-123"}')
    cfg_path = tmp_path / "tgw-api-config.json"
    cfg_path.write_text(f'{{"secrets_root": "{secrets_dir}"}}')

    cfg = load_config(cfg_path)

    assert cfg.get("api_key") == "test-key-123"

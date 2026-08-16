import os
from unittest.mock import Mock

import pytest

import tgw.effect_completion_store as store_module
from tgw.effect_completion_store import ImmutableEffectCompletionStore
from tgw.effect_handlers import EffectExecutionReceipt, EffectOutcome


def _receipt():
    return EffectExecutionReceipt(
        schema="tgw-effect-execution-receipt/v1", request_id="w09", authority_receipt_id="authority:1",
        effect_hash="sha256:" + "1" * 64, effect_kind="approval-platform-bootstrap-deployment",
        generation="release-b", handler_id="governed-application-bootstrap-install@2",
        outcome=EffectOutcome.SUCCEEDED, evidence=("health:ok",),
    ).sealed_mapping()


def _store(tmp_path):
    root = tmp_path / "terminal"; root.mkdir(mode=0o700)
    return ImmutableEffectCompletionStore(
        root, sink_id="w09-terminal", descriptor_hash="sha256:" + "2" * 64,
        trusted_uid=os.getuid(),
    ), root


def test_store_persists_and_idempotently_rechecks_exact_terminal_bytes(tmp_path):
    store, root = _store(tmp_path)
    first = store.persist(_receipt()); second = store.persist(_receipt())
    assert first == second
    artifact = next(root.iterdir())
    assert artifact.stat().st_mode & 0o777 == 0o400
    assert artifact.stat().st_nlink == 1
    store.close()


def test_store_rejects_precreated_wrong_mode_or_content(tmp_path):
    store, root = _store(tmp_path); receipt = _receipt()
    name = receipt["receipt_hash"].removeprefix("sha256:") + ".json"
    (root / name).write_text("different\n", encoding="utf-8")
    with pytest.raises(OSError, match="readback mismatch"):
        store.persist(receipt)
    store.close()


def test_store_loops_short_writes_and_rejects_hardlinked_existing_artifact(tmp_path, monkeypatch):
    store, root = _store(tmp_path); real_write = os.write; calls = 0
    def short(fd, body):
        nonlocal calls
        calls += 1
        return real_write(fd, body[:max(1, len(body) // 2)])
    monkeypatch.setattr(store_module.os, "write", short)
    receipt = _receipt(); store.persist(receipt)
    assert calls > 1
    store.close()

    other = tmp_path / "other"; other.mkdir(mode=0o700); other.chmod(0o700)
    other_store, other_root = _store(other)
    name = receipt["receipt_hash"].removeprefix("sha256:") + ".json"
    source = tmp_path / "hardlink-source"; source.write_bytes(__import__("json").dumps(receipt, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    source.chmod(0o400); os.link(source, other_root / name)
    with pytest.raises(OSError, match="readback mismatch"):
        other_store.persist(receipt)
    other_store.close()


def test_store_fsync_failure_removes_partial_artifact_or_reports_cleanup_ambiguity(tmp_path, monkeypatch):
    store, root = _store(tmp_path)
    monkeypatch.setattr(store_module.os, "fsync", Mock(side_effect=OSError("fsync failed")))
    with pytest.raises(OSError):
        store.persist(_receipt())
    assert list(root.iterdir()) == []
    store.close()


def test_store_detects_named_root_replacement_after_held_open(tmp_path):
    store, root = _store(tmp_path)
    moved = tmp_path / "old-terminal"; root.rename(moved); root.mkdir(mode=0o700)
    with pytest.raises(OSError, match="root identity changed"):
        store.persist(_receipt())
    store.close()


def test_store_rejects_symlink_root_and_closes_descriptors(tmp_path):
    real = tmp_path / "real"; real.mkdir(mode=0o700)
    link = tmp_path / "link"; link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        ImmutableEffectCompletionStore(
            link, sink_id="w09-terminal", descriptor_hash="sha256:" + "2" * 64,
            trusted_uid=os.getuid(),
        )
    store = ImmutableEffectCompletionStore(
        real, sink_id="w09-terminal", descriptor_hash="sha256:" + "2" * 64,
        trusted_uid=os.getuid(),
    )
    root_fd, parent_fd = store._root_fd, store._parent_fd
    store.close()
    with pytest.raises(OSError): os.fstat(root_fd)
    with pytest.raises(OSError): os.fstat(parent_fd)

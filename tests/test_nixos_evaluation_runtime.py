import hashlib
import os
from pathlib import Path

import pytest

from tgw.nixos_evaluation_runtime import (
    KNOWN_HOSTS_PATH,
    SOURCE_PATH,
    ExactArtifactResolver,
    RuntimeCompositionError,
    compose_reviewed_evaluation_provider,
    preflight_reviewed_evaluation,
)


def parameters():
    return {
        "artifact_ref": "artifact:sha256:d78726247e9168c0878975ff4f39acd6a1c1dc063febd3b8370713f5053e8095",
        "source_archive_sha256": "sha256:d78726247e9168c0878975ff4f39acd6a1c1dc063febd3b8370713f5053e8095",
        "known_hosts_sha256": "sha256:2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf",
    }


def test_exact_runtime_resolves_both_immutable_artifacts_without_repo_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("tgw.nixos_evaluation_runtime.FAILURE_RECEIPT_ROOT", tmp_path / "failures")
    provider, receipt = compose_reviewed_evaluation_provider(parameters(), invoke=lambda *a, **k: None)
    assert provider.resolve_artifact(parameters()["artifact_ref"]) == SOURCE_PATH
    assert provider.known_hosts == KNOWN_HOSTS_PATH
    assert receipt["artifacts"]["source_archive"]["mode"] == "0444"
    assert receipt["artifacts"]["known_hosts"]["mode"] == "0444"
    assert receipt["ssh_started"] is False
    assert receipt["failure_store"]["ready"] is True
    provider.failure_store.close()


def test_malicious_repo_paths_and_unregistered_identities_are_never_selected(tmp_path):
    malicious = tmp_path / "known_hosts"
    malicious.write_text("100.107.99.66 ssh-ed25519 AAAA\n")
    malicious.chmod(0o444)
    with pytest.raises(RuntimeCompositionError, match="not registered"):
        ExactArtifactResolver()("artifact:sha256:" + hashlib.sha256(malicious.read_bytes()).hexdigest())
    broadened = {**parameters(), "known_hosts_sha256": "sha256:" + hashlib.sha256(malicious.read_bytes()).hexdigest()}
    with pytest.raises(RuntimeCompositionError, match="known-hosts"):
        compose_reviewed_evaluation_provider(broadened)
    assert malicious != KNOWN_HOSTS_PATH and str(malicious) not in str(preflight_reviewed_evaluation(parameters()))


def test_preflight_rejects_mutable_or_wrong_stable_stat(monkeypatch):
    original = Path.lstat

    class Changed:
        st_mode = 0o100664
        st_uid = os.geteuid()
        st_size = 8_826_880

    monkeypatch.setattr(Path, "lstat", lambda self: Changed() if self == SOURCE_PATH else original(self))
    with pytest.raises(RuntimeCompositionError, match="source_archive"):
        preflight_reviewed_evaluation(parameters())

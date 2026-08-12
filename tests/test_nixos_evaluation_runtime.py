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
        "artifact_ref": "artifact:sha256:c288e2514b12bad292e6c712280bda1e071effe74deb7f095ad23be698a94fbe",
        "source_archive_sha256": "sha256:c288e2514b12bad292e6c712280bda1e071effe74deb7f095ad23be698a94fbe",
        "known_hosts_sha256": "sha256:2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf",
    }


def test_exact_runtime_resolves_both_immutable_artifacts_without_repo_fallback():
    provider, receipt = compose_reviewed_evaluation_provider(parameters(), invoke=lambda *a, **k: None)
    assert provider.resolve_artifact(parameters()["artifact_ref"]) == SOURCE_PATH
    assert provider.known_hosts == KNOWN_HOSTS_PATH
    assert receipt["artifacts"]["source_archive"]["mode"] == "0444"
    assert receipt["artifacts"]["known_hosts"]["mode"] == "0444"
    assert receipt["ssh_started"] is False


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
        st_size = 8_704_000

    monkeypatch.setattr(Path, "lstat", lambda self: Changed() if self == SOURCE_PATH else original(self))
    with pytest.raises(RuntimeCompositionError, match="source_archive"):
        preflight_reviewed_evaluation(parameters())

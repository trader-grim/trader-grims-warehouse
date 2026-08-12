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
        "artifact_ref": "artifact:sha256:0dd33b208fe978cf393e44656636f9717716a9a5aef8da1475c67b17d5947a58",
        "source_archive_sha256": "sha256:0dd33b208fe978cf393e44656636f9717716a9a5aef8da1475c67b17d5947a58",
        "known_hosts_sha256": "sha256:2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf",
    }


def effect(parameters_value=None):
    return {"kind": "nixos-reviewed-evaluation", "generation": "eval-1", "parameters": parameters_value or parameters()}


def test_exact_runtime_resolves_both_immutable_artifacts_without_repo_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("tgw.nixos_evaluation_runtime.FAILURE_RECEIPT_ROOT", tmp_path / "failures")
    provider, receipt = compose_reviewed_evaluation_provider(effect(), invoke=lambda *a, **k: None)
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
        compose_reviewed_evaluation_provider(effect(broadened))
    assert malicious != KNOWN_HOSTS_PATH and str(malicious) not in str(preflight_reviewed_evaluation(parameters()))


def test_composition_rejects_flattened_or_duplicate_generation():
    with pytest.raises(RuntimeCompositionError, match="typed evaluation effect"):
        compose_reviewed_evaluation_provider(parameters())
    duplicated = {**parameters(), "generation": "different"}
    with pytest.raises(RuntimeCompositionError, match="must not be duplicated"):
        compose_reviewed_evaluation_provider(effect(duplicated))


def test_preflight_rejects_mutable_or_wrong_stable_stat(monkeypatch):
    original = Path.lstat

    class Changed:
        st_mode = 0o100664
        st_uid = os.geteuid()
        st_size = 8_847_360

    monkeypatch.setattr(Path, "lstat", lambda self: Changed() if self == SOURCE_PATH else original(self))
    with pytest.raises(RuntimeCompositionError, match="source_archive"):
        preflight_reviewed_evaluation(parameters())

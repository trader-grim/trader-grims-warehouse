from copy import deepcopy
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from tgw.nix_observer_render_evaluation import canonical
from tgw.nix_observer_render_runtime import ClosedRenderProvider, RenderRuntimeError

_SPEC = spec_from_file_location("render_contract_fixtures", Path(__file__).with_name("test_nix_observer_render_evaluation.py"))
assert _SPEC and _SPEC.loader
_FIXTURES = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURES)
NOW, request, result = _FIXTURES.NOW, _FIXTURES.request, _FIXTURES.result


class Store:
    def __init__(self):
        self.values = []

    def persist(self, value):
        self.values.append(value)
        return {"artifact_ref": "artifact:sha256:" + "a" * 64}


def artifacts(tmp_path, req):
    archive = tmp_path / "archive.tar"
    archive.write_bytes(b"archive")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    archive.chmod(0o444)
    hosts.chmod(0o444)
    req["archive_sha256"] = "sha256:" + __import__("hashlib").sha256(b"archive").hexdigest()
    req["artifact_ref"] = "artifact:" + req["archive_sha256"]
    unsigned = dict(req)
    unsigned.pop("request_sha256")
    req["request_sha256"] = "sha256:" + __import__("hashlib").sha256(canonical(unsigned)).hexdigest()
    return archive, hosts


def test_closed_provider_holds_artifacts_and_validates_success(tmp_path, monkeypatch):
    from tgw import nix_observer_render_runtime as module

    req = request()
    archive, hosts = artifacts(tmp_path, req)
    monkeypatch.setattr(module, "SOURCE_REF", req["artifact_ref"])
    seen = {}

    def transport(**kwargs):
        seen.update(kwargs)
        value = result(req)
        value["systemd_verify"]["observed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        value["receipt_sha256"] = "sha256:" + __import__("hashlib").sha256(canonical({k: v for k, v in value.items() if k != "receipt_sha256"})).hexdigest()
        return value

    provider = ClosedRenderProvider(transport, Store(), archive, hosts)
    monkeypatch.setattr(module, "_held", lambda path, **kwargs: (open(path, "rb").fileno(), {}))

    # Use real held descriptors with fixture-specific identities through a narrow wrapper.
    def held(path, **_kwargs):
        fd = __import__("os").open(path, __import__("os").O_RDONLY)
        return fd, {}

    monkeypatch.setattr(module, "_held", held)
    assert provider.execute(req)["outcome"] == "VERIFIED"
    assert seen["archive_fd"] >= 0 and seen["known_hosts_fd"] >= 0


def test_failure_is_bound_and_persisted(tmp_path, monkeypatch):
    from tgw import nix_observer_render_runtime as module

    req = request()
    archive, hosts = artifacts(tmp_path, req)
    monkeypatch.setattr(module, "SOURCE_REF", req["artifact_ref"])
    store = Store()
    failure = {"schema": "tgw-nix-observer-render-evaluation-failure/v1", "request_sha256": req["request_sha256"], "outcome": "FAILED", "stage": "nix-build"}
    failure["receipt_sha256"] = "sha256:" + __import__("hashlib").sha256(canonical(failure)).hexdigest()
    monkeypatch.setattr(module, "_held", lambda path, **kwargs: (__import__("os").open(path, __import__("os").O_RDONLY), {}))
    with pytest.raises(RenderRuntimeError, match="terminated FAILED"):
        ClosedRenderProvider(lambda **_: deepcopy(failure), store, archive, hosts).execute(req)
    assert store.values == [failure]


def test_unknown_schema_and_wrong_source_fail_before_coercion(tmp_path, monkeypatch):
    from tgw import nix_observer_render_runtime as module

    req = request()
    archive, hosts = artifacts(tmp_path, req)
    monkeypatch.setattr(module, "SOURCE_REF", req["artifact_ref"])
    monkeypatch.setattr(module, "_held", lambda path, **kwargs: (__import__("os").open(path, __import__("os").O_RDONLY), {}))
    with pytest.raises(RenderRuntimeError, match="unknown terminal"):
        ClosedRenderProvider(lambda **_: {"schema": "old-schema"}, Store(), archive, hosts).execute(req)
    wrong = deepcopy(req)
    wrong["artifact_ref"] = "artifact:sha256:" + "0" * 64
    unsigned = dict(wrong)
    unsigned.pop("request_sha256")
    wrong["request_sha256"] = "sha256:" + __import__("hashlib").sha256(canonical(unsigned)).hexdigest()
    with pytest.raises(Exception):
        ClosedRenderProvider(lambda **_: {}, Store(), archive, hosts).execute(wrong)

"""Closed production caller for the A3 successor remote evaluator.

This is the only production composition that may call ``remote.execute``.  Test
code may inject a runner directly into that low-level function; production cannot.
"""

from __future__ import annotations

import os
import selectors
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tgw import nixos_a3_successor_remote as remote
from tgw.nixos_a3_successor_evaluation import A3EvaluationError, digest, validate_file_identity, validate_request
from tgw.nixos_a3_successor_remote import Completed

TRANSPORT_SCHEMA = "tgw-nixos-a3-successor-production-transport/v1"


def _read_exact(value: Mapping[str, Any], *, label: str, executable: bool = False) -> bytes:
    identity = validate_file_identity(value, label=label) if executable else dict(value)
    if not executable and set(identity) != {"path", "sha256", "size", "uid", "gid", "mode"}:
        raise A3EvaluationError(f"{label} identity is not exact")
    fd = os.open(identity["path"], os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        raw = bytearray()
        while len(raw) <= identity["size"]:
            block = os.read(fd, min(1024 * 1024, identity["size"] + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    named = os.stat(identity["path"], follow_symlinks=False)
    observed = {"sha256": digest(bytes(raw)), "size": len(raw), "uid": before.st_uid, "gid": before.st_gid, "mode": stat.S_IMODE(before.st_mode)}
    if (
        not stat.S_ISREG(before.st_mode)
        or any(observed[key] != identity[key] for key in observed)
        or (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size)
        or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise A3EvaluationError(f"{label} held identity changed")
    return bytes(raw)


class ExactSubprocessRunner:
    """Fixed subprocess implementation; no shell, ambient env, or fallback."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: int,
        max_output: int,
        pass_fds: Sequence[int],
    ) -> Completed:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            pass_fds=tuple(pass_fds),
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout
        while selector.get_map():
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise A3EvaluationError("fixed production subprocess timed out")
            for key, _ in selector.select(0.1):
                block = os.read(key.fd, 65536)
                if block:
                    output[key.data].extend(block)
                    if len(output["stdout"]) + len(output["stderr"]) > max_output:
                        process.kill()
                        process.wait()
                        raise A3EvaluationError("fixed production subprocess exceeded output bound")
                else:
                    selector.unregister(key.fileobj)
        return Completed(process.wait(), bytes(output["stdout"]), bytes(output["stderr"]))


@dataclass(frozen=True)
class ProductionTransportComposition:
    schema: str
    request_sha256: str
    runner: Mapping[str, Any]
    tgw_archive: Mapping[str, Any]
    integration_archive: Mapping[str, Any]
    scratch_parent: str
    target_host: str
    receipt_store_id: str
    ssh_transport_identity: str
    bootstrap_identity: str

    @property
    def receipt_sha256(self) -> str:
        return digest(self.__dict__)


class A3SuccessorProductionTransport:
    production_transport = True

    def __init__(self, composition: ProductionTransportComposition):
        self.composition = composition
        if (
            composition.schema != TRANSPORT_SCHEMA
            or composition.target_host != "tgw-prod"
            or not composition.receipt_store_id.startswith("a3-successor-receipts:sha256:")
            or len(composition.receipt_store_id) != len("a3-successor-receipts:sha256:") + 64
            or not composition.ssh_transport_identity.startswith("ssh-transport:sha256:")
            or len(composition.ssh_transport_identity) != len("ssh-transport:sha256:") + 64
            or not composition.bootstrap_identity.startswith("a3-bootstrap:sha256:")
            or len(composition.bootstrap_identity) != len("a3-bootstrap:sha256:") + 64
        ):
            raise A3EvaluationError("production transport composition is outside the closed identity set")
        runner_raw = _read_exact(composition.runner, label="remote runner", executable=False)
        installed_runner = Path(remote.__file__).resolve(strict=True)
        if Path(composition.runner["path"]).resolve(strict=True) != installed_runner or digest(runner_raw) != composition.runner["sha256"]:
            raise A3EvaluationError("composition runner is not the exact installed remote helper consumed")
        scratch = Path(composition.scratch_parent)
        metadata = scratch.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise A3EvaluationError("production scratch parent is not root-owned mode 0700")

    def __call__(self, request_value: Mapping[str, Any]) -> Mapping[str, Any]:
        request = validate_request(request_value)
        if request["request_sha256"] != self.composition.request_sha256 or request["target"]["host"] != self.composition.target_host:
            raise A3EvaluationError("production transport request binding mismatch")
        source = _read_exact(self.composition.tgw_archive, label="product archive")
        integration = _read_exact(self.composition.integration_archive, label="integration archive")
        if digest(source) != request["source"]["archive_sha256"] or len(source) != request["source"]["archive_size"]:
            raise A3EvaluationError("production product archive differs from request")
        if digest(integration) != request["integration"]["archive_sha256"] or len(integration) != request["integration"]["archive_size"]:
            raise A3EvaluationError("production integration archive differs from request")
        return remote.execute(
            request,
            tgw_archive=source,
            integration_archive=integration,
            runner=ExactSubprocessRunner(),
            scratch_parent=Path(self.composition.scratch_parent),
            allow_fixture=False,
        )

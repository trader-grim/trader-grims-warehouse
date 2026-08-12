"""TGW host bindings for the otherwise host-neutral operator console plugin."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.operator_console_plugin import OperatorConsoleMount
from tgw.plan_authority import PostgresAuthorityStore

DEFAULT_PLAN_ROOT = Path("/opt/TGW/library/plans")
_IDENTITY = re.compile(r"^[A-Za-z0-9:._-]+$")


def plan_root(config: Mapping[str, Any]) -> Path:
    return Path(config.get("plan_vault_path") or DEFAULT_PLAN_ROOT).resolve()


def current_plan_commit(config_provider: Callable[[], Mapping[str, Any]]) -> str:
    root = plan_root(config_provider())
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "HEAD"],
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f"standalone Plan commit unavailable: {result.stderr.strip()}")
    return result.stdout.strip()


def load_solution(config_provider: Callable[[], Mapping[str, Any]], solution_hash: str) -> Mapping[str, Any]:
    """Load one exact persisted solution; absence remains an explicit hold."""
    if not _IDENTITY.fullmatch(solution_hash):
        raise ValueError("invalid solution identity")
    path = plan_root(config_provider()) / "plan" / "execution" / "solutions" / f"{solution_hash}.json"
    if not path.is_file():
        raise ValueError(f"persisted Plan solution unavailable: {solution_hash}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("solution_hash") != solution_hash:
        raise ValueError("persisted Plan solution identity mismatch")
    return payload


class ConfiguredAuthorityStore:
    """Late-bound DSN proxy so FastAPI import does not open or configure DB state."""

    def __init__(self, config_provider: Callable[[], Mapping[str, Any]]):
        self.config_provider = config_provider

    def _store(self) -> PostgresAuthorityStore:
        dsn = self.config_provider().get("postgres_dsn")
        if not isinstance(dsn, str) or not dsn:
            raise RuntimeError("PlanAuthority database is not configured")
        return PostgresAuthorityStore(dsn)

    def create_request(self, request):
        return self._store().create_request(request)

    def decide(self, decision):
        return self._store().decide(decision)

    def consume(self, request_id, *, effect_hash, generation):
        return self._store().consume(request_id, effect_hash=effect_hash, generation=generation)

    def get(self, request_id):
        return self._store().get(request_id)

    def list(self, limit=100):
        return self._store().list(limit)

    def events(self, request_id):
        return self._store().events(request_id)


def configured_console_mount(
    config_provider: Callable[[], Mapping[str, Any]],
    *,
    require_operator: Callable[[], Any],
    require_executor: Callable[[], Any],
) -> OperatorConsoleMount:
    return OperatorConsoleMount(
        store=ConfiguredAuthorityStore(config_provider),
        current_plan_commit=lambda: current_plan_commit(config_provider),
        load_solution=lambda identity: load_solution(config_provider, identity),
        require_operator=require_operator,
        require_executor=require_executor,
    )

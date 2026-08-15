"""Strict, agent-neutral TGW environment registry validation and resolution."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml


class EnvironmentRegistryError(ValueError):
    """The registry is invalid or cannot resolve the requested identity."""


class RetiredHostError(EnvironmentRegistryError):
    """A retired host name was used as current authority."""


_CONTENT_KEYS = {
    "hosts", "retired_hosts", "repositories", "plans", "releases",
    "instructions", "agents", "unknowns",
}
_SECRET_WORDS = {"password", "secret", "token", "private_key", "credential"}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise EnvironmentRegistryError("registry is not canonical JSON data") from exc


def content_revision(content: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(content)).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise EnvironmentRegistryError(f"{label} must be a string-keyed mapping")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise EnvironmentRegistryError(f"{label} must be a canonical non-empty string")
    return value


def _strings(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise EnvironmentRegistryError(f"{label} must be a string list")
    if not all(isinstance(item, str) and item.strip() and item == item.strip() for item in value):
        raise EnvironmentRegistryError(f"{label} must be a string list")
    if len(value) != len(set(value)):
        raise EnvironmentRegistryError(f"{label} contains duplicates")
    return value


def _timestamp(value: Any, label: str) -> None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EnvironmentRegistryError(f"{label} is not a timestamp") from exc
    else:
        raise EnvironmentRegistryError(f"{label} is not a timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EnvironmentRegistryError(f"{label} must be timezone-aware")


def _absolute(value: Any, label: str) -> str:
    path = PurePosixPath(_string(value, label))
    if not path.is_absolute() or ".." in path.parts:
        raise EnvironmentRegistryError(f"{label} must be an absolute contained path")
    return str(path)


def _reject_secret_values(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in _SECRET_WORDS or any(word in normalized for word in _SECRET_WORDS):
                raise EnvironmentRegistryError(
                    f"registry cannot contain secret-bearing key: {'.'.join((*path, key))}",
                )
            _reject_secret_values(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_values(child, (*path, str(index)))


def validate_registry(raw: Mapping[str, Any]) -> dict[str, Any]:
    registry = _mapping(dict(raw), "registry")
    if set(registry) != {"schema", "revision", "generated_at", "content"}:
        raise EnvironmentRegistryError("registry top-level fields are not exact")
    if registry["schema"] != "tgw-environment/v1":
        raise EnvironmentRegistryError("unsupported registry schema")
    _timestamp(registry["generated_at"], "generated_at")
    content = _mapping(registry["content"], "content")
    if set(content) != _CONTENT_KEYS:
        raise EnvironmentRegistryError("registry content fields are not exact")
    if registry["revision"] != content_revision(content):
        raise EnvironmentRegistryError("registry revision does not match content")
    _reject_secret_values(content)

    hosts = _mapping(content["hosts"], "hosts")
    if not hosts:
        raise EnvironmentRegistryError("registry requires current hosts")
    names: set[str] = set()
    for role, entry_raw in hosts.items():
        entry = _mapping(entry_raw, f"host {role}")
        if set(entry) != {"canonical_name", "roles", "verified_at", "sources"}:
            raise EnvironmentRegistryError(f"host {role} fields are not exact")
        name = _string(entry["canonical_name"], f"host {role} canonical_name")
        if name in names:
            raise EnvironmentRegistryError("canonical host names must be unique")
        names.add(name)
        _strings(entry["roles"], f"host {role} roles")
        _strings(entry["sources"], f"host {role} sources")
        _timestamp(entry["verified_at"], f"host {role} verified_at")

    retired = _mapping(content["retired_hosts"], "retired_hosts")
    for name, entry_raw in retired.items():
        _string(name, "retired host name")
        if name in names:
            raise EnvironmentRegistryError("current and retired host names overlap")
        entry = _mapping(entry_raw, f"retired host {name}")
        if set(entry) != {"replacement_role", "behavior", "reason"}:
            raise EnvironmentRegistryError(f"retired host {name} fields are not exact")
        if entry["replacement_role"] not in hosts or entry["behavior"] != "fail":
            raise EnvironmentRegistryError("retired hosts must fail and name a current role")
        _string(entry["reason"], f"retired host {name} reason")

    repositories = _mapping(content["repositories"], "repositories")
    for repo_id, entry_raw in repositories.items():
        entry = _mapping(entry_raw, f"repository {repo_id}")
        if set(entry) != {"host_role", "path", "branch", "dirty_policy", "sources"}:
            raise EnvironmentRegistryError(f"repository {repo_id} fields are not exact")
        if entry["host_role"] not in hosts:
            raise EnvironmentRegistryError(f"repository {repo_id} has unknown host role")
        _absolute(entry["path"], f"repository {repo_id} path")
        _string(entry["branch"], f"repository {repo_id} branch")
        if entry["dirty_policy"] != "fail-unless-attributed":
            raise EnvironmentRegistryError("repository dirty policy must fail closed")
        _strings(entry["sources"], f"repository {repo_id} sources")

    plans = _mapping(content["plans"], "plans")
    if set(plans) != {"canonical_root", "source"} or plans["source"] not in {
        f"repository:{repo_id}" for repo_id in repositories
    }:
        raise EnvironmentRegistryError("plan registry fields are invalid")
    _absolute(plans["canonical_root"], "plans canonical_root")
    releases = _mapping(content["releases"], "releases")
    if set(releases) != {"production_root", "production_selector", "development_root"}:
        raise EnvironmentRegistryError("release registry fields are not exact")
    for key, value in releases.items():
        _absolute(value, f"releases {key}")

    instructions = _mapping(content["instructions"], "instructions")
    if set(instructions) != {"precedence", "history_grants_authority", "memory_grants_authority"}:
        raise EnvironmentRegistryError("instruction fields are not exact")
    expected_precedence = [
        "platform-and-user-authority", "repository-agents", "environment-snapshot",
        "exact-task-contract", "persona-style-overlay", "historical-reference-only",
    ]
    if instructions["precedence"] != expected_precedence:
        raise EnvironmentRegistryError("instruction precedence is invalid")
    if instructions["history_grants_authority"] is not False or instructions["memory_grants_authority"] is not False:
        raise EnvironmentRegistryError("history and memory must not grant authority")

    agents = _mapping(content["agents"], "agents")
    for actor, entry_raw in agents.items():
        entry = _mapping(entry_raw, f"agent {actor}")
        allowed = {"authority_files", "excluded_authority_files", "status"}
        if set(entry) - allowed or not {"authority_files", "excluded_authority_files"} <= set(entry):
            raise EnvironmentRegistryError(f"agent {actor} fields are invalid")
        authority = _strings(entry["authority_files"], f"agent {actor} authority_files")
        excluded = _strings(entry["excluded_authority_files"], f"agent {actor} excluded", nonempty=False)
        if set(authority) & set(excluded):
            raise EnvironmentRegistryError(f"agent {actor} authority files conflict")
        if actor != "claude-code" and "CLAUDE.md" not in excluded:
            raise EnvironmentRegistryError("non-Claude agents must exclude CLAUDE.md")
        if "status" in entry:
            _string(entry["status"], f"agent {actor} status")
    _strings(content["unknowns"], "unknowns")
    return registry


def load_registry(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EnvironmentRegistryError(f"cannot load environment registry: {exc}") from exc
    return validate_registry(_mapping(raw, "registry"))


def resolve_host(registry: Mapping[str, Any], identity: str) -> dict[str, Any]:
    validated = validate_registry(registry)
    content = validated["content"]
    retired = content["retired_hosts"]
    if identity in retired:
        raise RetiredHostError(f"retired host name is not current authority: {identity}")
    for role, entry in content["hosts"].items():
        if identity in {role, entry["canonical_name"]}:
            return {"host_role": role, **entry}
    raise EnvironmentRegistryError(f"unknown host identity: {identity}")


def resolved_agent_context(registry: Mapping[str, Any], actor: str) -> dict[str, Any]:
    validated = validate_registry(registry)
    content = validated["content"]
    if actor not in content["agents"]:
        raise EnvironmentRegistryError(f"unknown agent identity: {actor}")
    return {
        "schema": "tgw-environment-snapshot/v1",
        "registry_revision": validated["revision"],
        "actor": actor,
        "hosts": content["hosts"],
        "repositories": content["repositories"],
        "plans": content["plans"],
        "releases": content["releases"],
        "instructions": content["instructions"],
        "actor_instructions": content["agents"][actor],
        "unknowns": content["unknowns"],
    }

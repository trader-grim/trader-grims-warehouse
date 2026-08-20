"""W15 pinned execution-environment preflight.

The preflight has no launcher, broker, service, account-management, or source
write capability.  It turns one catalog-declared actor/profile observation into
a deterministic receipt that later boundaries can bind and revalidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

SCHEMAS = {
    "tgw-execution-environment-catalog/v1",
    "tgw-execution-environment-catalog/v2",
    "tgw-execution-environment-catalog/v3",
}
RECEIPT_SCHEMA = "tgw-environment-preflight-receipt/v1"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_NIX_STORE_HASH = re.compile(r"[0-9abcdfghijklmnpqrsvwxyz]{32}\Z")
_NEUTRAL_ROLES = {
    "implementation",
    "controller-verification",
    "independent-review",
    "release-operation",
}


class EnvironmentPreflightError(ValueError):
    """The declared environment cannot safely be used."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise EnvironmentPreflightError(f"{label} is invalid")
    return value


def _attempt_path(value: Any, label: str, attempt_id: str, *, root: str) -> str:
    if not isinstance(value, str) or value.count("{attempt_id}") != 1:
        raise EnvironmentPreflightError(f"{label} must contain one attempt id placeholder")
    rendered = Path(value.replace("{attempt_id}", attempt_id))
    expected = Path(root) / attempt_id
    if not rendered.is_absolute():
        raise EnvironmentPreflightError(f"{label} must be absolute")
    try:
        rendered.relative_to(expected)
    except ValueError:
        raise EnvironmentPreflightError(f"{label} escapes the durable attempt root") from None
    return str(rendered)


def _observe_store_file(declaration: Mapping[str, Any], *, path_key: str, hash_key: str, label: str) -> tuple[str, str]:
    path_value = declaration.get(path_key)
    store_path = declaration.get("store_path")
    store_hash = declaration.get("store_path_hash")
    expected_hash = declaration.get(hash_key)
    if not isinstance(path_value, str) or not path_value.startswith("/"):
        raise EnvironmentPreflightError(f"catalog {label} path is invalid")
    if not isinstance(store_path, str) or not store_path.startswith("/"):
        raise EnvironmentPreflightError(f"catalog {label} store path is invalid")
    if not isinstance(store_hash, str) or _NIX_STORE_HASH.fullmatch(store_hash) is None:
        raise EnvironmentPreflightError(f"catalog {label} store hash is invalid")
    if Path(store_path).name.split("-", 1)[0] != store_hash:
        raise EnvironmentPreflightError(f"catalog {label} store hash mismatch")
    if not isinstance(expected_hash, str) or _HASH.fullmatch(expected_hash) is None:
        raise EnvironmentPreflightError(f"catalog {label} content hash is invalid")
    path = Path(path_value)
    try:
        path.relative_to(Path(store_path))
    except ValueError:
        raise EnvironmentPreflightError(f"declared {label} unavailable: {path_value}") from None
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(Path(store_path).resolve())
    except ValueError:
        # Composed Nix packages may expose immutable symlinks to another store
        # path in their closure.  The lexical path and exact bytes remain bound
        # by the catalog; a link to mutable host state is never accepted.
        if not str(resolved).startswith("/nix/store/"):
            raise EnvironmentPreflightError(f"declared {label} unavailable: {path_value}") from None
    if not resolved.is_file():
        raise EnvironmentPreflightError(f"declared {label} unavailable: {path_value}")
    observed_hash = _file_hash(resolved)
    if observed_hash != expected_hash:
        raise EnvironmentPreflightError(f"declared {label} hash mismatch: {path_value}")
    return path_value, observed_hash


def _observe_enforcement_boundary(catalog: Mapping[str, Any], boundary_root: str | Path | None) -> dict[str, Any]:
    declaration = catalog.get("enforcement_boundary")
    if not isinstance(declaration, Mapping) or set(declaration) != {
        "schema", "version", "remote_inputs", "executable_renderer_inputs", "components", "assets",
    }:
        raise EnvironmentPreflightError("dynamic-surface enforcement boundary is invalid")
    if (
        declaration.get("schema") != "tgw-dynamic-surface-enforcement-boundary/v1"
        or declaration.get("remote_inputs") is not False
        or declaration.get("executable_renderer_inputs") is not False
    ):
        raise EnvironmentPreflightError("dynamic-surface enforcement boundary is unsafe")
    version = declaration.get("version")
    components, assets = declaration.get("components"), declaration.get("assets")
    if not isinstance(version, str) or not version or not isinstance(components, list) or not components or not isinstance(assets, list):
        raise EnvironmentPreflightError("dynamic-surface enforcement boundary is incomplete")
    if boundary_root is None:
        raise EnvironmentPreflightError("dynamic-surface enforcement root is required")
    root = Path(boundary_root)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise EnvironmentPreflightError("dynamic-surface enforcement root is unavailable")
    observed: list[dict[str, str]] = []
    names: set[str] = set()
    for entry in [*components, *assets]:
        if not isinstance(entry, Mapping) or set(entry) != {"name", "relative_path", "content_sha256", "purpose"}:
            raise EnvironmentPreflightError("dynamic-surface enforcement component is invalid")
        name, relative, expected, purpose = (
            entry.get("name"), entry.get("relative_path"), entry.get("content_sha256"), entry.get("purpose"),
        )
        if (
            not isinstance(name, str) or not name or name in names
            or not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts
            or not isinstance(expected, str) or _HASH.fullmatch(expected) is None
            or not isinstance(purpose, str) or not purpose
        ):
            raise EnvironmentPreflightError("dynamic-surface enforcement component is invalid")
        names.add(name)
        path = root / relative
        if path.is_symlink() or not path.is_file() or _file_hash(path) != expected:
            raise EnvironmentPreflightError(f"dynamic-surface enforcement component mismatch: {name}")
        observed.append({"name": name, "relative_path": relative, "observed_sha256": expected})
    return {
        "schema": declaration["schema"], "version": version,
        "root": str(root), "remote_inputs": False, "executable_renderer_inputs": False,
        "components": sorted(observed, key=lambda item: item["name"]),
    }


def preflight(
    *, catalog: Mapping[str, Any], actor: str, profile: str, attempt_id: str,
    boundary_root: str | Path | None = None,
) -> dict[str, Any]:
    """Verify one catalog-defined environment without executing role work."""
    if not isinstance(catalog, Mapping) or catalog.get("schema") not in SCHEMAS:
        raise EnvironmentPreflightError("environment catalog schema is invalid")
    actors, profiles = catalog.get("actors"), catalog.get("profiles")
    if not isinstance(actors, Mapping) or not isinstance(profiles, Mapping):
        raise EnvironmentPreflightError("environment catalog registry is invalid")
    actor, profile, attempt_id = (_identifier(actor, "actor"), _identifier(profile, "profile"), _identifier(attempt_id, "attempt id"))
    declared_actor, declared_profile = actors.get(actor), profiles.get(profile)
    if not isinstance(declared_actor, Mapping) or not isinstance(declared_profile, Mapping):
        raise EnvironmentPreflightError("catalog actor or profile is unknown")
    if declared_actor.get("enabled") is not True or profile not in declared_actor.get("permitted_profiles", []):
        raise EnvironmentPreflightError("actor/profile binding is refused")
    if declared_profile.get("state") != "ready-for-preflight":
        raise EnvironmentPreflightError("profile is not ready for preflight")
    is_extended = catalog.get("schema") in {
        "tgw-execution-environment-catalog/v2", "tgw-execution-environment-catalog/v3",
    }
    if is_extended:
        qualified_roles = declared_actor.get("qualified_roles")
        if (
            declared_actor.get("role") != "execution-provider"
            or not isinstance(qualified_roles, list)
            or not qualified_roles
            or len(qualified_roles) != len(set(qualified_roles))
            or not all(role in _NEUTRAL_ROLES for role in qualified_roles)
        ):
            raise EnvironmentPreflightError("catalog actor role qualification is invalid")
        workspace_root = _attempt_path(
            declared_profile.get("workspace_root_template"),
            "profile workspace root",
            attempt_id,
            root="/opt/TGW/w/attempts",
        )
        if "cache_roots" in declared_profile:
            raw_cache_roots = declared_profile.get("cache_roots")
            if not isinstance(raw_cache_roots, Mapping) or not raw_cache_roots:
                raise EnvironmentPreflightError("profile cache roots are invalid")
            cache_roots = {
                str(name): _attempt_path(value, f"profile cache root {name}", attempt_id, root="/var/cache/tgw/attempts")
                for name, value in raw_cache_roots.items()
                if isinstance(name, str) and name
            }
            if len(cache_roots) != len(raw_cache_roots):
                raise EnvironmentPreflightError("profile cache roots are invalid")
        else:
            cache_roots = {"default": _attempt_path(
                declared_profile.get("cache_root_template"),
                "profile cache root",
                attempt_id,
                root="/var/cache/tgw/attempts",
            )}
    else:
        workspace_root, cache_roots = None, {}
    tools = declared_profile.get("tools")
    if not isinstance(tools, list) or not tools:
        raise EnvironmentPreflightError("profile tools are invalid")
    observed: list[dict[str, str]] = []
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, Mapping) or set(tool) != {"name", "store_path", "store_path_hash", "executable_path", "executable_sha256"}:
            raise EnvironmentPreflightError("catalog tool declaration is invalid")
        name, executable = tool["name"], tool["executable_path"]
        if not isinstance(name, str) or name in names or not isinstance(executable, str) or not executable.startswith("/"):
            raise EnvironmentPreflightError("catalog tool identity is invalid")
        names.add(name)
        _, observed_hash = _observe_store_file(
            tool, path_key="executable_path", hash_key="executable_sha256", label="executable",
        )
        resolved = Path(executable).resolve(strict=False)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise EnvironmentPreflightError(f"declared executable unavailable: {executable}")
        observed.append({"name": name, "executable_path": executable, "observed_sha256": observed_hash})
    observed_artifacts: list[dict[str, str]] = []
    verification_commands: list[list[str]] = []
    environment: dict[str, str] = {}
    if is_extended and profile == "mobile":
        required_tools = {"flutter", "dart", "java", "gradle", "android-sdkmanager", "android-adb"}
        if names != required_tools:
            raise EnvironmentPreflightError("mobile profile tool set is incomplete")
        artifacts = declared_profile.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise EnvironmentPreflightError("mobile profile artifacts are invalid")
        artifact_names: set[str] = set()
        for artifact in artifacts:
            if not isinstance(artifact, Mapping) or set(artifact) != {
                "name", "version", "store_path", "store_path_hash", "content_path", "content_sha256",
            }:
                raise EnvironmentPreflightError("catalog artifact declaration is invalid")
            name, version = artifact["name"], artifact["version"]
            if not isinstance(name, str) or not name or name in artifact_names or not isinstance(version, str) or not version:
                raise EnvironmentPreflightError("catalog artifact identity is invalid")
            artifact_names.add(name)
            content_path, observed_hash = _observe_store_file(
                artifact, path_key="content_path", hash_key="content_sha256", label="artifact",
            )
            observed_artifacts.append({
                "name": name, "version": version, "content_path": content_path, "observed_sha256": observed_hash,
            })
        required_artifacts = {
            "flutter-sdk", "dart-sdk", "android-sdk-platform",
            "android-build-tools", "android-ndk", "android-license",
        }
        if catalog.get("schema") == "tgw-execution-environment-catalog/v3":
            required_artifacts.add("android-sdk-platform-35")
        if artifact_names != required_artifacts:
            raise EnvironmentPreflightError("mobile profile artifact set is incomplete")
        raw_environment = declared_profile.get("environment")
        required_environment = {
            "HOME", "PUB_CACHE", "GRADLE_USER_HOME", "ANDROID_USER_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME",
        }
        if not isinstance(raw_environment, Mapping) or set(raw_environment) != required_environment:
            raise EnvironmentPreflightError("mobile profile environment is invalid")
        for name, value in raw_environment.items():
            if not isinstance(value, str) or not value.startswith("/"):
                raise EnvironmentPreflightError("mobile profile environment is invalid")
            environment[name] = value.replace("{attempt_id}", attempt_id)
        expected_cache_environment = {
            "HOME": cache_roots.get("home"),
            "PUB_CACHE": cache_roots.get("pub"),
            "GRADLE_USER_HOME": cache_roots.get("gradle"),
            "ANDROID_USER_HOME": cache_roots.get("android_user"),
        }
        if any(environment[name] != value for name, value in expected_cache_environment.items()):
            raise EnvironmentPreflightError("mobile profile cache environment is inconsistent")
        if environment["ANDROID_HOME"] != environment["ANDROID_SDK_ROOT"]:
            raise EnvironmentPreflightError("mobile profile Android SDK roots disagree")
        raw_commands = declared_profile.get("verification_commands")
        if (
            not isinstance(raw_commands, list)
            or not raw_commands
            or any(not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command) for command in raw_commands)
        ):
            raise EnvironmentPreflightError("mobile verification commands are invalid")
        verification_commands = [list(command) for command in raw_commands]
        if any(command[0] not in names for command in verification_commands):
            raise EnvironmentPreflightError("mobile verification command uses an undeclared tool")
    unsigned = {
        "schema": RECEIPT_SCHEMA,
        "result": "PASS",
        "catalog_sha256": _hash(catalog),
        "actor": actor,
        "profile": profile,
        "attempt_id": attempt_id,
        "tools": sorted(observed, key=lambda item: item["name"]),
    }
    if is_extended:
        unsigned.update({
            "workspace_root": workspace_root,
            "cache_roots": cache_roots,
            "environment": environment,
            "artifacts": sorted(observed_artifacts, key=lambda item: item["name"]),
            "verification_commands": verification_commands,
        })
    if catalog.get("schema") == "tgw-execution-environment-catalog/v3":
        unsigned["enforcement_boundary"] = _observe_enforcement_boundary(catalog, boundary_root)
    return unsigned


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-environment-preflight")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--boundary-root", type=Path)
    args = parser.parse_args()
    try:
        raw = json.loads(args.catalog.read_text(encoding="utf-8"))
        print(json.dumps(preflight(
            catalog=raw, actor=args.actor, profile=args.profile,
            attempt_id=args.attempt_id, boundary_root=args.boundary_root,
        ), sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, EnvironmentPreflightError) as exc:
        print(json.dumps({"result": "HOLD", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Build one signed, complete W18 actor generation outside the source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.actor_contract import actor_contract_public_key, compile_actor_contract, sign_actor_contract

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ActorGenerationError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ActorGenerationError(f"actor generation source is not a regular file: {path}")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(path: Path) -> str:
    if not path.is_dir() or path.is_symlink():
        raise ActorGenerationError(f"actor generation source is not a directory: {path}")
    digest = hashlib.sha256()
    files = [item for item in path.rglob("*") if item.is_file() and not item.is_symlink() and "__pycache__" not in item.parts]
    for item in sorted(files, key=lambda value: value.relative_to(path).as_posix()):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorGenerationError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ActorGenerationError(f"{label} is invalid")
    return value


def _signing_key(path: Path) -> Ed25519PrivateKey:
    if not path.is_file() or path.is_symlink():
        raise ActorGenerationError("actor contract signing key is unavailable")
    if path.stat().st_mode & 0o077:
        raise ActorGenerationError("actor contract signing key permissions are too broad")
    raw = path.read_bytes()
    if len(raw) != 32:
        raise ActorGenerationError("actor contract signing key must contain 32 raw bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def _binding_hash(bindings: list[dict[str, str]]) -> str:
    return _hash(sorted(bindings, key=lambda item: item["endpoint"]))


def build_actor_generation(
    *, catalog_path: str | Path, descriptor_path: str | Path, source_root: str | Path,
    output_root: str | Path, signing_key_path: str | Path, plan_commit: str,
    solution_hash: str, source_commit: str, source_tree: str, freshness_hash: str,
) -> dict[str, Any]:
    """Compile, sign and atomically retain one content-bound actor generation."""
    if _COMMIT.fullmatch(plan_commit) is None or _COMMIT.fullmatch(source_commit) is None or _COMMIT.fullmatch(source_tree) is None:
        raise ActorGenerationError("actor generation Git binding is invalid")
    if _HASH.fullmatch(solution_hash) is None or _HASH.fullmatch(freshness_hash) is None:
        raise ActorGenerationError("actor generation content binding is invalid")
    catalog_source, descriptor_source = Path(catalog_path), Path(descriptor_path)
    catalog, descriptor = _read_json(catalog_source, "environment catalog"), _read_json(descriptor_source, "actor generation descriptor")
    if descriptor.get("schema") != "tgw-actor-generation-descriptor/v1" or set(descriptor) != {"schema", "actors"} or not isinstance(descriptor["actors"], Mapping):
        raise ActorGenerationError("actor generation descriptor is invalid")
    actors = catalog.get("actors") if isinstance(catalog.get("actors"), Mapping) else {}
    enabled = sorted(actor for actor, value in actors.items() if isinstance(value, Mapping) and value.get("enabled") is True)
    if sorted(descriptor["actors"]) != enabled or not enabled:
        raise ActorGenerationError("actor generation descriptor does not cover every enabled catalog actor")
    source = Path(source_root)
    output = Path(output_root)
    if not source.is_absolute() or not source.is_dir() or source.is_symlink():
        raise ActorGenerationError("actor generation source root is invalid")
    if not output.is_absolute() or output == Path("/tmp") or Path("/tmp") in output.parents or not output.is_dir() or output.is_symlink():
        raise ActorGenerationError("actor generation output root is not durable")
    key = _signing_key(Path(signing_key_path))
    catalog_bytes = _canonical(catalog)
    generation_body = {
        "schema": "tgw-actor-generation-identity/v1", "catalog_hash": _hash(catalog),
        "descriptor_hash": _hash(descriptor), "plan_commit": plan_commit,
        "solution_hash": solution_hash, "source_commit": source_commit,
        "source_tree": source_tree, "freshness_hash": freshness_hash,
    }
    generation = _hash(generation_body)
    final = output / generation.removeprefix("sha256:")
    if final.exists() or final.is_symlink():
        receipt = _read_json(final / "generation-receipt.json", "existing actor generation")
        if receipt.get("generation") != generation or receipt.get("generation_identity") != generation_body:
            raise ActorGenerationError("actor generation identity collision")
        return receipt
    stage = output / ("." + generation.removeprefix("sha256:") + ".next")
    if stage.exists() or stage.is_symlink():
        raise ActorGenerationError("stale actor generation staging directory exists")
    stage.mkdir(mode=0o750)
    try:
        contracts_root = stage / "contracts"
        contracts_root.mkdir(mode=0o750)
        (stage / "environment-catalog.json").write_bytes(catalog_bytes)
        bundle_actors: dict[str, Any] = {}
        contract_hashes: dict[str, str] = {}
        for actor in enabled:
            specification = descriptor["actors"][actor]
            if not isinstance(specification, Mapping) or set(specification) != {"profile", "home", "project", "bindings"}:
                raise ActorGenerationError(f"actor generation entry is invalid: {actor}")
            home, project = Path(str(specification["home"])), Path(str(specification["project"]))
            if not home.is_absolute() or not project.is_absolute() or not isinstance(specification["bindings"], list):
                raise ActorGenerationError(f"actor generation roots or bindings are invalid: {actor}")
            compiled_bindings: list[dict[str, str]] = []
            observed: dict[str, dict[str, str]] = {"skill": {}, "hook": {}, "mcp": {}, "launcher": {}, "bootstrap": {}}
            for raw in specification["bindings"]:
                if not isinstance(raw, Mapping) or set(raw) != {"kind", "name", "source", "destination"}:
                    raise ActorGenerationError(f"actor generation binding is invalid: {actor}")
                kind, name = raw["kind"], raw["name"]
                relative = Path(str(raw["source"]))
                destination = Path(str(raw["destination"]))
                if kind not in observed or not isinstance(name, str) or not name or relative.is_absolute() or ".." in relative.parts or not destination.is_absolute():
                    raise ActorGenerationError(f"actor generation binding is unsafe: {actor}")
                resolved = (source / relative).resolve(strict=True)
                if source.resolve() != resolved and source.resolve() not in resolved.parents:
                    raise ActorGenerationError(f"actor generation binding escapes source: {actor}")
                digest = _tree_hash(resolved) if resolved.is_dir() else _file_hash(resolved)
                if name in observed[kind]:
                    raise ActorGenerationError(f"actor generation binding is duplicated: {actor}:{name}")
                observed[kind][name] = digest
                compiled_bindings.append({
                    "kind": kind, "name": name, "source": relative.as_posix(),
                    "destination": str(destination), "sha256": digest,
                })
            environment_destination = home / ".tgw" / "execution-environment-catalog.json"
            catalog_file_hash = "sha256:" + hashlib.sha256(catalog_bytes).hexdigest()
            compiled_bindings.append({
                "kind": "environment", "name": "environment-catalog",
                "source": str(final / "environment-catalog.json"),
                "destination": str(environment_destination), "sha256": catalog_file_hash,
            })
            if set(observed["launcher"]) != {"launcher"} or set(observed["bootstrap"]) != {"bootstrap-receipt"}:
                raise ActorGenerationError(f"actor generation launcher or bootstrap is incomplete: {actor}")
            launcher = next(item for item in compiled_bindings if item["kind"] == "launcher")
            mcp_bindings = [{
                "endpoint": item["name"], "source_sha256": item["sha256"], "destination": item["destination"],
            } for item in compiled_bindings if item["kind"] == "mcp"]
            bootstrap = next(item for item in compiled_bindings if item["kind"] == "bootstrap")
            local = {
                "bootstrap_receipt_hash": bootstrap["sha256"],
                "launcher": {"path": launcher["destination"], "sha256": launcher["sha256"]},
                "skills": observed["skill"], "hooks": observed["hook"],
                "mcp": {"endpoints": sorted(observed["mcp"]), "binding_hash": _binding_hash(mcp_bindings)},
            }
            contract = compile_actor_contract(
                catalog=catalog, actor=actor, profile=str(specification["profile"]),
                plan_commit=plan_commit, plan_solution_hash=solution_hash,
                code_graph={"commit": source_commit, "tree": source_tree, "freshness_hash": freshness_hash},
                local=local,
            )
            if contract["status"] != "READY":
                raise ActorGenerationError(f"actor generation contract is quarantined: {actor}:{contract['diagnostics']}")
            signed = sign_actor_contract(contract, signing_private_key=key)
            signed_bytes = _canonical(signed) + b"\n"
            (contracts_root / f"{actor}.json").write_bytes(signed_bytes)
            compiled_bindings.append({
                "kind": "contract", "name": "actor-contract",
                "source": str(final / "contracts" / f"{actor}.json"),
                "destination": str(home / ".tgw" / "actor-contract.json"),
                "sha256": "sha256:" + hashlib.sha256(signed_bytes).hexdigest(),
            })
            contract_hashes[actor] = signed["receipt_hash"]
            bundle_actors[actor] = {
                "home": str(home), "project": str(project), "bindings": compiled_bindings,
            }
        bundle = {"schema": "tgw-complete-actor-contract-bundle/v1", "generation": generation, "actors": bundle_actors}
        (stage / "bundle.json").write_bytes(_canonical(bundle) + b"\n")
        unsigned_receipt = {
            "schema": "tgw-actor-generation-receipt/v1", "status": "PREPARED",
            "generation": generation, "generation_identity": generation_body,
            "actors": enabled, "contract_receipt_hashes": contract_hashes,
            "signer_public_key": actor_contract_public_key(key), "bundle_hash": _hash(bundle),
        }
        receipt = {**unsigned_receipt, "receipt_hash": _hash(unsigned_receipt)}
        (stage / "generation-receipt.json").write_bytes(_canonical(receipt) + b"\n")
        for path in stage.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o440)
        os.replace(stage, final)
        directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt
    except Exception:
        if stage.exists() and not stage.is_symlink():
            for path in sorted(stage.rglob("*"), reverse=True):
                path.rmdir() if path.is_dir() else path.unlink()
            stage.rmdir()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-build-actor-generation")
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--signing-key", required=True, type=Path)
    parser.add_argument("--plan-commit", required=True)
    parser.add_argument("--solution-hash", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--freshness-hash", required=True)
    args = parser.parse_args()
    try:
        receipt = build_actor_generation(
            catalog_path=args.catalog, descriptor_path=args.descriptor,
            source_root=args.source_root, output_root=args.output_root,
            signing_key_path=args.signing_key, plan_commit=args.plan_commit,
            solution_hash=args.solution_hash, source_commit=args.source_commit,
            source_tree=args.source_tree, freshness_hash=args.freshness_hash,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": "tgw-actor-generation-build-result/v1", "status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

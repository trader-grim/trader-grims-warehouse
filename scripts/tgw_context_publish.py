#!/bin/sh
""":"
exec /opt/TGW/.venvs/controller/bin/python3 -I -S "$0" "$@"
":"""

# ruff: noqa: E402 -- bytecode policy intentionally precedes remaining imports.

import sys

# This entrypoint executes from immutable release trees.  Isolated mode ignores
# PYTHON* environment variables, so bytecode suppression must be interpreter
# state established before any release module can be imported.
sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _selected_runtime() -> Path:
    """Resolve only the immutable runtime shipped with this entrypoint generation."""
    runtime = Path(__file__).resolve().parent / "runtime"
    if not runtime.exists() and Path(__file__).resolve().parent.name == "scripts":
        # Source-tree execution is retained for offline verification only.  An
        # installed generation always has the sibling runtime directory.
        runtime = Path(__file__).resolve().parent.parent / "src"
    resolved = runtime.resolve(strict=True)
    if runtime.is_symlink() or not resolved.is_dir():
        raise RuntimeError("Context publisher runtime binding is not a direct directory")
    return resolved


def _snapshot_api() -> tuple[int, Any]:
    runtime = _selected_runtime()
    module_path = runtime / "tgw/current_context_snapshot.py"
    spec = importlib.util.spec_from_file_location("_tgw_selected_context_snapshot", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("selected Context snapshot parser is unavailable")
    module = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    old_pythonpath = os.environ.pop("PYTHONPATH", None)
    try:
        # The -I -S bootstrap has already excluded the script directory, cwd,
        # PYTHONPATH, and site packages.  Preserve its interpreter-owned ZIP,
        # stdlib, and dynload search roots before the exact generation runtime;
        # the selected parser may use the stdlib but cannot be shadowed by the
        # ambient process or by its own generation directory.
        sys.path[:] = [*old_path, str(runtime)]
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
        if old_pythonpath is not None:
            os.environ["PYTHONPATH"] = old_pythonpath
    return module.MAX_TASK_BYTES, module.publish_bytes


def _object(path: Path, maximum: int) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError(f"input exceeds {maximum} bytes: {path}")
    value = json.loads(raw.decode("utf-8", errors="strict"))
    if not isinstance(value, dict):
        raise ValueError(f"input root is not an object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--cursor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("current-context publication must run as root")
    maximum, publish_bytes = _snapshot_api()
    task = _object(args.task, maximum)
    cursor = _object(args.cursor, maximum)
    raw = publish_bytes(task, cursor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=args.output.name + ".publish.", dir=args.output.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
        directory = os.open(args.output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

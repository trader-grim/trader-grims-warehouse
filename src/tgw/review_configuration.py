"""Deterministic local configuration for the isolated Codex review provider."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


class ReviewConfigurationError(ValueError):
    pass


def configured_review_command(
    *,
    python: str | Path = sys.executable,
    probe: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Return a runner command only after the local backend health probe passes."""

    python_path = str(Path(python).absolute())
    health = probe(
        [python_path, "-m", "tgw.codex_review_backend", "--health"],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        evidence = json.loads(health.stdout)
    except json.JSONDecodeError as exc:
        raise ReviewConfigurationError("Codex review health probe returned invalid JSON") from exc
    if health.returncode or evidence.get("available") is not True:
        return {
            "schema": "tgw-review-runner-configuration/v1",
            "status": "HOLD",
            "command": None,
            "health": evidence,
        }
    backend = [python_path, "-m", "tgw.codex_review_backend"]
    wrapper = [
        python_path,
        "-m",
        "tgw.review_runner",
        "--provider-command-json",
        json.dumps(backend, separators=(",", ":")),
    ]
    return {
        "schema": "tgw-review-runner-configuration/v1",
        "status": "AVAILABLE",
        "command": wrapper,
        "health": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-review-configuration")
    parser.parse_args()
    try:
        result = configured_review_command()
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["status"] == "AVAILABLE" else 2
    except (ReviewConfigurationError, OSError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

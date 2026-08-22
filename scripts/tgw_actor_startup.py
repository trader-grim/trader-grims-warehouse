#!/opt/TGW/.venvs/controller/bin/python3
"""Run the actor startup check from its exact materialized release."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tgw.actor_startup import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

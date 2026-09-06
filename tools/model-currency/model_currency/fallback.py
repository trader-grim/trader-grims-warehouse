"""Load the checked-in offline fallback model list."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ModelInfo

FALLBACK_PATH = Path(__file__).with_name("fallback_models.json")

SOURCE = "fallback"


def load(path: str | Path | None = None) -> list[ModelInfo]:
    raw = json.loads(Path(path or FALLBACK_PATH).read_text("utf-8"))
    out: list[ModelInfo] = []
    for m in raw.get("models", []):
        out.append(
            ModelInfo(
                provider=m["provider"],
                model_id=m["model_id"],
                context=int(m.get("context") or 0),
                tool_use=bool(m.get("tool_use")),
                free=bool(m.get("free")),
                deprecated=bool(m.get("deprecated")),
                source=SOURCE,
                name=m.get("name") or m["model_id"],
                modalities_in=tuple(m.get("modalities_in") or ("text",)),
                reasoning=bool(m.get("reasoning")),
                notes=tuple(m.get("notes") or ()),
            )
        )
    return out


def generated_date(path: str | Path | None = None) -> str:
    try:
        raw = json.loads(Path(path or FALLBACK_PATH).read_text("utf-8"))
        return str(raw.get("generated") or "unknown")
    except (OSError, json.JSONDecodeError, ValueError):
        return "unknown"

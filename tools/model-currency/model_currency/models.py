"""The one data type this tool produces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True)
class ModelInfo:
    """A single model as seen by one (or more, after merge) upstream sources.

    ``free`` means "usable at no per-token cost" — an OpenRouter ``:free``
    variant, a zero-price entry, or a provider whose models are served on a
    rate-limited free tier (Groq). It does not promise unlimited use.

    ``deprecated`` means the source flagged the model inactive or past its
    published expiry date. Coding-appropriateness filtering drops these.
    """

    provider: str
    model_id: str
    context: int
    tool_use: bool
    free: bool
    deprecated: bool
    source: str
    name: str = ""
    modalities_in: tuple[str, ...] = ()
    reasoning: bool = False
    notes: tuple[str, ...] = ()
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["modalities_in"] = list(self.modalities_in)
        d["notes"] = list(self.notes)
        return d

    def merge(self, other: "ModelInfo") -> "ModelInfo":
        """Combine two records for the same (provider, model_id) from
        different sources, keeping the most capable / most informative view."""
        sources = sorted({*self.source.split("+"), *other.source.split("+")})
        notes = tuple(dict.fromkeys((*self.notes, *other.notes)))
        return replace(
            self,
            context=max(self.context, other.context),
            tool_use=self.tool_use or other.tool_use,
            free=self.free or other.free,
            # Only stay "deprecated" if no source still considers it live.
            deprecated=self.deprecated and other.deprecated,
            reasoning=self.reasoning or other.reasoning,
            name=self.name or other.name,
            modalities_in=self.modalities_in or other.modalities_in,
            source="+".join(sources),
            notes=notes,
        )

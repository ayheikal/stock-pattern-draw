from __future__ import annotations

from dataclasses import dataclass, field

# Points and lines are expressed as (bar_index, price). Indices are converted
# to chart timestamps at the API layer, which keeps detectors data-agnostic.
Point = tuple[int, float]


@dataclass
class Pattern:
    name: str
    category: str  # "reversal" | "continuation" | "trend" | "candle"
    direction: str  # "bullish" | "bearish" | "neutral"
    start: int
    end: int
    confidence: float
    points: list[Point] = field(default_factory=list)
    lines: list[list[Point]] = field(default_factory=list)
    target: float | None = None
    confirmed: bool = False
    label: str = ""


@dataclass
class Level:
    price: float
    kind: str  # "support" | "resistance"
    touches: int
    first: int
    last: int
    strength: float

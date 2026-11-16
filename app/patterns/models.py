from __future__ import annotations

from dataclasses import dataclass, field

# Points and lines are expressed as (bar_index, price). Indices are converted
# to chart timestamps at the API layer, which keeps detectors data-agnostic.
Point = tuple[int, float]


@dataclass
class Pattern:
    name: str
    category: str  # "reversal" | "continuation" | "trend" | "gap"
    direction: str  # "bullish" | "bearish" | "neutral"
    start: int
    end: int
    confidence: float
    points: list[Point] = field(default_factory=list)
    lines: list[list[Point]] = field(default_factory=list)
    # Breakout line (neckline, or a cup's rim); drawn separately so the chart can extend and label it.
    neckline: list[Point] | None = None
    target: float | None = None
    confirmed: bool = False
    label: str = ""
    # Price has since closed back behind where it stood before the breakout: the signal no longer holds.
    failed: bool = False
    # Breakout bar's volume over the 20 bars before it (None when there is no volume data or no breakout).
    volume_ratio: float | None = None


@dataclass
class Level:
    price: float
    kind: str  # "support" | "resistance"
    touches: int
    first: int
    last: int
    strength: float

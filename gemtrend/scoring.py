"""Scoring engine: normalisation, the data-availability traffic light, and the
decision ladder (Modul 08 + Modul 13).

The key idea (anti-hallucination): a component flagged RED is *not* guessed.
It is removed from the weighted sum and the remaining weights are renormalised.
If too much of the original weight was RED, the recommendation is capped at
"Watchlist" regardless of the headline number.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import ScoringConfig
from .models import Component, Source


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def ramp(value: float | None, lo: float, hi: float) -> float | None:
    """Linearly map ``value`` from [lo, hi] onto [0, 100] (clamped).

    Handles ``lo > hi`` (i.e. "smaller is better"). Returns None if value is None
    so the caller can mark the component RED rather than invent a 0.
    """
    if value is None:
        return None
    if lo == hi:
        return 50.0
    t = (value - lo) / (hi - lo)
    return clamp(t * 100.0)


@dataclass
class ScoreResult:
    score: float            # 0-100
    confidence: float       # fraction of total weight backed by GREEN data
    red_fraction: float     # fraction of total weight that was RED (unavailable)


def aggregate(components: list[Component]) -> ScoreResult:
    """Combine components into a 0-100 score with traffic-light renormalisation."""
    total_weight = sum(c.weight for c in components)
    if total_weight <= 0:
        return ScoreResult(0.0, 0.0, 1.0)

    available = [c for c in components if c.available]
    avail_weight = sum(c.weight for c in available)
    red_weight = sum(c.weight for c in components if c.source is Source.RED)
    green_weight = sum(c.weight for c in components if c.source is Source.GREEN)

    if avail_weight <= 0:
        return ScoreResult(0.0, 0.0, 1.0)

    score = sum(clamp(c.score) * c.weight for c in available) / avail_weight
    return ScoreResult(
        score=score,
        confidence=green_weight / total_weight,
        red_fraction=red_weight / total_weight,
    )


def decide(score: float, red_fraction: float, cfg: ScoringConfig
           ) -> tuple[str, float]:
    """Map a score to a (decision label, base position fraction).

    Applies the >40 %-RED guard: if the score leans too heavily on unavailable
    data, it cannot earn more than a Watchlist tag.
    """
    if red_fraction > cfg.red_cap_fraction and score >= cfg.watchlist_min:
        return ("Watchlist (Daten 🔴-limitiert)", 0.0)

    if score < cfg.watchlist_min:
        return ("Nicht handeln", 0.0)
    if score < cfg.small_min:
        return ("Watchlist", 0.0)
    if score < cfg.normal_min:
        return ("Kleine Position", cfg.small_pos)
    if score < cfg.full_min:
        return ("Normale Position", cfg.normal_pos)
    return ("Volle Position", cfg.full_pos)


def apply_market_regime(position_pct: float, risk_multiplier: float) -> float:
    """Dampen the suggested size by the market regime (Modul 00)."""
    return round(position_pct * max(0.0, risk_multiplier), 4)

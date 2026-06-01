"""Lightweight data models shared across the pipeline.

These are intentionally compact dataclasses. The pipeline keeps only these
small records in memory (never the raw API payloads), which is what allows a
large universe to be scanned with a bounded memory footprint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Source(str, Enum):
    """Data-availability traffic light (Modul 13 — anti-hallucination).

    GREEN  real / directly retrievable from Yahoo
    YELLOW delayed or approximated (e.g. possibly stale ownership figures)
    RED    not freely available (social buzz, options flow, real-time insider)
           -> the value is *never invented*; it is dropped and weights renorm.
    """

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass
class Component:
    """One scored sub-signal with its provenance."""

    name: str
    score: float            # 0-100 (ignored when source is RED)
    weight: float           # relative weight within its path
    source: Source
    detail: str = ""        # short human-readable explanation

    @property
    def available(self) -> bool:
        return self.source is not Source.RED


@dataclass
class Prelim:
    """Stage-1/2 compact record. Holds just enough to pre-rank cheaply."""

    symbol: str
    name: str = ""
    price: float | None = None
    market_cap: float | None = None
    avg_volume: float | None = None
    reg_volume: float | None = None
    trailing_pe: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    change_pct: float | None = None
    analyst_rating: str | None = None
    sources: set[str] = field(default_factory=set)   # which screeners flagged it
    pre_score: float = 0.0


@dataclass
class Candidate:
    """Final scored candidate for one path (TREND or GEM)."""

    symbol: str
    name: str
    path: str                       # "TREND" | "GEM"
    score: float                    # 0-100
    decision: str                   # label from the decision ladder
    position_pct: float             # suggested max position (informative)
    confidence: float               # fraction of weight backed by 🟢 data (0-1)
    components: list[Component] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)   # key display values
    stop_hint: float | None = None  # ATR-based stop suggestion (informative)
    notes: str = ""

    # Heaps order candidates by score; ties broken by symbol for determinism.
    def __lt__(self, other: "Candidate") -> bool:
        if self.score != other.score:
            return self.score < other.score
        return self.symbol > other.symbol


@dataclass
class MarketContext:
    """Modul 00 — top-level regime read that dampens position sizing."""

    spx_price: float | None = None
    spx_sma200: float | None = None
    vix: float | None = None
    regime: str = "unbekannt"       # "Risk-On" | "Neutral" | "Risk-Off"
    risk_multiplier: float = 1.0    # scales suggested position sizes
    note: str = ""

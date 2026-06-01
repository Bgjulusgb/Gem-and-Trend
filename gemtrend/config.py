"""Configuration for the Gem & Trend scanner.

All tunables live here as dataclasses with sensible defaults. A run can be
customised either via CLI flags (see ``__main__.py``) or by passing a JSON file
that overrides any subset of fields (``--config path.json``).

Keeping config as plain dataclasses (no YAML dependency) keeps the footprint
small, which matches the project's lean / memory-conscious philosophy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


# --- Universe: which Yahoo predefined screeners feed the candidate pool. ------
# These are deliberately weighted toward small / overlooked names (the "gems")
# plus a few momentum screeners (the "trends"). All are verified valid scrIds.
DEFAULT_SCREENERS: tuple[str, ...] = (
    "aggressive_small_caps",       # small, higher-beta names
    "small_cap_gainers",           # small caps moving now
    "undervalued_growth_stocks",   # cheap-ish growth (gem candidates)
    "undervalued_large_caps",      # value tilt
    "day_gainers",                 # momentum
    "most_actives",                # liquidity / attention
    "most_shorted_stocks",         # squeeze potential
    "growth_technology_stocks",    # growth tilt
)


@dataclass
class UniverseConfig:
    """Controls how the raw candidate universe is assembled (Stage 1)."""

    screeners: list[str] = field(default_factory=lambda: list(DEFAULT_SCREENERS))
    use_trending: bool = True
    per_screener: int = 60          # rows pulled per screener
    trending_count: int = 30
    max_universe: int = 350         # hard cap on symbols held after Stage 1

    # Hard filters (Modul 01). Defaults allow the small-cap "gem" floor while
    # still demanding real liquidity so we never chase untradeable microcaps.
    min_market_cap: float = 50_000_000        # 50M floor (gem floor)
    max_market_cap: float = 20_000_000_000    # 20B ceiling (keep it "overlooked")
    min_price: float = 1.0                    # avoid sub-$1 / penny noise
    max_price: float = 2_000.0
    min_avg_volume: float = 100_000           # tradable liquidity


@dataclass
class FetchConfig:
    """Controls HTTP behaviour and the cost/efficiency knobs (Stages 2-3)."""

    deep_limit: int = 60            # how many survivors get expensive Stage-3 calls
    batch_size: int = 50            # symbols per v7 batch-quote request
    concurrency: int = 6            # parallel Stage-3 workers
    requests_per_second: float = 4  # global throttle (be polite to Yahoo)
    timeout: float = 12.0
    max_retries: int = 4
    chart_range: str = "6mo"
    chart_interval: str = "1d"


@dataclass
class ScoringConfig:
    """Weights, thresholds and the decision ladder (Modul 08)."""

    # Trend path (A) component weights — must sum to 1.0.
    trend_weights: dict[str, float] = field(default_factory=lambda: {
        "volume": 0.30,
        "momentum": 0.25,
        "breakout": 0.20,
        "rsi": 0.15,
        "near_high": 0.10,
    })
    # Gem path (B) component weights — must sum to 1.0.
    gem_weights: dict[str, float] = field(default_factory=lambda: {
        "fundamental": 0.30,
        "valuation": 0.20,
        "base": 0.20,            # technical accumulation / basing
        "ownership": 0.15,       # low analyst coverage + sane institutional %
        "insider": 0.10,
        "early_social": 0.05,    # almost always 🔴 (honest n/a)
    })

    # Decision ladder (score 0-100 -> label, max position fraction).
    watchlist_min: float = 50.0
    small_min: float = 65.0
    normal_min: float = 80.0
    full_min: float = 95.0
    small_pos: float = 0.08
    normal_pos: float = 0.15
    full_pos: float = 0.20

    # Anti-hallucination guard: if >this fraction of weight rests on 🔴 data,
    # cap the recommendation at "Watchlist".
    red_cap_fraction: float = 0.40

    # Gem-specific thresholds (Modul 0.5 filters).
    max_analysts: int = 3                 # "still overlooked"
    max_institutional: float = 0.40       # not yet crowded
    valuation_pe_cap: float = 25.0        # below this scores well
    valuation_ps_cap: float = 4.0


@dataclass
class IndicatorConfig:
    """Lookback windows for technical indicators."""

    rsi_period: int = 14
    atr_period: int = 14
    sma_fast: int = 20
    sma_slow: int = 50
    sma_trend: int = 200
    momentum_period: int = 20
    vol_lookback: int = 20
    high_low_lookback: int = 252
    base_lookback: int = 40               # window for basing/accumulation check
    rel_volume_trend: float = 1.8         # rel-vol >= this => volume anomaly
    rsi_momentum_lo: float = 55.0
    rsi_momentum_hi: float = 72.0         # above => overbought, momentum fading


@dataclass
class CacheConfig:
    enabled: bool = False
    directory: str = ".gemtrend_cache"
    ttl_seconds: int = 900                # 15 min; stale entries auto-deleted


@dataclass
class OutputConfig:
    fmt: str = "table"                    # table | json | csv
    out_file: str | None = None
    top_k: int = 12                       # per table
    min_score: float = 0.0                # filter floor for display


@dataclass
class AppConfig:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    verbose: bool = False

    # -- (de)serialisation ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        cfg = cls()
        _merge_dataclass(cfg.universe, data.get("universe", {}))
        _merge_dataclass(cfg.fetch, data.get("fetch", {}))
        _merge_dataclass(cfg.scoring, data.get("scoring", {}))
        _merge_dataclass(cfg.indicators, data.get("indicators", {}))
        _merge_dataclass(cfg.cache, data.get("cache", {}))
        _merge_dataclass(cfg.output, data.get("output", {}))
        if "verbose" in data:
            cfg.verbose = bool(data["verbose"])
        return cfg

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def _merge_dataclass(obj: Any, overrides: dict[str, Any]) -> None:
    """Shallow-merge a dict of overrides onto a dataclass instance in place."""
    for key, value in overrides.items():
        if hasattr(obj, key):
            setattr(obj, key, value)

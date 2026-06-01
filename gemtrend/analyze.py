"""Per-symbol analysis: turn raw Yahoo payloads into scored :class:`Candidate`s.

Two paths are evaluated (Modul 0.5): TREND (momentum) and GEM (pre-discovery).
Each sub-signal carries an honest data-availability flag (Modul 13): signals we
can actually compute from Yahoo are GREEN/YELLOW; signals that need paid/closed
feeds (social buzz, options flow) are RED and never fabricated.
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from . import indicators as ind
from .config import IndicatorConfig, ScoringConfig
from .models import Candidate, Component, Prelim, Source
from .scoring import aggregate, apply_market_regime, clamp, decide, ramp

# Yahoo quoteSummary modules we need for the gem path.
SUMMARY_MODULES = (
    "financialData",
    "defaultKeyStatistics",
    "summaryDetail",
    "netSharePurchaseActivity",
)


# --- raw-value extraction helpers --------------------------------------------
def _raw(v: Any) -> Any:
    """Yahoo wraps numbers as {"raw": x, "fmt": "..."}; unwrap to the number."""
    if isinstance(v, dict):
        return v.get("raw")
    return v


def dig(d: dict | None, *path: str) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return _raw(cur)


def _avg(parts: list[float | None]) -> float | None:
    vals = [p for p in parts if p is not None]
    return mean(vals) if vals else None


# --- cheap pre-ranking (Stage 2, no chart/summary needed) --------------------
# Two separate lenses so the expensive Stage-3 budget is split between movers
# (trend) and quiet, overlooked small caps (gem) — otherwise momentum names
# crowd the gems out before they are ever analysed.
def pre_score_trend(p: Prelim) -> float:
    """Lean toward names that are moving NOW (upside momentum, volume, near high)."""
    parts: list[float] = []
    if p.change_pct is not None:
        parts.append(clamp(ramp(p.change_pct, -2.0, 15.0) or 0))
    if p.reg_volume and p.avg_volume:
        parts.append(clamp(ramp(p.reg_volume / p.avg_volume, 0.8, 3.5) or 0))
    if p.week52_high and p.price:
        parts.append(clamp(p.price / p.week52_high * 100.0))
    return mean(parts) if parts else 0.0


def pre_score_gem(p: Prelim) -> float:
    """Lean toward small, quiet, cheap names with room below their highs."""
    parts: list[float] = []
    if p.market_cap:
        parts.append(clamp(ramp(p.market_cap, 4e9, 5e7) or 0))        # smaller better
    if p.change_pct is not None:
        parts.append(clamp(100.0 - (ramp(abs(p.change_pct), 0.0, 12.0) or 0)))  # quiet
    if p.trailing_pe and p.trailing_pe > 0:
        parts.append(clamp(ramp(p.trailing_pe, 40.0, 6.0) or 0))      # cheaper better
    if p.week52_high and p.price:
        pos = p.price / p.week52_high                                 # room below high
        parts.append(clamp(100.0 - abs(pos - 0.70) / 0.70 * 100.0))   # peak ~70% of high
    if p.avg_volume:
        parts.append(clamp((ramp(p.avg_volume, 5e4, 5e5) or 0) * 0.5 + 30))  # liquidity
    return mean(parts) if parts else 0.0


# =============================================================================
# TREND PATH (A)
# =============================================================================
def analyze_trending(p: Prelim, chart: dict[str, list] | None,
                     icfg: IndicatorConfig, scfg: ScoringConfig,
                     risk_multiplier: float) -> Candidate:
    closes = chart.get("close", []) if chart else []
    highs = chart.get("high", []) if chart else []
    lows = chart.get("low", []) if chart else []
    vols = chart.get("volume", []) if chart else []
    price = p.price or (ind.clean(closes)[-1] if ind.clean(closes) else None)

    rel_vol = ind.rel_volume(vols, icfg.vol_lookback)
    mom = ind.momentum_pct(closes, icfg.momentum_period)
    sma_f = ind.sma(closes, icfg.sma_fast)
    sma_s = ind.sma(closes, icfg.sma_slow)
    pct_h20 = ind.pct_from_high(closes, 20)
    rsi = ind.rsi(closes, icfg.rsi_period)
    pct_h252 = ind.pct_from_high(closes, icfg.high_low_lookback)
    atr = ind.atr(highs, lows, closes, icfg.atr_period)

    w = scfg.trend_weights
    comps = [
        _comp("volume", _trend_volume(rel_vol), w["volume"],
              rel_vol, f"RelVol {rel_vol:.1f}x" if rel_vol else "n/a"),
        _comp("momentum", _trend_momentum(mom), w["momentum"],
              mom, f"{mom:+.1f}% / {icfg.momentum_period}d" if mom is not None else "n/a"),
        _comp("breakout", _trend_breakout(price, sma_f, sma_s, pct_h20),
              w["breakout"], price, _breakout_detail(price, sma_f, sma_s)),
        _comp("rsi", _trend_rsi(rsi), w["rsi"],
              rsi, f"RSI {rsi:.0f}" if rsi is not None else "n/a"),
        _comp("near_high", _near_high(pct_h252), w["near_high"],
              pct_h252, f"{pct_h252:+.0f}% vs 52W-Hoch" if pct_h252 is not None else "n/a"),
    ]

    res = aggregate(comps)
    decision, base_pos = decide(res.score, res.red_fraction, scfg)
    pos = apply_market_regime(base_pos, risk_multiplier)
    stop = round(price - 2.0 * atr, 2) if (price and atr) else None

    return Candidate(
        symbol=p.symbol, name=p.name or p.symbol, path="TREND",
        score=round(res.score, 1), decision=decision, position_pct=pos,
        confidence=res.confidence, components=comps, stop_hint=stop,
        metrics={
            "Preis": _fmt_price(price),
            "RelVol": f"{rel_vol:.1f}x" if rel_vol else "–",
            "Mom%": f"{mom:+.0f}" if mom is not None else "–",
            "RSI": f"{rsi:.0f}" if rsi is not None else "–",
            "MCap": _fmt_cap(p.market_cap),
        },
        notes=_low_data_note(res.red_fraction),
    )


def _trend_volume(rel_vol: float | None) -> float | None:
    return ramp(rel_vol, 1.0, 4.0)


def _trend_momentum(mom: float | None) -> float | None:
    return ramp(mom, -5.0, 35.0)


def _trend_breakout(price, sma_f, sma_s, pct_h20) -> float | None:
    parts: list[float] = []
    if price is not None and sma_f is not None:
        parts.append(100.0 if price > sma_f else 0.0)
    if price is not None and sma_s is not None:
        parts.append(100.0 if price > sma_s else 0.0)
    if pct_h20 is not None:
        parts.append(ramp(pct_h20, -12.0, 0.0) or 0.0)
    return mean(parts) if parts else None


def _trend_rsi(r: float | None) -> float | None:
    if r is None:
        return None
    if 55.0 <= r <= 72.0:
        return 100.0
    if r < 55.0:
        return ramp(r, 35.0, 55.0)
    return clamp(100.0 - (r - 72.0) * 5.0)


def _near_high(pct_h252: float | None) -> float | None:
    return ramp(pct_h252, -35.0, 0.0)


# =============================================================================
# GEM PATH (B)
# =============================================================================
def analyze_gem(p: Prelim, chart: dict[str, list] | None,
                summary: dict[str, Any] | None,
                icfg: IndicatorConfig, scfg: ScoringConfig,
                risk_multiplier: float) -> Candidate:
    closes = chart.get("close", []) if chart else []
    highs = chart.get("high", []) if chart else []
    lows = chart.get("low", []) if chart else []
    price = p.price or (ind.clean(closes)[-1] if ind.clean(closes) else None)

    # fundamentals / valuation / ownership / insider from quoteSummary
    margins = dig(summary, "financialData", "profitMargins")
    rev_growth = dig(summary, "financialData", "revenueGrowth")
    earn_growth = dig(summary, "financialData", "earningsGrowth")
    pe = p.trailing_pe if p.trailing_pe is not None else dig(summary, "summaryDetail", "trailingPE")
    ps = dig(summary, "summaryDetail", "priceToSalesTrailing12Months")
    num_analysts = dig(summary, "financialData", "numberOfAnalystOpinions")
    inst_pct = dig(summary, "defaultKeyStatistics", "heldPercentInstitutions")
    net_insider = dig(summary, "netSharePurchaseActivity", "netPercentInsiderShares")
    buy_ct = dig(summary, "netSharePurchaseActivity", "buyInfoCount")
    sell_ct = dig(summary, "netSharePurchaseActivity", "sellInfoCount")
    atr = ind.atr(highs, lows, closes, icfg.atr_period)

    w = scfg.gem_weights
    fundamental = _gem_fundamental(margins, rev_growth, earn_growth)
    valuation = _gem_valuation(pe, ps, scfg)
    base = _gem_base(closes, icfg)
    ownership = _gem_ownership(num_analysts, inst_pct, scfg)
    insider = _gem_insider(net_insider, buy_ct, sell_ct)

    comps = [
        _comp("fundamental", fundamental, w["fundamental"],
              margins, _fund_detail(margins, rev_growth)),
        _comp("valuation", valuation, w["valuation"],
              pe, _val_detail(pe, ps)),
        _comp("base", base, w["base"], None,
              "Bodenbildung/Akkumulation" if base is not None else "n/a"),
        _comp("ownership", ownership, w["ownership"],
              num_analysts, _own_detail(num_analysts, inst_pct)),
        # insider data on Yahoo is delayed -> YELLOW when present, RED if absent
        _comp("insider", insider, w["insider"], net_insider,
              _insider_detail(net_insider, buy_ct, sell_ct),
              yellow=insider is not None),
        # social buzz is not freely available -> always honest RED (n/a)
        Component("early_social", 0.0, w["early_social"], Source.RED,
                  "Social-Frühsignal nicht frei verfügbar"),
    ]

    res = aggregate(comps)
    decision, base_pos = decide(res.score, res.red_fraction, scfg)
    pos = apply_market_regime(base_pos, risk_multiplier)
    stop = round(price - 1.6 * atr, 2) if (price and atr) else None

    return Candidate(
        symbol=p.symbol, name=p.name or p.symbol, path="GEM",
        score=round(res.score, 1), decision=decision, position_pct=pos,
        confidence=res.confidence, components=comps, stop_hint=stop,
        metrics={
            "Preis": _fmt_price(price),
            "MCap": _fmt_cap(p.market_cap),
            "P/E": f"{pe:.1f}" if isinstance(pe, (int, float)) and pe > 0 else "–",
            "Analysten": str(int(num_analysts)) if isinstance(num_analysts, (int, float)) else "–",
            "Inst%": f"{inst_pct*100:.0f}" if isinstance(inst_pct, (int, float)) else "–",
        },
        notes=_low_data_note(res.red_fraction),
    )


def _gem_fundamental(margins, rev_growth, earn_growth) -> float | None:
    return _avg([
        ramp(margins, -0.10, 0.25),
        ramp(rev_growth, -0.05, 0.30),
        ramp(earn_growth, -0.10, 0.40),
    ])


def _gem_valuation(pe, ps, scfg: ScoringConfig) -> float | None:
    parts: list[float | None] = []
    if isinstance(pe, (int, float)) and pe > 0:
        parts.append(ramp(pe, scfg.valuation_pe_cap * 1.6, 5.0))
    if isinstance(ps, (int, float)) and ps > 0:
        parts.append(ramp(ps, scfg.valuation_ps_cap * 2.0, 0.4))
    return _avg(parts)


def _gem_base(closes, icfg: IndicatorConfig) -> float | None:
    cl = ind.clean(closes)
    if len(cl) < icfg.base_lookback:
        return None
    parts: list[float | None] = []
    rv = ind.realized_vol(cl, icfg.base_lookback)
    parts.append(ramp(rv, 0.055, 0.012))                 # calm base preferred
    slope = ind.linreg_slope_pct(cl, icfg.base_lookback)
    parts.append(ramp(slope, -0.6, 0.5))                 # flat -> gently rising
    window = cl[-icfg.high_low_lookback:] if len(cl) > icfg.high_low_lookback else cl
    lo, hi = min(window), max(window)
    if hi > lo:
        rng = (cl[-1] - lo) / (hi - lo)                  # 0=low, 1=high
        parts.append(clamp(100.0 - abs(rng - 0.35) / 0.65 * 100.0))  # peak lower-mid
    return _avg(parts)


def _gem_ownership(num_analysts, inst_pct, scfg: ScoringConfig) -> float | None:
    parts: list[float | None] = []
    if isinstance(num_analysts, (int, float)):
        parts.append(clamp(100.0 - max(0.0, num_analysts - scfg.max_analysts) * 8.0))
    if isinstance(inst_pct, (int, float)) and inst_pct >= 0:
        if inst_pct <= scfg.max_institutional:
            parts.append(60.0 + (ramp(inst_pct, 0.0, scfg.max_institutional) or 0) * 0.4)
        else:
            parts.append(ramp(inst_pct, 0.85, scfg.max_institutional) or 0.0)
    return _avg(parts)


def _gem_insider(net_insider, buy_ct, sell_ct) -> float | None:
    if net_insider is None and buy_ct is None and sell_ct is None:
        return None
    parts: list[float | None] = []
    if isinstance(net_insider, (int, float)):
        parts.append(ramp(net_insider, -0.02, 0.05))     # net buying -> high
    if isinstance(buy_ct, (int, float)) and isinstance(sell_ct, (int, float)):
        total = buy_ct + sell_ct
        if total > 0:
            parts.append(clamp(buy_ct / total * 100.0))
    return _avg(parts)


# --- component + formatting helpers ------------------------------------------
def _comp(name: str, score: float | None, weight: float, raw_val: Any,
          detail: str, yellow: bool = False) -> Component:
    """Build a Component, choosing the traffic-light colour from availability."""
    if score is None:
        return Component(name, 0.0, weight, Source.RED, detail or "n/a")
    src = Source.YELLOW if yellow else Source.GREEN
    return Component(name, score, weight, src, detail)


def _fmt_price(p) -> str:
    return f"${p:,.2f}" if isinstance(p, (int, float)) else "–"


def _fmt_cap(c) -> str:
    if not isinstance(c, (int, float)) or c <= 0:
        return "–"
    if c >= 1e9:
        return f"${c/1e9:.1f}B"
    return f"${c/1e6:.0f}M"


def _breakout_detail(price, sma_f, sma_s) -> str:
    if price is None:
        return "n/a"
    flags = []
    if sma_f is not None:
        flags.append("▲MA20" if price > sma_f else "▼MA20")
    if sma_s is not None:
        flags.append("▲MA50" if price > sma_s else "▼MA50")
    return " ".join(flags) if flags else "n/a"


def _fund_detail(margins, rev_growth) -> str:
    bits = []
    if isinstance(margins, (int, float)):
        bits.append(f"Marge {margins*100:.0f}%")
    if isinstance(rev_growth, (int, float)):
        bits.append(f"Umsatz {rev_growth*100:+.0f}%")
    return ", ".join(bits) if bits else "n/a"


def _val_detail(pe, ps) -> str:
    bits = []
    if isinstance(pe, (int, float)) and pe > 0:
        bits.append(f"P/E {pe:.1f}")
    if isinstance(ps, (int, float)) and ps > 0:
        bits.append(f"P/S {ps:.1f}")
    return ", ".join(bits) if bits else "n/a"


def _own_detail(num_analysts, inst_pct) -> str:
    bits = []
    if isinstance(num_analysts, (int, float)):
        bits.append(f"{int(num_analysts)} Analysten")
    if isinstance(inst_pct, (int, float)):
        bits.append(f"Inst {inst_pct*100:.0f}%")
    return ", ".join(bits) if bits else "n/a"


def _insider_detail(net, buy_ct, sell_ct) -> str:
    if isinstance(net, (int, float)):
        return f"Insider netto {net*100:+.1f}%"
    if isinstance(buy_ct, (int, float)) and isinstance(sell_ct, (int, float)):
        return f"Insider Käufe {int(buy_ct)}/Verk {int(sell_ct)}"
    return "n/a"


def _low_data_note(red_fraction: float) -> str:
    if red_fraction > 0.40:
        return "⚠ >40% Score auf 🔴-Daten → auf Watchlist begrenzt"
    return ""

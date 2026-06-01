"""Pipeline orchestration — the 3-stage funnel that realises the efficiency goal.

Stage 1  broad & cheap : screeners + trending -> compact Prelim records.
Stage 2  cheap, batched: v7 batch-quote fills gaps, pre-rank, keep top survivors.
Stage 3  narrow & dear : chart + quoteSummary ONLY for survivors (concurrent,
                         rate-limited); both paths scored; routed to bounded heaps.

Memory discipline: raw payloads are extracted into small records and then
dropped; non-survivors are released between stages; results live only in
fixed-size top-K heaps.
"""
from __future__ import annotations

import heapq
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from . import analyze
from . import indicators as ind
from .config import AppConfig
from .models import Candidate, MarketContext, Prelim
from .yahoo import YahooClient


@dataclass
class Report:
    market: MarketContext
    trending: list[Candidate]
    gems: list[Candidate]
    scanned: int          # symbols deep-analysed
    universe_size: int    # symbols after hard filter


class BoundedTopK:
    """A min-heap that keeps only the K highest-scoring candidates."""

    def __init__(self, k: int):
        self.k = max(1, k)
        self._heap: list[Candidate] = []

    def push(self, c: Candidate) -> None:
        if len(self._heap) < self.k:
            heapq.heappush(self._heap, c)
        elif c > self._heap[0]:
            heapq.heapreplace(self._heap, c)

    def sorted_desc(self) -> list[Candidate]:
        return sorted(self._heap, reverse=True)


# --- Stage 0: market context --------------------------------------------------
def fetch_market_context(client: YahooClient) -> MarketContext:
    mc = MarketContext()
    chart = client.chart("^GSPC", rng="1y", interval="1d")
    if chart:
        closes = ind.clean(chart.get("close", []))
        if closes:
            mc.spx_price = closes[-1]
            mc.spx_sma200 = ind.sma(closes, 200)
        del chart
    quotes = client.batch_quote(["^VIX"])
    if quotes:
        mc.vix = quotes[0].get("regularMarketPrice")
    del quotes

    above = (mc.spx_price is not None and mc.spx_sma200 is not None
             and mc.spx_price > mc.spx_sma200)
    vix = mc.vix if isinstance(mc.vix, (int, float)) else 20.0
    if above and vix < 20:
        mc.regime, mc.risk_multiplier = "Risk-On", 1.0
    elif above and vix < 28:
        mc.regime, mc.risk_multiplier = "Neutral", 0.8
    else:
        mc.regime, mc.risk_multiplier = "Risk-Off", 0.5
    mc.note = (f"S&P {'>' if above else '<'} 200-MA · "
               f"VIX {vix:.0f} → Positionsgrößen ×{mc.risk_multiplier}")
    return mc


# --- Stage 1: universe --------------------------------------------------------
def _prelim_from_quote(q: dict[str, Any]) -> Prelim | None:
    sym = q.get("symbol")
    if not sym:
        return None
    if q.get("quoteType") not in (None, "EQUITY"):
        return None
    return Prelim(
        symbol=sym,
        name=q.get("displayName") or q.get("shortName") or q.get("longName") or sym,
        price=q.get("regularMarketPrice"),
        market_cap=q.get("marketCap"),
        avg_volume=q.get("averageDailyVolume3Month") or q.get("averageDailyVolume10Day"),
        reg_volume=q.get("regularMarketVolume"),
        trailing_pe=q.get("trailingPE"),
        week52_high=q.get("fiftyTwoWeekHigh"),
        week52_low=q.get("fiftyTwoWeekLow"),
        change_pct=q.get("regularMarketChangePercent"),
        analyst_rating=q.get("averageAnalystRating"),
    )


def build_universe(client: YahooClient, cfg: AppConfig) -> dict[str, Prelim]:
    prelims: dict[str, Prelim] = {}
    ucfg = cfg.universe

    for scr in ucfg.screeners:
        if len(prelims) >= ucfg.max_universe:
            break
        rows = client.screener(scr, ucfg.per_screener)
        for q in rows:
            p = _prelim_from_quote(q)
            if p is None:
                continue
            existing = prelims.get(p.symbol)
            if existing:
                existing.sources.add(scr)
            else:
                p.sources.add(scr)
                prelims[p.symbol] = p
            if len(prelims) >= ucfg.max_universe:
                break
        del rows                                   # drop raw screener payload

    if ucfg.use_trending:
        for sym in client.trending(ucfg.trending_count):
            if sym not in prelims and len(prelims) < ucfg.max_universe:
                p = Prelim(symbol=sym)
                p.sources.add("trending")
                prelims[sym] = p
    return prelims


def _passes_hard_filter(p: Prelim, cfg: AppConfig) -> bool:
    u = cfg.universe
    if p.price is None or not (u.min_price <= p.price <= u.max_price):
        return False
    if p.market_cap is None or not (u.min_market_cap <= p.market_cap <= u.max_market_cap):
        return False
    if p.avg_volume is None or p.avg_volume < u.min_avg_volume:
        return False
    return True


# --- Stage 2: enrich + pre-rank ----------------------------------------------
def enrich_and_rank(client: YahooClient, prelims: dict[str, Prelim],
                    cfg: AppConfig) -> list[Prelim]:
    missing = [s for s, p in prelims.items()
               if p.price is None or p.market_cap is None or p.avg_volume is None]
    if missing:
        for q in client.batch_quote(missing, cfg.fetch.batch_size):
            sym = q.get("symbol")
            p = prelims.get(sym)
            if not p:
                continue
            p.price = p.price or q.get("regularMarketPrice")
            p.market_cap = p.market_cap or q.get("marketCap")
            p.avg_volume = (p.avg_volume or q.get("averageDailyVolume3Month")
                            or q.get("averageDailyVolume10Day"))
            p.reg_volume = p.reg_volume or q.get("regularMarketVolume")
            p.trailing_pe = p.trailing_pe or q.get("trailingPE")
            p.week52_high = p.week52_high or q.get("fiftyTwoWeekHigh")
            p.week52_low = p.week52_low or q.get("fiftyTwoWeekLow")
            if p.change_pct is None:
                p.change_pct = q.get("regularMarketChangePercent")
            if p.name in ("", p.symbol):
                p.name = (q.get("displayName") or q.get("shortName")
                          or q.get("longName") or p.symbol)

    survivors = [p for p in prelims.values() if _passes_hard_filter(p, cfg)]
    return _balanced_select(survivors, cfg.fetch.deep_limit)


def _balanced_select(survivors: list[Prelim], deep_limit: int) -> list[Prelim]:
    """Interleave the best trend-lean and gem-lean prelims so the Stage-3 budget
    is shared — guaranteeing both tables get candidates."""
    by_trend = sorted(survivors, key=analyze.pre_score_trend, reverse=True)
    by_gem = sorted(survivors, key=analyze.pre_score_gem, reverse=True)
    result: list[Prelim] = []
    seen: set[str] = set()
    ti = gi = 0
    while len(result) < deep_limit and (ti < len(by_trend) or gi < len(by_gem)):
        if ti < len(by_trend):
            p = by_trend[ti]; ti += 1
            if p.symbol not in seen:
                seen.add(p.symbol); result.append(p)
        if len(result) >= deep_limit:
            break
        if gi < len(by_gem):
            p = by_gem[gi]; gi += 1
            if p.symbol not in seen:
                seen.add(p.symbol); result.append(p)
    return result


# --- Stage 3: deep analysis ---------------------------------------------------
def _deep_fetch(client: YahooClient, p: Prelim, cfg: AppConfig
                ) -> tuple[Prelim, dict | None, dict | None]:
    chart = client.chart(p.symbol, cfg.fetch.chart_range, cfg.fetch.chart_interval)
    summary = client.quote_summary(p.symbol, analyze.SUMMARY_MODULES)
    return p, chart, summary


def deep_analyse(client: YahooClient, survivors: list[Prelim],
                 market: MarketContext, cfg: AppConfig
                 ) -> tuple[list[Candidate], list[Candidate], int]:
    trend_heap = BoundedTopK(cfg.output.top_k)
    gem_heap = BoundedTopK(cfg.output.top_k)
    scanned = 0
    rm = market.risk_multiplier

    with ThreadPoolExecutor(max_workers=cfg.fetch.concurrency) as ex:
        futures = [ex.submit(_deep_fetch, client, p, cfg) for p in survivors]
        for fut in futures:
            try:
                p, chart, summary = fut.result()
            except Exception:                      # one bad symbol must not kill the run
                continue
            scanned += 1
            t = analyze.analyze_trending(p, chart, cfg.indicators, cfg.scoring, rm)
            g = analyze.analyze_gem(p, chart, summary, cfg.indicators, cfg.scoring, rm)
            # route to the better-fitting single path -> no duplicates across tables
            if t.score >= g.score:
                trend_heap.push(t)
            else:
                gem_heap.push(g)
            del chart, summary                     # discard raw payloads immediately

    return trend_heap.sorted_desc(), gem_heap.sorted_desc(), scanned


# --- top-level entry ----------------------------------------------------------
def run_scan(client: YahooClient, cfg: AppConfig) -> Report:
    if cfg.verbose:
        print("• Stage 0: Marktkontext …")
    market = fetch_market_context(client)

    if cfg.verbose:
        print("• Stage 1: Universe aufbauen …")
    prelims = build_universe(client, cfg)
    if cfg.verbose:
        print(f"  {len(prelims)} Roh-Symbole gesammelt")

    if cfg.verbose:
        print("• Stage 2: Batch-Anreicherung + Vor-Ranking …")
    survivors = enrich_and_rank(client, prelims, cfg)
    universe_size = sum(1 for p in prelims.values() if _passes_hard_filter(p, cfg))
    del prelims                                    # release the non-survivors

    if cfg.verbose:
        print(f"  {universe_size} nach Hard-Filter · {len(survivors)} für Tiefenanalyse")
        print("• Stage 3: Tiefenanalyse (parallel, gedrosselt) …")
    trending, gems, scanned = deep_analyse(client, survivors, market, cfg)
    del survivors

    return Report(market=market, trending=trending, gems=gems,
                  scanned=scanned, universe_size=universe_size)

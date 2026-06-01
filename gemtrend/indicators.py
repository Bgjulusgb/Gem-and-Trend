"""Pure-Python technical indicators.

No numpy/pandas on purpose: the inputs are short OHLCV lists, the math is
simple, and avoiding heavy array libraries keeps both dependencies and memory
overhead minimal. Every function is None-safe: given insufficient or dirty data
it returns ``None`` instead of raising, so the pipeline never crashes on a
single bad symbol.
"""
from __future__ import annotations

from math import sqrt
from typing import Sequence


Num = float | int | None


def clean(values: Sequence[Num]) -> list[float]:
    """Drop ``None`` / non-finite entries, preserving order."""
    out: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
            continue
        out.append(f)
    return out


def sma(values: Sequence[Num], period: int) -> float | None:
    vals = clean(values)
    if period <= 0 or len(vals) < period:
        return None
    return sum(vals[-period:]) / period


def ema(values: Sequence[Num], period: int) -> float | None:
    vals = clean(values)
    if period <= 0 or len(vals) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(vals[:period]) / period          # seed with SMA
    for v in vals[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: Sequence[Num], period: int = 14) -> float | None:
    """Relative Strength Index using Wilder's smoothing."""
    vals = clean(closes)
    if len(vals) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(highs: Sequence[Num], lows: Sequence[Num], closes: Sequence[Num],
        period: int = 14) -> float | None:
    """Average True Range (Wilder). Inputs must be aligned/equal length."""
    h, l, c = clean(highs), clean(lows), clean(closes)
    n = min(len(h), len(l), len(c))
    if n < period + 1:
        return None
    h, l, c = h[-n:], l[-n:], c[-n:]
    trs = []
    for i in range(1, n):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs.append(tr)
    a = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a


def momentum_pct(closes: Sequence[Num], period: int) -> float | None:
    """Percent return over ``period`` bars."""
    vals = clean(closes)
    if len(vals) < period + 1:
        return None
    past = vals[-period - 1]
    if past == 0:
        return None
    return (vals[-1] / past - 1.0) * 100.0


def rel_volume(volumes: Sequence[Num], lookback: int = 20) -> float | None:
    """Latest volume divided by the average of the prior ``lookback`` bars."""
    vals = clean(volumes)
    if len(vals) < lookback + 1:
        return None
    window = vals[-lookback - 1:-1]
    avg = sum(window) / len(window)
    if avg <= 0:
        return None
    return vals[-1] / avg


def volume_zscore(volumes: Sequence[Num], lookback: int = 20) -> float | None:
    vals = clean(volumes)
    if len(vals) < lookback + 1:
        return None
    window = vals[-lookback - 1:-1]
    mean = sum(window) / len(window)
    var = sum((v - mean) ** 2 for v in window) / len(window)
    sd = sqrt(var)
    if sd == 0:
        return None
    return (vals[-1] - mean) / sd


def pct_from_high(closes: Sequence[Num], lookback: int) -> float | None:
    """How far the latest close sits below the lookback high (<=0 means at high)."""
    vals = clean(closes)
    if not vals:
        return None
    window = vals[-lookback:] if lookback < len(vals) else vals
    hi = max(window)
    if hi == 0:
        return None
    return (vals[-1] / hi - 1.0) * 100.0


def pct_from_low(closes: Sequence[Num], lookback: int) -> float | None:
    """How far the latest close sits above the lookback low (>=0)."""
    vals = clean(closes)
    if not vals:
        return None
    window = vals[-lookback:] if lookback < len(vals) else vals
    lo = min(window)
    if lo == 0:
        return None
    return (vals[-1] / lo - 1.0) * 100.0


def realized_vol(closes: Sequence[Num], period: int) -> float | None:
    """Std-dev of daily returns over ``period`` bars (lower => calmer base)."""
    vals = clean(closes)
    if len(vals) < period + 1:
        return None
    window = vals[-period - 1:]
    rets = [window[i] / window[i - 1] - 1.0
            for i in range(1, len(window)) if window[i - 1] != 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return sqrt(var)


def linreg_slope_pct(values: Sequence[Num], period: int) -> float | None:
    """Least-squares slope over ``period`` bars, normalised to % of mean per bar.

    Positive => gently rising (accumulation), near-zero => flat base.
    """
    vals = clean(values)
    if len(vals) < period:
        return None
    y = vals[-period:]
    n = len(y)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(y) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0 or mean_y == 0:
        return None
    slope = sum((xs[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / denom
    return slope / mean_y * 100.0

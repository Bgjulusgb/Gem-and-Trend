"""Unit tests for the pure technical indicators (deterministic, no network)."""
import math

from gemtrend import indicators as ind


def test_clean_drops_none_and_nonfinite():
    assert ind.clean([1, None, 2.0, float("nan"), float("inf"), "x", 3]) == [1.0, 2.0, 3.0]


def test_sma_basic():
    assert ind.sma([1, 2, 3, 4, 5], 5) == 3.0
    assert ind.sma([2, 4, 6], 2) == 5.0
    assert ind.sma([1, 2], 5) is None          # not enough data


def test_ema_seeded_with_sma():
    # With a flat series, EMA equals the value.
    assert math.isclose(ind.ema([5, 5, 5, 5, 5], 3), 5.0, rel_tol=1e-9)


def test_rsi_all_gains_is_100():
    assert ind.rsi(list(range(1, 20)), 14) == 100.0


def test_rsi_known_midrange():
    # Alternating up/down around a level keeps RSI near 50.
    closes = [10 + (1 if i % 2 == 0 else -1) for i in range(40)]
    r = ind.rsi(closes, 14)
    assert r is not None and 30 < r < 70


def test_atr_positive():
    highs = [11, 12, 13, 12, 14, 15, 16, 15, 17, 18, 19, 18, 20, 21, 22, 21]
    lows = [9, 10, 11, 10, 12, 13, 14, 13, 15, 16, 17, 16, 18, 19, 20, 19]
    closes = [10, 11, 12, 11, 13, 14, 15, 14, 16, 17, 18, 17, 19, 20, 21, 20]
    a = ind.atr(highs, lows, closes, 14)
    assert a is not None and a > 0


def test_momentum_pct():
    assert math.isclose(ind.momentum_pct([100, 0, 0, 110], 3), 10.0, rel_tol=1e-9)
    assert ind.momentum_pct([100], 3) is None


def test_rel_volume():
    vols = [100] * 20 + [300]
    assert math.isclose(ind.rel_volume(vols, 20), 3.0, rel_tol=1e-9)


def test_volume_zscore_zero_when_flat():
    assert ind.volume_zscore([100] * 21, 20) is None   # zero std -> None


def test_pct_from_high_and_low():
    closes = [10, 20, 15]
    assert math.isclose(ind.pct_from_high(closes, 3), (15 / 20 - 1) * 100, rel_tol=1e-9)
    assert math.isclose(ind.pct_from_low(closes, 3), (15 / 10 - 1) * 100, rel_tol=1e-9)


def test_linreg_slope_sign():
    rising = ind.linreg_slope_pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 10)
    falling = ind.linreg_slope_pct([10, 9, 8, 7, 6, 5, 4, 3, 2, 1], 10)
    assert rising is not None and rising > 0
    assert falling is not None and falling < 0


def test_realized_vol_calm_vs_wild():
    calm = ind.realized_vol([100, 100.1, 100.0, 100.2, 100.1, 100.3], 5)
    wild = ind.realized_vol([100, 130, 90, 140, 80, 150], 5)
    assert calm is not None and wild is not None and wild > calm

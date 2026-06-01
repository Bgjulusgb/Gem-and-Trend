"""Unit tests for normalisation, the traffic-light aggregation and the decision
ladder (the anti-hallucination logic)."""
from gemtrend.config import ScoringConfig
from gemtrend.models import Component, Source
from gemtrend.scoring import aggregate, decide, ramp, clamp


def test_ramp_clamps_and_inverts():
    assert ramp(0, 0, 100) == 0.0
    assert ramp(100, 0, 100) == 100.0
    assert ramp(50, 0, 100) == 50.0
    assert ramp(-5, 0, 100) == 0.0           # clamped low
    assert ramp(200, 0, 100) == 100.0        # clamped high
    # inverted (smaller is better)
    assert ramp(5, 40, 5) == 100.0
    assert ramp(40, 40, 5) == 0.0
    assert ramp(None, 0, 100) is None


def test_aggregate_all_green():
    comps = [
        Component("a", 80, 0.5, Source.GREEN),
        Component("b", 60, 0.5, Source.GREEN),
    ]
    res = aggregate(comps)
    assert abs(res.score - 70.0) < 1e-9
    assert res.confidence == 1.0
    assert res.red_fraction == 0.0


def test_aggregate_red_is_excluded_and_renormalised():
    # RED component must not contribute its (zero) score; weights renormalise.
    comps = [
        Component("a", 90, 0.5, Source.GREEN),
        Component("b", 0, 0.5, Source.RED),
    ]
    res = aggregate(comps)
    assert abs(res.score - 90.0) < 1e-9       # not 45 -> RED dropped, not averaged in
    assert abs(res.red_fraction - 0.5) < 1e-9
    assert abs(res.confidence - 0.5) < 1e-9


def test_decision_ladder():
    cfg = ScoringConfig()
    assert decide(40, 0.0, cfg)[0] == "Nicht handeln"
    assert decide(55, 0.0, cfg)[0] == "Watchlist"
    assert decide(70, 0.0, cfg)[0] == "Kleine Position"
    assert decide(85, 0.0, cfg)[1] == cfg.normal_pos
    assert decide(97, 0.0, cfg)[1] == cfg.full_pos


def test_red_guard_caps_at_watchlist():
    cfg = ScoringConfig()
    # High score but >40% of weight was RED -> capped at Watchlist, no position.
    label, pos = decide(90, 0.5, cfg)
    assert "Watchlist" in label
    assert pos == 0.0


def test_clamp_bounds():
    assert clamp(-10) == 0.0
    assert clamp(150) == 100.0
    assert clamp(42) == 42.0

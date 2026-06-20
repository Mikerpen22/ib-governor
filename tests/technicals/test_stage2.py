from governor.config import EquitySetupRules
from governor.technicals.stage2 import compute_stage2
from governor.technicals.types import Bar

def _trend_bars(n, start, step, spread=1.0):
    bars = []
    for i in range(n):
        c = start + i * step
        bars.append(Bar(date=str(i), open=c, high=c + spread, low=c - spread, close=c, volume=100.0))
    return bars

def test_clean_uptrend_is_confirmed():
    # 260 bars rising 50 -> ~180: price above all MAs, stacked, rising, near highs
    bars = _trend_bars(260, 50.0, 0.5)
    r = compute_stage2(bars, EquitySetupRules())
    assert r.classification == "confirmed"
    assert r.pass_count >= 6
    assert r.slope_up is True

def test_downtrend_is_not_stage2():
    bars = _trend_bars(260, 180.0, -0.5)
    r = compute_stage2(bars, EquitySetupRules())
    assert r.classification == "none"

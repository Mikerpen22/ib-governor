from governor.config import FuturesSetupRules
from governor.gate.intent import Action
from governor.technicals.futures_setup import compute_futures_setup
from governor.technicals.types import Bar

def _trend_bars(n, start, step, spread=2.0):
    return [Bar(date=str(i), open=start + i * step, high=start + i * step + spread,
                low=start + i * step - spread, close=start + i * step, volume=100.0)
            for i in range(n)]

def test_long_into_uptrend_is_with_trend():
    bars = _trend_bars(260, 100.0, 1.0)
    fs = compute_futures_setup(bars, Action.BUY, FuturesSetupRules())
    assert fs.with_trend is True and fs.counter_trend is False

def test_short_into_uptrend_is_counter_trend_and_poor():
    bars = _trend_bars(260, 100.0, 1.0)
    fs = compute_futures_setup(bars, Action.SELL, FuturesSetupRules())
    assert fs.counter_trend is True
    assert fs.poor is True   # counter-trend alone -> poor

def test_buying_at_20d_high_is_chasing():
    bars = _trend_bars(260, 100.0, 1.0)  # last close is the highest -> at the 20d high
    fs = compute_futures_setup(bars, Action.BUY, FuturesSetupRules())
    assert fs.chasing is True

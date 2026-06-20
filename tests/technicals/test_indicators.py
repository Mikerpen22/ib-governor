from governor.technicals.indicators import (
    sma, rolling_sma, slope_up, atr, rsi, roc, pct_from_high, pct_from_low, percentile_rank,
)
from governor.technicals.types import Bar

def _bars(closes):
    # flat-range bars; high/low straddle close by 1 so TR is well-defined
    return [Bar(date=str(i), open=c, high=c + 1, low=c - 1, close=c, volume=100.0)
            for i, c in enumerate(closes)]

def test_sma_last_n():
    assert sma([1, 2, 3, 4, 5], 3) == 4.0          # (3+4+5)/3
    assert sma([1, 2], 3) is None                   # too short

def test_rolling_sma_len_and_values():
    s = rolling_sma([1, 2, 3, 4], 2)
    assert s == [1.5, 2.5, 3.5]

def test_slope_up():
    assert slope_up([1, 2, 3, 4], 2) is True        # 4 > 2
    assert slope_up([4, 3, 2, 1], 2) is False
    assert slope_up([1, 2], 5) is False             # too short

def test_rsi_extremes():
    assert rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], 14) == 100.0
    assert rsi(list(range(16, 0, -1)), 14) == 0.0

def test_roc():
    import pytest
    assert roc([100, 110], 1) == pytest.approx(0.10)

def test_pct_from_high_low():
    assert pct_from_high(90, 100) == -0.10
    assert pct_from_low(110, 100) == 0.10

def test_percentile_rank():
    assert percentile_rank([1, 2, 3, 4], 3) == 0.75   # 3 of 4 <= 3
    assert percentile_rank([], 5) == 0.0

def test_atr_simple():
    # each TR = 2 (high-low), so ATR = 2
    bars = _bars([10, 10, 10, 10, 10])
    assert atr(bars, 3) == 2.0

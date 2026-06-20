from governor.config import SetupRules
from governor.gate.intent import Action, SecType
from governor.technicals.assess import assess_setup, setup_to_dict
from governor.technicals.types import Bar

def _trend_bars(n, start, step, spread=1.0):
    return [Bar(date=str(i), open=start + i * step, high=start + i * step + spread,
                low=start + i * step - spread, close=start + i * step, volume=100.0)
            for i in range(n)]

def test_short_history_unavailable():
    s = assess_setup(SecType.STK, Action.BUY, _trend_bars(50, 10, 1), SetupRules())
    assert s.available is False and s.poor is False

def test_none_bars_unavailable():
    s = assess_setup(SecType.STK, Action.BUY, None, SetupRules())
    assert s.available is False

def test_buy_confirmed_uptrend_not_poor():
    s = assess_setup(SecType.STK, Action.BUY, _trend_bars(260, 50, 0.5), SetupRules())
    assert s.available is True and s.asset_class == "equity"
    assert s.poor is False     # confirmed Stage 2, VCP stubbed unavailable

def test_buy_downtrend_is_poor_with_reason():
    s = assess_setup(SecType.STK, Action.BUY, _trend_bars(260, 180, -0.5), SetupRules())
    assert s.poor is True
    assert any("Stage 2" in r for r in s.caution_reasons)

def test_equity_sell_never_poor():
    # an exit/trim is not judged on buy-setup quality
    s = assess_setup(SecType.STK, Action.SELL, _trend_bars(260, 180, -0.5), SetupRules())
    assert s.poor is False

def test_setup_to_dict_roundtrips_keys():
    s = assess_setup(SecType.STK, Action.BUY, _trend_bars(260, 50, 0.5), SetupRules())
    d = setup_to_dict(s)
    assert d["available"] is True and d["asset_class"] == "equity" and "equity" in d


# --- Futures path through assess_setup ---

def test_futures_short_into_uptrend_is_poor():
    # A clean uptrend: price > ma20 > ma50 > ma200. SELL into it -> counter_trend -> poor.
    # Use spread=2.0 so highs/lows straddle the close cleanly (matches futures bar helper).
    bars = _trend_bars(260, 100.0, 1.0, spread=2.0)
    s = assess_setup(SecType.FUT, Action.SELL, bars, SetupRules())
    assert s.available is True
    assert s.asset_class == "future"
    assert s.poor is True
    assert len(s.caution_reasons) > 0
    assert any("counter-trend" in r for r in s.caution_reasons)

def test_futures_short_history_unavailable():
    # Fewer bars than min_bars (200) -> available=False regardless of sec_type.
    bars = _trend_bars(50, 100.0, 1.0, spread=2.0)
    s = assess_setup(SecType.FUT, Action.BUY, bars, SetupRules())
    assert s.available is False
    assert s.asset_class == "future"
    assert s.poor is False

def test_futures_setup_to_dict_contains_futures_not_equity():
    # setup_to_dict on a futures result must have "futures" key with expected fields
    # and must NOT have "equity" key.
    bars = _trend_bars(260, 100.0, 1.0, spread=2.0)
    s = assess_setup(SecType.FUT, Action.SELL, bars, SetupRules())
    d = setup_to_dict(s)
    assert d["available"] is True
    assert d["asset_class"] == "future"
    assert "futures" in d
    assert "equity" not in d
    fut = d["futures"]
    # All expected fields must be present.
    for key in ("with_trend", "counter_trend", "trend_label", "atr_pctile",
                "vol_label", "rsi", "momentum_label"):
        assert key in fut, f"missing key: {key}"
    # Counter-trend SELL into uptrend.
    assert fut["with_trend"] is False
    assert fut["counter_trend"] is True
    assert fut["trend_label"] == "uptrend"

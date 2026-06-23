"""Read-only technicals CLI: qualify → fetch daily bars → assess setup.

The safety-relevant property: it PLACES nothing and STAGES nothing. The fake ib's
placeOrder / whatIfOrder raise, so the call completing at all proves the read-only
path never touches them.
"""
from __future__ import annotations

from types import SimpleNamespace

from governor.config import RulesConfig
from governor.technicals.cli import assess_symbol, render_text


def _bar(o, h, l, c, v=1_000_000.0):
    return SimpleNamespace(date="2026-06-01", open=o, high=h, low=l, close=c, volume=v)


def _uptrend(n=260, start=50.0, step=0.4):
    bars, price = [], start
    for _ in range(n):
        o = price
        c = price + step
        bars.append(_bar(o, c + 0.2, o - 0.2, c))
        price = c
    return bars


def _fake_ib(bars):
    """Duck-typed ib: qualifies the contract + returns canned bars. placeOrder and
    whatIfOrder raise — proving the read-only path never reaches an order."""
    def _must_not(*a, **k):
        raise AssertionError("technicals CLI must not place / whatif an order")
    return SimpleNamespace(
        qualifyContracts=lambda c: [c],
        reqHistoricalData=lambda *a, **k: bars,
        placeOrder=_must_not,
        whatIfOrder=_must_not,
    )


def test_assess_symbol_returns_setup_and_touches_no_order_path():
    result = assess_symbol(_fake_ib(_uptrend()), "nvda", "stk", RulesConfig())
    assert result["symbol"] == "NVDA" and result["sec_type"] == "STK"
    assert result["setup"]["available"] is True          # 260 bars → a real read
    # reaching here proves placeOrder/whatIfOrder (which raise) were never called


def test_assess_symbol_insufficient_history_is_unavailable():
    result = assess_symbol(_fake_ib(_uptrend(n=10)), "AAPL", "stk", RulesConfig())
    assert result["setup"]["available"] is False


def test_render_text_names_the_symbol():
    result = assess_symbol(_fake_ib(_uptrend()), "NVDA", "stk", RulesConfig())
    assert "NVDA" in render_text(result)


def test_render_text_handles_unavailable():
    result = {"symbol": "AAPL", "sec_type": "STK", "setup": {"available": False}}
    assert "insufficient" in render_text(result).lower()

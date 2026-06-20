# tests/live/test_history.py
from types import SimpleNamespace
from governor.live.history import fetch_daily_bars
from governor.technicals.types import Bar


class _FakeIB:
    def __init__(self, bars=None, raise_exc=False):
        self._bars, self._raise = bars, raise_exc

    def reqHistoricalData(self, *a, **k):
        if self._raise:
            raise RuntimeError("hist timeout")
        return self._bars


def _raw(n):
    return [SimpleNamespace(date=f"2026-01-{i+1:02d}", open=1.0, high=2.0, low=0.5,
                            close=1.5, volume=100.0) for i in range(n)]


def test_returns_bars():
    out = fetch_daily_bars(_FakeIB(_raw(3)), object(), "1 Y")
    assert len(out) == 3 and isinstance(out[0], Bar) and out[0].close == 1.5


def test_failsoft_on_exception_returns_none():
    assert fetch_daily_bars(_FakeIB(raise_exc=True), object(), "1 Y") is None


def test_empty_returns_none():
    assert fetch_daily_bars(_FakeIB([]), object(), "1 Y") is None

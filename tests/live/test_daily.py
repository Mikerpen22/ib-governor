"""Tests for governor.live.daily.collect_day_data.

Uses lightweight duck-typed fakes — no real IB connection needed.
"""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from governor.config import RulesConfig
from governor.live.daily import (
    VIX_ELEVATED_THRESHOLD,
    collect_account_view,
    collect_day_data,
    collect_market_backdrop,
    fetch_account_pnl,
)

ET = ZoneInfo("America/New_York")

_TODAY = dt.datetime(2026, 6, 19, 14, 30, tzinfo=ET)

# IBKR unset sentinel — must be excluded from P&L sum
_SENTINEL = 1.79e308


# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------

def _account_value(tag: str, value: str, currency: str = "USD"):
    return SimpleNamespace(tag=tag, value=value, currency=currency, account="U1")


def _portfolio_item(
    sec_type: str,
    position: float,
    market_value: float,
    unrealized_pnl: float = 0.0,
    symbol: str = "AAPL",
):
    contract = SimpleNamespace(secType=sec_type, symbol=symbol, localSymbol=symbol)
    return SimpleNamespace(
        contract=contract,
        position=position,
        marketValue=market_value,
        unrealizedPNL=unrealized_pnl,
    )


def _fill(
    sec_type: str,
    realized_pnl: float,
    order_id: int,
    symbol: str = "AAPL",
    side: str = "BOT",
    avg_price: float = 100.0,
    shares: float = 10.0,
    time: dt.datetime | None = None,
):
    contract = SimpleNamespace(secType=sec_type, symbol=symbol, localSymbol=symbol)
    commission = SimpleNamespace(realizedPNL=realized_pnl)
    execution = SimpleNamespace(
        orderId=order_id,
        side=side,
        avgPrice=avg_price,
        shares=shares,
        time=time or dt.datetime(2026, 6, 19, 11, 0),  # today by default
    )
    return SimpleNamespace(
        contract=contract,
        commissionReport=commission,
        execution=execution,
    )


def _fake_ib(
    nav: float = 250_000.0,
    portfolio: list | None = None,
    fills: list | None = None,
    req_historical_data=None,
) -> SimpleNamespace:
    account_values = [
        _account_value("NetLiquidation", str(nav)),
        _account_value("ExcessLiquidity", str(nav * 0.5)),
        _account_value("GrossPositionValue", str(nav * 0.8)),
    ]
    ib = SimpleNamespace(
        accountValues=lambda: account_values,
        portfolio=lambda: portfolio or [],
        fills=lambda: fills or [],
    )
    if req_historical_data is not None:
        ib.reqHistoricalData = req_historical_data
    return ib


# ---------------------------------------------------------------------------
# Market-backdrop fake helpers
# ---------------------------------------------------------------------------

def _bar(close: float):
    """A minimal duck-typed daily bar — only .close is read by the collector."""
    return SimpleNamespace(close=close)


def _contract_key(contract) -> str:
    """How the fake reqHistoricalData identifies which symbol is requested.

    Stock(...) carries .symbol; Index('VIX', 'CBOE') also carries .symbol='VIX'.
    """
    return getattr(contract, "symbol", "") or getattr(contract, "localSymbol", "")


def _fake_hist(bars_by_symbol: dict[str, list]):
    """Build a reqHistoricalData(contract, **kw) that returns canned bars per symbol.

    A symbol mapped to an Exception instance (or class) raises when fetched
    (drives the fail-soft tests). A symbol absent from the mapping returns [].
    """
    def req(contract, **kwargs):
        key = _contract_key(contract)
        val = bars_by_symbol.get(key, [])
        if isinstance(val, BaseException):
            raise val
        if isinstance(val, type) and issubclass(val, BaseException):
            raise val("boom")
        return val
    return req


# Two daily bars (prev, latest): +1% move on a 100→101 close.
_TWO_BARS_UP = [_bar(100.0), _bar(101.0)]


# ---------------------------------------------------------------------------
# Tests: fetch_account_pnl (reqPnL seam)
# ---------------------------------------------------------------------------

def _pnl_obj(daily, realized, unrealized, account="U1"):
    return SimpleNamespace(account=account, dailyPnL=daily, realizedPnL=realized,
                           unrealizedPnL=unrealized)


def _no_resubscribe(*_a):
    raise AssertionError("reqPnL must not be called when a warm subscription exists")


def test_fetch_account_pnl_reads_warm_subscription():
    # production path: the daemon already subscribed, so ib.pnl() holds the settled
    # object; we must NOT re-call reqPnL (ib_async raises on re-subscribe).
    ib = SimpleNamespace(pnl=lambda *a: [_pnl_obj(-3637.4, 265.4, -9098.9)],
                         reqPnL=_no_resubscribe)
    out = fetch_account_pnl(ib, "U1")
    assert out == {"daily": pytest.approx(-3637.4),
                   "realized": pytest.approx(265.4),
                   "unrealized": pytest.approx(-9098.9)}


def test_fetch_account_pnl_subscribes_when_cold():
    # no warm subscription -> reqPnL subscribes and returns the object
    ib = SimpleNamespace(pnl=lambda *a: [], reqPnL=lambda acct: _pnl_obj(-1.0, 2.0, 3.0))
    out = fetch_account_pnl(ib, "U1")
    assert out == {"daily": pytest.approx(-1.0),
                   "realized": pytest.approx(2.0),
                   "unrealized": pytest.approx(3.0)}


def test_fetch_account_pnl_maps_nan_and_inf_to_none():
    ib = SimpleNamespace(pnl=lambda *a: [_pnl_obj(float("nan"), float("inf"), -10.0)],
                         reqPnL=_no_resubscribe)
    out = fetch_account_pnl(ib, "U1")
    assert out["daily"] is None and out["realized"] is None
    assert out["unrealized"] == pytest.approx(-10.0)


def test_fetch_account_pnl_maps_unset_sentinel_to_none():
    """IBKR sends the UNSET sentinel (~1.79e308, which IS finite) for a reqPnL
    field before it settles — it must map to None, not a phantom 309-digit P&L."""
    ib = SimpleNamespace(pnl=lambda *a: [_pnl_obj(_SENTINEL, -_SENTINEL, -10.0)],
                         reqPnL=_no_resubscribe)
    out = fetch_account_pnl(ib, "U1")
    assert out["daily"] is None and out["realized"] is None
    assert out["unrealized"] == pytest.approx(-10.0)


def test_fetch_account_pnl_is_all_none_when_reqpnl_raises():
    def boom(acct):
        raise RuntimeError("no subscription")
    ib = SimpleNamespace(pnl=lambda *a: [], reqPnL=boom)
    assert fetch_account_pnl(ib, "U1") == {"daily": None, "realized": None, "unrealized": None}


def test_fetch_account_pnl_is_all_none_when_ib_lacks_pnl_api():
    ib = SimpleNamespace()  # no pnl / reqPnL attributes
    assert fetch_account_pnl(ib, "U1") == {"daily": None, "realized": None, "unrealized": None}


# ---------------------------------------------------------------------------
# Tests: collect_account_view carries the pnl dict
# ---------------------------------------------------------------------------

def test_collect_account_view_includes_pnl_from_reqpnl():
    account_values = [
        _account_value("NetLiquidation", "250000"),
        _account_value("ExcessLiquidity", "125000"),
        _account_value("GrossPositionValue", "200000"),
    ]
    ib = SimpleNamespace(
        accountValues=lambda: account_values,
        portfolio=lambda: [],
        fills=lambda: [],
        managedAccounts=lambda: ["U1"],
        pnl=lambda *a: [SimpleNamespace(account="U1", dailyPnL=-1000.0,
                                        realizedPnL=50.0, unrealizedPnL=-2000.0)],
    )
    view = collect_account_view(ib, RulesConfig(), _TODAY)
    assert view["pnl"] == {"daily": pytest.approx(-1000.0),
                           "realized": pytest.approx(50.0),
                           "unrealized": pytest.approx(-2000.0)}
    assert view["nav"] == pytest.approx(250000.0)  # existing keys intact


def test_collect_account_view_pnl_all_none_without_reqpnl():
    """Backward-compat: a fake ib lacking reqPnL/managedAccounts still works."""
    view = collect_account_view(_fake_ib(), RulesConfig(), _TODAY)
    assert view["pnl"] == {"daily": None, "realized": None, "unrealized": None}


# ---------------------------------------------------------------------------
# Tests: basic shape
# ---------------------------------------------------------------------------

class TestCollectDayDataShape:
    def test_returns_all_required_keys(self):
        ib = _fake_ib()
        config = RulesConfig()
        result = collect_day_data(ib, config, _TODAY)

        required_keys = {
            "date", "nav", "margin_cushion", "gross_leverage",
            "realized_pnl_today", "fills", "positions", "trips",
        }
        assert required_keys.issubset(result.keys())

    def test_date_is_iso_date_string(self):
        ib = _fake_ib()
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["date"] == "2026-06-19"

    def test_nav_matches_account(self):
        ib = _fake_ib(nav=250_000.0)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["nav"] == pytest.approx(250_000.0)

    def test_margin_cushion_derived_correctly(self):
        ib = _fake_ib(nav=400_000.0)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        # ExcessLiquidity = nav * 0.5 → cushion = 0.5
        assert result["margin_cushion"] == pytest.approx(0.5)

    def test_gross_leverage_derived_correctly(self):
        ib = _fake_ib(nav=400_000.0)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        # GrossPositionValue = nav * 0.8 → leverage = 0.8
        assert result["gross_leverage"] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Tests: realized P&L sentinel filtering
# ---------------------------------------------------------------------------

class TestRealizedPnl:
    def test_sums_sane_fills_only(self):
        fills = [
            _fill("STK", realized_pnl=250.0, order_id=1, symbol="AAPL"),
            _fill("FUT", realized_pnl=-100.0, order_id=2, symbol="MNQ"),
        ]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["realized_pnl_today"] == pytest.approx(150.0)

    def test_excludes_sentinel_value(self):
        """1.79e308 is the IBKR unset sentinel — must NOT be included in the sum."""
        fills = [
            _fill("STK", realized_pnl=500.0, order_id=1, symbol="AAPL"),
            _fill("FUT", realized_pnl=_SENTINEL, order_id=2, symbol="MNQ"),
        ]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["realized_pnl_today"] == pytest.approx(500.0)

    def test_excludes_negative_sentinel(self):
        """Negative sentinel (-1.79e308) must also be filtered."""
        fills = [
            _fill("STK", realized_pnl=300.0, order_id=1, symbol="AAPL"),
            _fill("FUT", realized_pnl=-_SENTINEL, order_id=2, symbol="MNQ"),
        ]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["realized_pnl_today"] == pytest.approx(300.0)

    def test_zero_pnl_with_no_fills(self):
        ib = _fake_ib(fills=[])
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["realized_pnl_today"] == pytest.approx(0.0)

    def test_excludes_fills_from_prior_day(self):
        """Fills with time != today must not contribute to realized_pnl_today."""
        yesterday = dt.datetime(2026, 6, 18, 11, 0)  # prior day
        fills = [
            _fill("STK", realized_pnl=1000.0, order_id=1, symbol="AAPL", time=yesterday),
            _fill("STK", realized_pnl=50.0, order_id=2, symbol="NVDA"),  # today
        ]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["realized_pnl_today"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Tests: fills list shape
# ---------------------------------------------------------------------------

class TestFillsList:
    def test_fills_empty_when_no_fills(self):
        ib = _fake_ib(fills=[])
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["fills"] == []

    def test_fills_have_required_fields(self):
        fills = [_fill("STK", realized_pnl=100.0, order_id=1, symbol="AAPL")]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)

        assert len(result["fills"]) == 1
        f = result["fills"][0]
        required = {"symbol", "sec_type", "side", "shares", "price", "realized_pnl", "time"}
        assert required.issubset(f.keys())

    def test_fills_values_correct(self):
        fills = [_fill(
            "STK",
            realized_pnl=250.0,
            order_id=1,
            symbol="NVDA",
            side="BOT",
            avg_price=900.0,
            shares=5.0,
        )]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)

        f = result["fills"][0]
        assert f["symbol"] == "NVDA"
        assert f["sec_type"] == "STK"
        assert f["side"] == "BOT"
        assert f["shares"] == pytest.approx(5.0)
        assert f["price"] == pytest.approx(900.0)
        assert f["realized_pnl"] == pytest.approx(250.0)

    def test_fills_time_is_string(self):
        """time must be stringified for JSON-serialization."""
        fills = [_fill("STK", realized_pnl=100.0, order_id=1)]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert isinstance(result["fills"][0]["time"], str)

    def test_excludes_prior_day_fills(self):
        yesterday = dt.datetime(2026, 6, 18, 11, 0)
        fills = [
            _fill("STK", realized_pnl=100.0, order_id=1, time=yesterday),
            _fill("FUT", realized_pnl=50.0, order_id=2),  # today
        ]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert len(result["fills"]) == 1
        assert result["fills"][0]["sec_type"] == "FUT"


# ---------------------------------------------------------------------------
# Tests: positions list shape
# ---------------------------------------------------------------------------

class TestPositionsList:
    def test_positions_empty_when_no_portfolio(self):
        ib = _fake_ib(portfolio=[])
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["positions"] == []

    def test_positions_have_required_fields(self):
        portfolio = [_portfolio_item("STK", position=100.0, market_value=15000.0)]
        ib = _fake_ib(portfolio=portfolio)
        result = collect_day_data(ib, RulesConfig(), _TODAY)

        assert len(result["positions"]) == 1
        p = result["positions"][0]
        required = {"symbol", "sec_type", "position", "market_value", "unrealized_pnl"}
        assert required.issubset(p.keys())

    def test_positions_values_correct(self):
        portfolio = [_portfolio_item(
            "STK",
            position=50.0,
            market_value=7500.0,
            unrealized_pnl=-200.0,
            symbol="TSLA",
        )]
        ib = _fake_ib(portfolio=portfolio)
        result = collect_day_data(ib, RulesConfig(), _TODAY)

        p = result["positions"][0]
        assert p["symbol"] == "TSLA"
        assert p["sec_type"] == "STK"
        assert p["position"] == pytest.approx(50.0)
        assert p["market_value"] == pytest.approx(7500.0)
        assert p["unrealized_pnl"] == pytest.approx(-200.0)


# ---------------------------------------------------------------------------
# Tests: trips list
# ---------------------------------------------------------------------------

class TestTrips:
    def test_trips_is_list(self):
        ib = _fake_ib()
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert isinstance(result["trips"], list)

    def test_trips_have_required_fields_when_nonempty(self):
        """Force a trip by making margin_cushion low."""
        # min_cushion default is 0.25; give ExcessLiquidity = 5% of NAV
        account_values = [
            _account_value("NetLiquidation", "250000"),
            _account_value("ExcessLiquidity", "12500"),   # 5% → trips cushion rule
            _account_value("GrossPositionValue", "0"),
        ]
        ib = SimpleNamespace(
            accountValues=lambda: account_values,
            portfolio=lambda: [],
            fills=lambda: [],
        )
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert len(result["trips"]) >= 1
        t = result["trips"][0]
        assert {"rule_id", "severity", "message"}.issubset(t.keys())

    def test_no_trips_on_clean_account(self):
        """Healthy account — no rules trip."""
        ib = _fake_ib(nav=250_000.0)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        # Healthy cushion=0.5, leverage=0.8, no futures, no big trades → no trips expected
        assert result["trips"] == []


# ---------------------------------------------------------------------------
# Tests: JSON-serializability
# ---------------------------------------------------------------------------

class TestJsonSerializable:
    def test_result_is_json_serializable(self):
        fills = [
            _fill("STK", realized_pnl=100.0, order_id=1, symbol="AAPL"),
            _fill("FUT", realized_pnl=_SENTINEL, order_id=2, symbol="MNQ"),
        ]
        portfolio = [_portfolio_item("STK", 100.0, 15000.0, -50.0, symbol="AAPL")]
        ib = _fake_ib(fills=fills, portfolio=portfolio)
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        # Should not raise
        serialized = json.dumps(result)
        assert isinstance(serialized, str)

    def test_no_datetime_objects_in_result(self):
        """No raw datetime objects — everything must be a JSON-safe primitive."""
        fills = [_fill("STK", 100.0, 1)]
        ib = _fake_ib(fills=fills)
        result = collect_day_data(ib, RulesConfig(), _TODAY)

        def _check_no_datetimes(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    _check_no_datetimes(v)
            elif isinstance(obj, list):
                for item in obj:
                    _check_no_datetimes(item)
            else:
                assert not isinstance(obj, dt.datetime), f"Found datetime: {obj!r}"

        _check_no_datetimes(result)


# ---------------------------------------------------------------------------
# Tests: market backdrop (indices + VIX) — collect_market_backdrop
# ---------------------------------------------------------------------------

# All four index ETFs + VIX, each with a known two-bar series.
_FULL_BACKDROP = {
    "SPY": [_bar(500.0), _bar(502.0)],   # +0.4%
    "QQQ": [_bar(440.0), _bar(439.12)],  # -0.2%
    "DIA": [_bar(390.0), _bar(391.95)],  # +0.5%
    "IWM": [_bar(200.0), _bar(199.0)],   # -0.5%
    "VIX": [_bar(18.0), _bar(23.0)],     # elevated (>20)
}


class TestMarketBackdropIndices:
    def test_change_pct_computed_from_prev_and_latest_close(self):
        ib = _fake_ib(req_historical_data=_fake_hist(_FULL_BACKDROP))
        backdrop = collect_market_backdrop(ib)

        spy = backdrop["indices"]["SPY"]
        assert spy["label"] == "S&P 500"
        assert spy["last"] == pytest.approx(502.0)
        assert spy["change_pct"] == pytest.approx((502.0 - 500.0) / 500.0 * 100)  # +0.4%

        qqq = backdrop["indices"]["QQQ"]
        assert qqq["label"] == "Nasdaq 100"
        assert qqq["change_pct"] == pytest.approx((439.12 - 440.0) / 440.0 * 100)  # -0.2%

    def test_all_four_indices_present_with_labels(self):
        ib = _fake_ib(req_historical_data=_fake_hist(_FULL_BACKDROP))
        backdrop = collect_market_backdrop(ib)
        labels = {sym: entry["label"] for sym, entry in backdrop["indices"].items()}
        assert labels == {
            "SPY": "S&P 500",
            "QQQ": "Nasdaq 100",
            "DIA": "Dow",
            "IWM": "Russell 2000",
        }

    def test_missing_or_empty_bars_set_entry_to_none(self):
        partial = {**_FULL_BACKDROP, "IWM": []}  # IWM returns no bars
        ib = _fake_ib(req_historical_data=_fake_hist(partial))
        backdrop = collect_market_backdrop(ib)
        assert backdrop["indices"]["IWM"] is None
        # Others still populated.
        assert backdrop["indices"]["SPY"]["last"] == pytest.approx(502.0)

    def test_single_bar_yields_none(self):
        """A one-bar series has no prior close to diff against → None, not a raise."""
        partial = {**_FULL_BACKDROP, "DIA": [_bar(390.0)]}
        ib = _fake_ib(req_historical_data=_fake_hist(partial))
        backdrop = collect_market_backdrop(ib)
        assert backdrop["indices"]["DIA"] is None


class TestMarketBackdropVix:
    def test_vix_elevated_above_threshold(self):
        ib = _fake_ib(req_historical_data=_fake_hist(_FULL_BACKDROP))
        backdrop = collect_market_backdrop(ib)
        vix = backdrop["vix"]
        assert vix["level"] == pytest.approx(23.0)
        assert vix["elevated"] is True
        assert vix["signal"] == "elevated fear — contrarian long"
        assert vix["change_pct"] == pytest.approx((23.0 - 18.0) / 18.0 * 100)

    def test_vix_calm_at_or_below_threshold(self):
        calm = {**_FULL_BACKDROP, "VIX": [_bar(15.0), _bar(18.0)]}
        ib = _fake_ib(req_historical_data=_fake_hist(calm))
        backdrop = collect_market_backdrop(ib)
        vix = backdrop["vix"]
        assert vix["level"] == pytest.approx(18.0)
        assert vix["elevated"] is False
        assert vix["signal"] == "calm"

    def test_vix_exactly_at_threshold_is_not_elevated(self):
        """Threshold is strict >: a level == VIX_ELEVATED_THRESHOLD is calm."""
        at = {**_FULL_BACKDROP, "VIX": [_bar(19.0), _bar(VIX_ELEVATED_THRESHOLD)]}
        ib = _fake_ib(req_historical_data=_fake_hist(at))
        backdrop = collect_market_backdrop(ib)
        assert backdrop["vix"]["elevated"] is False
        assert backdrop["vix"]["signal"] == "calm"

    def test_vix_none_when_unavailable(self):
        no_vix = {k: v for k, v in _FULL_BACKDROP.items() if k != "VIX"}
        ib = _fake_ib(req_historical_data=_fake_hist(no_vix))
        backdrop = collect_market_backdrop(ib)
        assert backdrop["vix"] is None
        # Indices unaffected.
        assert backdrop["indices"]["SPY"]["last"] == pytest.approx(502.0)

    def test_threshold_constant_is_twenty(self):
        assert VIX_ELEVATED_THRESHOLD == pytest.approx(20.0)


class TestMarketBackdropFailSoft:
    def test_one_symbol_raising_does_not_sink_the_rest(self):
        """A reqHistoricalData that raises for SPY → that entry None, others survive."""
        bars = {**_FULL_BACKDROP, "SPY": RuntimeError("TWS disconnected")}
        ib = _fake_ib(req_historical_data=_fake_hist(bars))
        backdrop = collect_market_backdrop(ib)  # must not raise
        assert backdrop["indices"]["SPY"] is None
        assert backdrop["indices"]["QQQ"]["change_pct"] == pytest.approx(-0.2, abs=1e-6)
        assert backdrop["vix"]["elevated"] is True

    def test_vix_raising_yields_none_indices_survive(self):
        bars = {**_FULL_BACKDROP, "VIX": RuntimeError("no entitlement")}
        ib = _fake_ib(req_historical_data=_fake_hist(bars))
        backdrop = collect_market_backdrop(ib)
        assert backdrop["vix"] is None
        assert backdrop["indices"]["DIA"]["change_pct"] == pytest.approx(0.5, abs=1e-6)

    def test_ib_without_req_historical_data_does_not_raise(self):
        """A fake IB lacking reqHistoricalData entirely → all-None backdrop, no raise."""
        ib = _fake_ib()  # no reqHistoricalData attribute
        backdrop = collect_market_backdrop(ib)
        assert backdrop["vix"] is None
        assert all(v is None for v in backdrop["indices"].values())

    def test_reqhistoricaldata_raising_globally_is_contained(self):
        def boom(contract, **kwargs):
            raise RuntimeError("hard down")

        ib = _fake_ib(req_historical_data=boom)
        backdrop = collect_market_backdrop(ib)  # must not raise
        assert backdrop["vix"] is None
        assert all(v is None for v in backdrop["indices"].values())


# ---------------------------------------------------------------------------
# Tests: collect_day_data wiring (backdrop keys + existing keys preserved)
# ---------------------------------------------------------------------------

class TestCollectDayDataBackdropWiring:
    def test_indices_and_vix_keys_added(self):
        ib = _fake_ib(req_historical_data=_fake_hist(_FULL_BACKDROP))
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert "indices" in result
        assert "vix" in result
        assert result["indices"]["SPY"]["last"] == pytest.approx(502.0)
        assert result["vix"]["elevated"] is True

    def test_existing_keys_still_present_alongside_backdrop(self):
        ib = _fake_ib(req_historical_data=_fake_hist(_FULL_BACKDROP))
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        required_keys = {
            "date", "nav", "margin_cushion", "gross_leverage",
            "realized_pnl_today", "fills", "positions", "trips",
            "indices", "vix",
        }
        assert required_keys.issubset(result.keys())

    def test_collector_does_not_raise_without_market_data(self):
        """The original fake (no reqHistoricalData) must still drive collect_day_data."""
        ib = _fake_ib()
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        assert result["vix"] is None
        assert all(v is None for v in result["indices"].values())

    def test_result_with_backdrop_is_json_serializable(self):
        ib = _fake_ib(req_historical_data=_fake_hist(_FULL_BACKDROP))
        result = collect_day_data(ib, RulesConfig(), _TODAY)
        serialized = json.dumps(result)  # must not raise
        assert isinstance(serialized, str)

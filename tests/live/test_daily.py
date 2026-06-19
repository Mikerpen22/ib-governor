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
from governor.live.daily import collect_day_data

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
) -> SimpleNamespace:
    account_values = [
        _account_value("NetLiquidation", str(nav)),
        _account_value("ExcessLiquidity", str(nav * 0.5)),
        _account_value("GrossPositionValue", str(nav * 0.8)),
    ]
    return SimpleNamespace(
        accountValues=lambda: account_values,
        portfolio=lambda: portfolio or [],
        fills=lambda: fills or [],
    )


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

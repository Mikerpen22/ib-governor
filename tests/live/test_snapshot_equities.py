"""Tests for the equity_weights() helper, equity_adds_at_loss() helper, and
the extended build_snapshot() in Tasks 8 and 9."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from governor.config import LiveConfig
from governor.live.snapshot import equity_weights, equity_adds_at_loss, build_snapshot

ET = ZoneInfo("America/New_York")


def _pi(sec_type, symbol, market_value, unrealized=0.0):
    from types import SimpleNamespace
    return SimpleNamespace(
        contract=SimpleNamespace(secType=sec_type, symbol=symbol, localSymbol=symbol),
        position=1.0,
        marketPrice=0.0,
        marketValue=market_value,
        unrealizedPNL=unrealized,
    )


def test_equity_weights_by_name_and_sector():
    items = [_pi("STK", "NVDA", 74_000.0), _pi("STK", "AMD", 37_000.0), _pi("FUT", "MNQ", 999.0)]
    sector = {"NVDA": "Technology", "AMD": "Technology"}
    name_w, sector_w = equity_weights(items, nav=250_000.0, sector_by_symbol=sector)
    assert name_w["NVDA"] == pytest.approx(74_000 / 250_000)
    assert sector_w["Technology"] == pytest.approx((74_000 + 37_000) / 250_000)


def test_equity_weights_unknown_sector_bucketed():
    items = [_pi("STK", "XYZ", 50_000.0)]
    name_w, sector_w = equity_weights(items, nav=250_000.0, sector_by_symbol={})
    assert "unknown" in sector_w        # missing sector -> 'unknown' bucket (not dropped)


def test_equity_weights_excludes_non_stk():
    """FUT and OPT should not appear in name or sector weights."""
    items = [
        _pi("STK", "AAPL", 10_000.0),
        _pi("FUT", "MNQ", 50_000.0),
        _pi("OPT", "NVDA", 5_000.0),
    ]
    name_w, sector_w = equity_weights(items, nav=100_000.0, sector_by_symbol={"AAPL": "Technology"})
    assert list(name_w.keys()) == ["AAPL"]
    assert "MNQ" not in name_w and "NVDA" not in name_w


def test_equity_weights_zero_nav_returns_empty():
    items = [_pi("STK", "NVDA", 10_000.0)]
    name_w, sector_w = equity_weights(items, nav=0.0, sector_by_symbol={})
    assert name_w == {} and sector_w == {}


def test_build_snapshot_accepts_new_kwargs(mk_account_value, mk_portfolio_item, mk_fill):
    """Existing Plan-2 callers can pass the new kwargs and get the derived fields."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    account_values = [
        mk_account_value("NetLiquidation", "250000"),
        mk_account_value("ExcessLiquidity", "200000"),
    ]
    # portfolio_item from conftest doesn't have symbol/marketValue — use _pi for STK
    from types import SimpleNamespace
    portfolio = [
        _pi("STK", "NVDA", 74_000.0, unrealized=1000.0),
        _pi("STK", "AMD", 37_000.0, unrealized=-500.0),
    ]
    fills = [mk_fill("FUT", 0.0, order_id=1)]
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")

    snap = build_snapshot(
        now=now,
        account_values=account_values,
        portfolio_items=portfolio,
        fills=fills,
        cfg=cfg,
        sector_by_symbol={"NVDA": "Technology", "AMD": "Technology"},
        name_trade_counts_week={"NVDA": 1},
        hwm_drawdown_pct=0.05,
        equity_adds_at_loss_today=("AMD",),
    )

    assert snap.name_weights["NVDA"] == pytest.approx(74_000 / 250_000)
    assert snap.sector_weights["Technology"] == pytest.approx((74_000 + 37_000) / 250_000)
    assert snap.name_trade_counts_week == {"NVDA": 1}
    assert snap.drawdown_pct == pytest.approx(0.05)
    assert snap.equity_adds_at_loss_today == ("AMD",)


def test_build_snapshot_existing_callers_unchanged(mk_account_value, mk_portfolio_item, mk_fill):
    """Existing Plan-2 tests: calling build_snapshot with only the original 5 kwargs still works."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    account_values = [
        mk_account_value("NetLiquidation", "250000"),
        mk_account_value("ExcessLiquidity", "200000"),
        mk_account_value("GrossPositionValue", "400000"),
    ]
    portfolio = [mk_portfolio_item("FUT", position=6, market_price=21000.0, multiplier=2)]
    fills = [mk_fill("FUT", 11700.0, order_id=1)]
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")

    snap = build_snapshot(now=now, account_values=account_values,
                          portfolio_items=portfolio, fills=fills, cfg=cfg)

    # existing fields still correct
    assert snap.nav == pytest.approx(250_000.0)
    assert snap.futures_realized_pnl_today == pytest.approx(11700.0)
    assert snap.futures_trade_count_today == 1
    # new fields default to safe empty values
    assert snap.drawdown_pct == 0.0
    assert snap.sector_weights == {}
    assert snap.name_weights == {}
    assert snap.name_trade_counts_week == {}
    assert snap.equity_adds_at_loss_today == ()


# ── equity_adds_at_loss tests (Task 9 pure helper) ────────────────────────────

def _fill_stk(symbol, side="BOT", shares=100):
    from types import SimpleNamespace
    return SimpleNamespace(
        contract=SimpleNamespace(secType="STK", symbol=symbol, localSymbol=symbol),
        execution=SimpleNamespace(side=side, shares=shares),
        commissionReport=SimpleNamespace(realizedPNL=0.0),
    )


def test_equity_adds_at_loss_bought_and_losing():
    """Name bought today AND at unrealized loss -> appears in result."""
    fills = [_fill_stk("AMD")]
    portfolio = [_pi("STK", "AMD", 10_000.0, unrealized=-300.0)]
    result = equity_adds_at_loss(fills, portfolio)
    assert "AMD" in result


def test_equity_adds_at_loss_bought_but_winning():
    """Name bought today but in profit -> NOT in result."""
    fills = [_fill_stk("NVDA")]
    portfolio = [_pi("STK", "NVDA", 15_000.0, unrealized=500.0)]
    result = equity_adds_at_loss(fills, portfolio)
    assert result == ()


def test_equity_adds_at_loss_sold_not_counted():
    """A sell fill (SLD) does not qualify even if the position is at a loss."""
    fills = [_fill_stk("SHOP", side="SLD")]
    portfolio = [_pi("STK", "SHOP", 8_000.0, unrealized=-200.0)]
    result = equity_adds_at_loss(fills, portfolio)
    assert result == ()


def test_equity_adds_at_loss_fut_fills_ignored():
    """FUT fills do not contribute to equity_adds_at_loss."""
    from types import SimpleNamespace
    fut_fill = SimpleNamespace(
        contract=SimpleNamespace(secType="FUT", symbol="MNQ", localSymbol="MNQU6"),
        execution=SimpleNamespace(side="BOT", shares=1),
        commissionReport=SimpleNamespace(realizedPNL=0.0),
    )
    portfolio = [_pi("FUT", "MNQ", 5_000.0, unrealized=-100.0)]
    result = equity_adds_at_loss([fut_fill], portfolio)
    assert result == ()


def test_equity_adds_at_loss_multiple_names_sorted():
    """Multiple qualifying names are returned sorted (deterministic ordering)."""
    fills = [_fill_stk("SHOP"), _fill_stk("AMD")]
    portfolio = [
        _pi("STK", "SHOP", 5_000.0, unrealized=-100.0),
        _pi("STK", "AMD", 10_000.0, unrealized=-50.0),
    ]
    result = equity_adds_at_loss(fills, portfolio)
    assert result == ("AMD", "SHOP")


def test_equity_adds_at_loss_no_fills_returns_empty():
    portfolio = [_pi("STK", "NVDA", 10_000.0, unrealized=-500.0)]
    result = equity_adds_at_loss([], portfolio)
    assert result == ()


def test_equity_adds_at_loss_zero_pnl_not_a_loss():
    """Exactly zero unrealized P&L is not a loss."""
    fills = [_fill_stk("AAPL")]
    portfolio = [_pi("STK", "AAPL", 10_000.0, unrealized=0.0)]
    result = equity_adds_at_loss(fills, portfolio)
    assert result == ()


def test_equity_adds_at_loss_side_none_ignored():
    """Contract pin (review L1): a STK fill with side=None is NOT treated as a buy.
    Real ib_async Executions always populate side; this locks the post-refactor
    narrowing so a future change can't silently resurrect the old None-is-buy path."""
    fills = [_fill_stk("AMD", side=None)]
    portfolio = [_pi("STK", "AMD", 10_000.0, unrealized=-300.0)]
    assert equity_adds_at_loss(fills, portfolio) == ()

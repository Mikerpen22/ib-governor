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


def _fut(symbol, position, market_price, multiplier=2, unrealized=0.0):
    """A FUT portfolio item with the fields build_snapshot reads for signed
    notional + mark-to-market open P&L."""
    from types import SimpleNamespace
    return SimpleNamespace(
        contract=SimpleNamespace(secType="FUT", symbol=symbol, localSymbol=symbol,
                                 multiplier=str(multiplier)),
        position=position,
        marketPrice=market_price,
        marketValue=position * multiplier * market_price,
        unrealizedPNL=unrealized,
    )


def test_equity_weights_by_name_and_sector():
    items = [_pi("STK", "NVDA", 74_000.0), _pi("STK", "AMD", 37_000.0), _pi("FUT", "MNQ", 999.0)]
    sector = {"NVDA": "Technology", "AMD": "Technology"}
    name_w, sector_w, name_signed = equity_weights(items, nav=250_000.0, sector_by_symbol=sector)
    assert name_w["NVDA"] == pytest.approx(74_000 / 250_000)
    assert sector_w["Technology"] == pytest.approx((74_000 + 37_000) / 250_000)


# ── [H1] name_exposure_signed (signed per-name exposure) ──────────────────────

def test_equity_weights_signed_positive_for_long():
    """A long STK position yields a POSITIVE signed exposure; magnitude matches name_w."""
    items = [_pi("STK", "NVDA", 74_000.0)]
    name_w, _sector_w, name_signed = equity_weights(items, nav=250_000.0, sector_by_symbol={})
    assert name_signed["NVDA"] == pytest.approx(74_000 / 250_000)
    assert name_w["NVDA"] == pytest.approx(abs(name_signed["NVDA"]))


def test_equity_weights_signed_negative_for_short():
    """A short STK position (negative marketValue) yields a NEGATIVE signed exposure,
    while name_weights stays a positive magnitude."""
    items = [_pi("STK", "SHOP", -30_000.0)]  # short position, negative market value
    name_w, _sector_w, name_signed = equity_weights(items, nav=250_000.0, sector_by_symbol={})
    assert name_signed["SHOP"] == pytest.approx(-30_000 / 250_000)
    assert name_w["SHOP"] == pytest.approx(30_000 / 250_000)  # magnitude only


def test_equity_weights_signed_finite_guards_nan():
    """A nan marketValue maps to 0.0 in the signed map too (no nan leak)."""
    items = [_pi("STK", "NVDA", 50_000.0), _pi("STK", "AMD", float("nan"))]
    _name_w, _sector_w, name_signed = equity_weights(items, nav=250_000.0, sector_by_symbol={})
    assert name_signed["NVDA"] == pytest.approx(50_000 / 250_000)
    assert name_signed["AMD"] == pytest.approx(0.0)
    assert all(v == v for v in name_signed.values())  # no nan


def test_build_snapshot_sets_name_exposure_signed(mk_account_value):
    """build_snapshot threads name_exposure_signed onto the StateSnapshot (signed)."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [
        _pi("STK", "NVDA", 74_000.0),
        _pi("STK", "SHOP", -30_000.0),  # short
    ]
    snap = build_snapshot(
        now=now, account_values=[mk_account_value("NetLiquidation", "250000")],
        portfolio_items=portfolio, fills=[], cfg=cfg, sector_by_symbol={},
    )
    assert snap.name_exposure_signed["NVDA"] == pytest.approx(74_000 / 250_000)
    assert snap.name_exposure_signed["SHOP"] == pytest.approx(-30_000 / 250_000)
    # name_weights remains magnitude
    assert snap.name_weights["SHOP"] == pytest.approx(30_000 / 250_000)


def test_equity_weights_unknown_sector_bucketed():
    items = [_pi("STK", "XYZ", 50_000.0)]
    name_w, sector_w, _name_signed = equity_weights(items, nav=250_000.0, sector_by_symbol={})
    assert "unknown" in sector_w        # missing sector -> 'unknown' bucket (not dropped)


def test_equity_weights_excludes_non_stk():
    """FUT and OPT should not appear in name or sector weights."""
    items = [
        _pi("STK", "AAPL", 10_000.0),
        _pi("FUT", "MNQ", 50_000.0),
        _pi("OPT", "NVDA", 5_000.0),
    ]
    name_w, sector_w, name_signed = equity_weights(items, nav=100_000.0, sector_by_symbol={"AAPL": "Technology"})
    assert list(name_w.keys()) == ["AAPL"]
    assert "MNQ" not in name_w and "NVDA" not in name_w
    assert list(name_signed.keys()) == ["AAPL"]


def test_equity_weights_zero_nav_returns_empty():
    items = [_pi("STK", "NVDA", 10_000.0)]
    name_w, sector_w, name_signed = equity_weights(items, nav=0.0, sector_by_symbol={})
    assert name_w == {} and sector_w == {} and name_signed == {}


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


# ── finite-guard tests (data-layer correctness) ──────────────────────────────

def test_equity_weights_finite_guards_nan_market_value():
    """A nan marketValue must not silently drop the name (or NaN-poison its sector)."""
    items = [
        _pi("STK", "NVDA", 50_000.0),
        _pi("STK", "AMD", float("nan")),  # nan marketValue
    ]
    name_w, sector_w, _name_signed = equity_weights(items, nav=250_000.0, sector_by_symbol={})
    # NVDA weight is finite and correct; AMD maps to 0.0 (not nan, not dropped silently)
    assert name_w["NVDA"] == pytest.approx(50_000 / 250_000)
    assert name_w["AMD"] == pytest.approx(0.0)
    assert all(v == v for v in name_w.values())  # no nan (nan != nan)
    assert all(v == v for v in sector_w.values())


def test_equity_adds_at_loss_finite_guards_nan_unrealized():
    """A nan unrealizedPNL must not hide an at-loss add: nan < 0 is False, so the
    old `or 0.0` path would silently treat a poisoned value as 'not a loss'.
    _finite_float maps nan -> 0.0 (also not a loss), but the guard is the point —
    this locks that a nan can never sneak past as a real negative either."""
    fills = [_fill_stk("AMD")]
    portfolio = [_pi("STK", "AMD", 10_000.0, unrealized=float("nan"))]
    # nan -> 0.0 -> not < 0 -> not at loss (deterministic, never a phantom)
    assert equity_adds_at_loss(fills, portfolio) == ()


# ── futures_notional_signed + futures_unrealized_pnl_today (build_snapshot) ───

def _account(mk_account_value):
    return [mk_account_value("NetLiquidation", "250000")]


def test_futures_notional_signed_positive_for_net_long(mk_account_value):
    """A net-LONG futures book yields a POSITIVE signed notional."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [_fut("MNQU6", position=3, market_price=21000.0, multiplier=2)]
    snap = build_snapshot(
        now=now, account_values=_account(mk_account_value),
        portfolio_items=portfolio, fills=[], cfg=cfg,
    )
    assert snap.futures_notional_signed == pytest.approx(3 * 2 * 21000.0)   # +126000
    assert snap.futures_notional_signed > 0
    # absolute notional unchanged
    assert snap.futures_notional == pytest.approx(3 * 2 * 21000.0)


def test_futures_notional_signed_negative_for_net_short(mk_account_value):
    """A net-SHORT futures book yields a NEGATIVE signed notional (abs notional stays +)."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [_fut("MNQU6", position=-3, market_price=21000.0, multiplier=2)]
    snap = build_snapshot(
        now=now, account_values=_account(mk_account_value),
        portfolio_items=portfolio, fills=[], cfg=cfg,
    )
    assert snap.futures_notional_signed == pytest.approx(-3 * 2 * 21000.0)  # -126000
    assert snap.futures_notional_signed < 0
    assert snap.futures_notional == pytest.approx(3 * 2 * 21000.0)          # abs stays positive


def test_futures_notional_signed_nets_long_and_short(mk_account_value):
    """Mixed book: signed notional NETS (long − short); absolute notional SUMS magnitudes."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [
        _fut("MNQU6", position=5, market_price=21000.0, multiplier=2),    # +210000
        _fut("MESU6", position=-2, market_price=5000.0, multiplier=5),    # -50000
    ]
    snap = build_snapshot(
        now=now, account_values=_account(mk_account_value),
        portfolio_items=portfolio, fills=[], cfg=cfg,
    )
    assert snap.futures_notional_signed == pytest.approx(210000.0 - 50000.0)  # +160000 net
    assert snap.futures_notional == pytest.approx(210000.0 + 50000.0)          # 260000 abs


def test_futures_notional_signed_skips_nan_price(mk_account_value):
    """A nan marketPrice is finite-guarded out of the signed sum (not propagated)."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [
        _fut("MNQU6", position=3, market_price=21000.0, multiplier=2),       # +126000
        _fut("MESU6", position=2, market_price=float("nan"), multiplier=5),  # skipped
    ]
    snap = build_snapshot(
        now=now, account_values=_account(mk_account_value),
        portfolio_items=portfolio, fills=[], cfg=cfg,
    )
    assert snap.futures_notional_signed == pytest.approx(126000.0)
    assert snap.futures_notional_signed == snap.futures_notional_signed  # not nan


def test_futures_unrealized_pnl_today_sums_fut(mk_account_value):
    """futures_unrealized_pnl_today sums unrealizedPNL over FUT items (signed)."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [
        _fut("MNQU6", position=3, market_price=21000.0, unrealized=1200.0),
        _fut("MESU6", position=-2, market_price=5000.0, unrealized=-300.0),
    ]
    snap = build_snapshot(
        now=now, account_values=_account(mk_account_value),
        portfolio_items=portfolio, fills=[], cfg=cfg,
    )
    assert snap.futures_unrealized_pnl_today == pytest.approx(1200.0 - 300.0)  # 900


def test_futures_unrealized_pnl_today_ignores_sentinel(mk_account_value):
    """An UNSET-sentinel unrealizedPNL (≈1.79e308) must NOT be summed."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [
        _fut("MNQU6", position=3, market_price=21000.0, unrealized=800.0),
        _fut("MESU6", position=1, market_price=5000.0, unrealized=1.7976931348623157e308),
    ]
    snap = build_snapshot(
        now=now, account_values=_account(mk_account_value),
        portfolio_items=portfolio, fills=[], cfg=cfg,
    )
    assert snap.futures_unrealized_pnl_today == pytest.approx(800.0)  # sentinel excluded
    assert abs(snap.futures_unrealized_pnl_today) < 1e12


def test_futures_unrealized_pnl_today_excludes_non_fut(mk_account_value):
    """STK unrealizedPNL must NOT contribute to futures_unrealized_pnl_today."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [
        _fut("MNQU6", position=3, market_price=21000.0, unrealized=500.0),
        _pi("STK", "NVDA", 50_000.0, unrealized=9999.0),  # must be ignored
    ]
    snap = build_snapshot(
        now=now, account_values=_account(mk_account_value),
        portfolio_items=portfolio, fills=[], cfg=cfg,
    )
    assert snap.futures_unrealized_pnl_today == pytest.approx(500.0)


def test_new_signed_fields_default_to_zero_when_no_futures(mk_account_value):
    """An all-equity / empty book leaves the new fields at 0.0 (benign default)."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")
    portfolio = [_pi("STK", "NVDA", 50_000.0, unrealized=100.0)]
    snap = build_snapshot(
        now=now, account_values=_account(mk_account_value),
        portfolio_items=portfolio, fills=[], cfg=cfg,
    )
    assert snap.futures_notional_signed == 0.0
    assert snap.futures_unrealized_pnl_today == 0.0

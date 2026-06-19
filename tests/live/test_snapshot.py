import math

import pytest

from governor.live.snapshot import account_metrics


def test_account_metrics_basic(mk_account_value):
    values = [
        mk_account_value("NetLiquidation", "365542.54"),
        mk_account_value("ExcessLiquidity", "199561.47"),
        mk_account_value("GrossPositionValue", "548000.00"),
    ]
    nav, cushion, gross_leverage = account_metrics(values)
    assert nav == pytest.approx(365542.54)
    assert cushion == pytest.approx(199561.47 / 365542.54)
    assert gross_leverage == pytest.approx(548000.0 / 365542.54)


def test_account_metrics_zero_nav_is_safe(mk_account_value):
    nav, cushion, gross_leverage = account_metrics(
        [mk_account_value("NetLiquidation", "0")]
    )
    assert nav == 0.0
    assert cushion == 0.0 and gross_leverage == 0.0


def test_account_metrics_ignores_non_usd_and_missing(mk_account_value):
    values = [
        mk_account_value("NetLiquidation", "365000", currency="USD"),
        mk_account_value("NetLiquidation", "999999", currency="BASE"),  # must be ignored
    ]
    nav, cushion, gross = account_metrics(values)
    assert nav == pytest.approx(365000.0)   # USD wins, BASE ignored
    assert cushion == 0.0 and gross == 0.0  # ExcessLiquidity/GrossPositionValue missing -> 0


# append to tests/live/test_snapshot.py
from governor.live.snapshot import futures_exposure


def test_futures_exposure_sums_notional(mk_portfolio_item):
    items = [
        mk_portfolio_item("FUT", position=6, market_price=21000.0, multiplier=2),  # MNQ-ish
        mk_portfolio_item("STK", position=100, market_price=150.0),                # ignored
    ]
    notional, contracts = futures_exposure(items, mnq_notional_usd=42000.0)
    assert notional == pytest.approx(6 * 2 * 21000.0)            # 252,000
    assert contracts == pytest.approx(252000.0 / 42000.0)        # 6.0 MNQ-equiv


def test_futures_exposure_absolute_value(mk_portfolio_item):
    items = [mk_portfolio_item("FUT", position=-3, market_price=21000.0, multiplier=2)]
    notional, contracts = futures_exposure(items, 42000.0)
    assert notional == pytest.approx(3 * 2 * 21000.0)            # short counts positive


def test_futures_exposure_empty(mk_portfolio_item):
    assert futures_exposure([], 42000.0) == (0.0, 0.0)


# append to tests/live/test_snapshot.py
from governor.live.snapshot import futures_activity


def test_futures_activity_derives_pnl_counts_and_churn(mk_fill):
    fills = [
        mk_fill("FUT", realized_pnl=500.0, order_id=1, local_symbol="MNQU6"),
        mk_fill("FUT", realized_pnl=-200.0, order_id=2, local_symbol="MNQU6"),
        mk_fill("FUT", realized_pnl=0.0, order_id=2, local_symbol="MNQU6"),   # same order -> 1 trade
        mk_fill("FUT", realized_pnl=-50.0, order_id=3, local_symbol="MESU6"),
        mk_fill("STK", realized_pnl=999.0, order_id=4, local_symbol="NVDA"),  # ignored
    ]
    pnl, trades, losers, counts = futures_activity(fills)
    assert pnl == pytest.approx(500.0 - 200.0 + 0.0 - 50.0)  # 250.0, STK excluded
    assert trades == 3            # distinct FUT orderIds: 1,2,3
    assert losers == 2            # fills with realizedPNL < 0: order 2's -200, order 3's -50
    assert counts == {"MNQU6": 3, "MESU6": 1}  # per-contract FUT fill counts


def test_futures_activity_empty(mk_fill):
    assert futures_activity([]) == (0.0, 0, 0, {})


# append to tests/live/test_snapshot.py
import datetime as dt
from zoneinfo import ZoneInfo

from governor.live.snapshot import minutes_to_close

ET = ZoneInfo("America/New_York")


def test_minutes_to_close_before_close():
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)   # 10 min before 16:00
    assert minutes_to_close(now, "16:00") == pytest.approx(10.0)


def test_minutes_to_close_after_close_is_none():
    now = dt.datetime(2026, 6, 17, 16, 5, tzinfo=ET)
    assert minutes_to_close(now, "16:00") is None


def test_minutes_to_close_naive_now_is_treated_as_et():
    now = dt.datetime(2026, 6, 17, 15, 0)               # naive
    assert minutes_to_close(now, "16:00") == pytest.approx(60.0)


# append to tests/live/test_snapshot.py
from governor.config import LiveConfig
from governor.live.snapshot import build_snapshot
from governor.model import StateSnapshot


def test_build_snapshot_composes_everything(mk_account_value, mk_portfolio_item, mk_fill):
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    account_values = [mk_account_value("NetLiquidation", "250000"),
                      mk_account_value("ExcessLiquidity", "200000"),
                      mk_account_value("GrossPositionValue", "400000")]
    portfolio = [mk_portfolio_item("FUT", position=6, market_price=21000.0, multiplier=2)]
    fills = [mk_fill("FUT", 11700.0, order_id=1)]
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")

    snap = build_snapshot(now=now, account_values=account_values,
                          portfolio_items=portfolio, fills=fills, cfg=cfg)

    assert isinstance(snap, StateSnapshot)
    assert snap.nav == pytest.approx(250000.0)
    assert snap.futures_realized_pnl_today == pytest.approx(11700.0)
    assert snap.futures_trade_count_today == 1
    assert snap.futures_notional == pytest.approx(6 * 2 * 21000.0)
    assert snap.futures_contracts_overnight == pytest.approx(252000.0 / 42000.0)
    assert snap.minutes_to_futures_close == pytest.approx(10.0)
    assert snap.ts == now.isoformat()
    assert snap.margin_cushion == pytest.approx(200000.0 / 250000.0)
    assert snap.gross_leverage == pytest.approx(400000.0 / 250000.0)


def test_build_snapshot_feeds_evaluate_end_to_end(mk_account_value, mk_portfolio_item, mk_fill):
    """The whole point: a live-shaped snapshot should trip the right rules via evaluate()."""
    from governor.config import RulesConfig
    from governor.rules.engine import evaluate
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    cfg = RulesConfig()
    snap = build_snapshot(
        now=now,
        account_values=[mk_account_value("NetLiquidation", "250000"),
                        mk_account_value("ExcessLiquidity", "200000")],
        portfolio_items=[mk_portfolio_item("FUT", 6, 21000.0, multiplier=2)],
        fills=[mk_fill("FUT", 11700.0, order_id=1)],
        cfg=cfg.live,
    )
    ids = {t.rule_id for t in evaluate(snap, cfg)}
    assert "futures.house_money_lockout" in ids   # realized win exceeds the house-money threshold
    assert "futures.overnight_notional" in ids     # 6 MNQ-equiv in close window


# ---------------------------------------------------------------------------
# Tests: build_snapshot mnq_notional_usd override
# ---------------------------------------------------------------------------

def test_build_snapshot_mnq_notional_override_used(mk_account_value, mk_portfolio_item, mk_fill):
    """Passing mnq_notional_usd=61000 overrides cfg and scales contracts_overnight."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    account_values = [mk_account_value("NetLiquidation", "250000")]
    # 6 FUT contracts × multiplier=2 × price=21000 → notional=252000
    portfolio = [mk_portfolio_item("FUT", position=6, market_price=21000.0, multiplier=2)]
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")

    snap = build_snapshot(
        now=now,
        account_values=account_values,
        portfolio_items=portfolio,
        fills=[],
        cfg=cfg,
        mnq_notional_usd=61000.0,
    )

    total_futures_notional = 6 * 2 * 21000.0  # 252000
    assert snap.futures_notional == pytest.approx(total_futures_notional)
    assert snap.futures_contracts_overnight == pytest.approx(total_futures_notional / 61000.0)


def test_build_snapshot_mnq_notional_none_falls_back_to_cfg(mk_account_value, mk_portfolio_item, mk_fill):
    """Without mnq_notional_usd param (default None) uses cfg.mnq_notional_usd — unchanged behavior."""
    now = dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET)
    account_values = [mk_account_value("NetLiquidation", "250000")]
    portfolio = [mk_portfolio_item("FUT", position=6, market_price=21000.0, multiplier=2)]
    cfg = LiveConfig(mnq_notional_usd=42000.0, session_close_et="16:00")

    snap = build_snapshot(
        now=now,
        account_values=account_values,
        portfolio_items=portfolio,
        fills=[],
        cfg=cfg,
        # mnq_notional_usd not passed → defaults to None → uses cfg.mnq_notional_usd=42000
    )

    total_futures_notional = 6 * 2 * 21000.0  # 252000
    assert snap.futures_notional == pytest.approx(total_futures_notional)
    assert snap.futures_contracts_overnight == pytest.approx(total_futures_notional / 42000.0)

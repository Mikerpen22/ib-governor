# tests/test_futures_rules.py
from governor.config import FuturesRules
from governor.model import ActionType, Severity
from governor.rules import futures

CFG = FuturesRules()


def test_house_money_fires_above_threshold(make_snapshot):
    s = make_snapshot(futures_realized_pnl_today=3001.0)
    trip = futures.house_money_lockout(s, CFG)
    assert trip is not None
    assert trip.severity is Severity.HARD
    assert trip.action is ActionType.LOCKOUT_FUTURES_48H
    assert trip.rule_id == "futures.house_money_lockout"


def test_house_money_silent_at_or_below_threshold(make_snapshot):
    s = make_snapshot(futures_realized_pnl_today=3000.0)
    assert futures.house_money_lockout(s, CFG) is None


def test_daily_loss_fires_on_dollar_loss(make_snapshot):
    s = make_snapshot(futures_realized_pnl_today=-1500.01)
    trip = futures.daily_loss_stop(s, CFG)
    assert trip is not None
    assert trip.action is ActionType.PLATFORM_OFF_TODAY


def test_daily_loss_fires_on_losing_streak(make_snapshot):
    s = make_snapshot(futures_realized_pnl_today=-200.0, futures_losing_trades_today=3)
    assert futures.daily_loss_stop(s, CFG) is not None


def test_daily_loss_silent_when_fine(make_snapshot):
    s = make_snapshot(futures_realized_pnl_today=-200.0, futures_losing_trades_today=2)
    assert futures.daily_loss_stop(s, CFG) is None


# ── [H2] daily_loss_stop includes UNREALIZED (open mark-to-market) loss ───────

def test_daily_loss_fires_on_large_unrealized_loss_zero_realized(make_snapshot):
    """Audit H2 (user decision): an open losing position with ZERO realized P&L
    can trip the daily stop. realized 0 + unrealized −1500.01 < −1500."""
    s = make_snapshot(
        futures_realized_pnl_today=0.0,
        futures_unrealized_pnl_today=-1500.01,
        futures_losing_trades_today=0,
    )
    trip = futures.daily_loss_stop(s, CFG)
    assert trip is not None
    assert trip.action is ActionType.PLATFORM_OFF_TODAY


def test_daily_loss_fires_on_combined_realized_plus_unrealized(make_snapshot):
    """Neither leg alone breaches, but realized + unrealized together do."""
    s = make_snapshot(
        futures_realized_pnl_today=-900.0,
        futures_unrealized_pnl_today=-700.0,  # combined −1600 < −1500
        futures_losing_trades_today=0,
    )
    assert futures.daily_loss_stop(s, CFG) is not None


def test_daily_loss_silent_when_open_gain_offsets_realized_loss(make_snapshot):
    """A realized loss offset by a larger open GAIN must NOT trip (combined > −1500)."""
    s = make_snapshot(
        futures_realized_pnl_today=-1400.0,
        futures_unrealized_pnl_today=+1000.0,  # combined −400
        futures_losing_trades_today=0,
    )
    assert futures.daily_loss_stop(s, CFG) is None


def test_daily_loss_message_mentions_open_pnl(make_snapshot):
    """The trip message reflects realized + open (mark-to-market) futures P&L."""
    s = make_snapshot(
        futures_realized_pnl_today=0.0,
        futures_unrealized_pnl_today=-2000.0,
    )
    trip = futures.daily_loss_stop(s, CFG)
    assert trip is not None
    msg = trip.message.lower()
    assert "open" in msg or "mark-to-market" in msg or "unrealized" in msg


def test_overtrading_warn_band(make_snapshot):
    s = make_snapshot(futures_trade_count_today=10)
    trip = futures.overtrading(s, CFG)
    assert trip is not None and trip.severity is Severity.WARN
    assert trip.action is ActionType.ALERT_ONLY


def test_overtrading_hard_band(make_snapshot):
    s = make_snapshot(futures_trade_count_today=20)
    trip = futures.overtrading(s, CFG)
    assert trip is not None and trip.severity is Severity.HARD
    assert trip.action is ActionType.PLATFORM_OFF_TODAY


def test_overtrading_silent_below_warn(make_snapshot):
    s = make_snapshot(futures_trade_count_today=9)
    assert futures.overtrading(s, CFG) is None


def test_overnight_fires_when_oversized_in_window(make_snapshot):
    s = make_snapshot(
        futures_contracts_overnight=6.0,
        minutes_to_futures_close=10.0,
        futures_notional=349_000.0,
    )
    trip = futures.overnight_notional(s, CFG)
    assert trip is not None and trip.action is ActionType.TRIM_FUTURES


def test_overnight_silent_outside_close_window(make_snapshot):
    s = make_snapshot(futures_contracts_overnight=6.0, minutes_to_futures_close=None)
    assert futures.overnight_notional(s, CFG) is None


def test_overnight_silent_when_within_cap(make_snapshot):
    s = make_snapshot(futures_contracts_overnight=2.0, minutes_to_futures_close=5.0)
    assert futures.overnight_notional(s, CFG) is None


def test_live_notional_fires_over_pct(make_snapshot):
    s = make_snapshot(futures_notional=200_000.0, nav=250_000.0)  # ~80%
    trip = futures.live_notional(s, CFG)
    assert trip is not None and trip.severity is Severity.WARN


def test_live_notional_silent_under_pct(make_snapshot):
    s = make_snapshot(futures_notional=100_000.0, nav=250_000.0)  # ~40%
    assert futures.live_notional(s, CFG) is None


def test_live_notional_silent_when_nav_nonpositive(make_snapshot):
    s = make_snapshot(futures_notional=100_000.0, nav=0.0)
    assert futures.live_notional(s, CFG) is None


def test_churn_fires_and_reports_worst_contract(make_snapshot):
    s = make_snapshot(contract_trade_counts_today={"MNQU6": 8, "MESU6": 2})
    trip = futures.same_contract_churn(s, CFG)
    assert trip is not None
    assert trip.context["contract"] == "MNQU6"
    assert trip.context["count"] == "8"


def test_churn_silent_below_threshold(make_snapshot):
    s = make_snapshot(contract_trade_counts_today={"MNQU6": 4})
    assert futures.same_contract_churn(s, CFG) is None


def test_daily_loss_silent_at_exact_dollar_threshold(make_snapshot):
    s = make_snapshot(futures_realized_pnl_today=-1500.0, futures_losing_trades_today=0)
    assert futures.daily_loss_stop(s, CFG) is None


def test_overnight_fires_at_exact_close_window(make_snapshot):
    s = make_snapshot(futures_contracts_overnight=6.0, minutes_to_futures_close=15.0,
                      futures_notional=349_000.0)
    assert futures.overnight_notional(s, CFG) is not None


def test_overnight_silent_just_outside_close_window(make_snapshot):
    s = make_snapshot(futures_contracts_overnight=6.0, minutes_to_futures_close=15.01,
                      futures_notional=349_000.0)
    assert futures.overnight_notional(s, CFG) is None


def test_overnight_handles_zero_nav(make_snapshot):
    s = make_snapshot(nav=0.0, futures_contracts_overnight=6.0,
                      minutes_to_futures_close=10.0, futures_notional=349_000.0)
    trip = futures.overnight_notional(s, CFG)
    assert trip is not None
    assert trip.context["notional_pct"] == "0.0000"


def test_house_money_silent_below_threshold(make_snapshot):
    s = make_snapshot(futures_realized_pnl_today=2999.0)
    assert futures.house_money_lockout(s, CFG) is None

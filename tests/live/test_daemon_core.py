# tests/live/test_daemon_core.py
import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from governor.config import LiveConfig, RulesConfig
from governor.live.daemon import (
    BrakeDaemon,
    is_expected_restart,
    is_weekly_relogin_window,
    next_briefing_dt,
    next_weekly_probe_dt,
    should_alert_blind,
)

ET = ZoneInfo("America/New_York")


def test_next_briefing_picks_soonest_future_time():
    now = dt.datetime(2026, 6, 17, 11, 0, tzinfo=ET)  # after 10:30, before 12:30
    nxt = next_briefing_dt(now, ["10:30", "12:30", "15:55"])
    assert (nxt.hour, nxt.minute) == (12, 30)
    assert nxt.date() == now.date()


def test_next_briefing_rolls_to_tomorrow_after_last():
    now = dt.datetime(2026, 6, 17, 16, 30, tzinfo=ET)  # after 15:55
    nxt = next_briefing_dt(now, ["10:30", "12:30", "15:55"])
    assert (nxt.hour, nxt.minute) == (10, 30)
    assert nxt.date() == now.date() + dt.timedelta(days=1)


def test_daemon_constructs_with_dry_run_false():
    # Plan 3 removed the NotImplementedError guard — dry_run=False is now valid (armed mode).
    cfg = RulesConfig(live=LiveConfig(dry_run=False))
    assert BrakeDaemon(cfg) is not None


def test_daemon_constructs_with_dry_run_true():
    # default dry_run is True -> constructs fine (no connection opened)
    assert BrakeDaemon(RulesConfig()) is not None


def test_subscribe_pnl_calls_reqpnl_with_account():
    d = BrakeDaemon(RulesConfig())
    calls = []
    d.conn.ib = SimpleNamespace(reqPnL=lambda acct: calls.append(acct),
                                managedAccounts=lambda: ["U1"])
    d._subscribe_pnl()
    assert calls == ["U1"]


def test_subscribe_pnl_swallows_errors():
    d = BrakeDaemon(RulesConfig())

    def boom(acct):
        raise RuntimeError("no pnl subscription")

    d.conn.ib = SimpleNamespace(reqPnL=boom, managedAccounts=lambda: ["U1"])
    d._subscribe_pnl()  # must NOT raise


def test_expected_restart_inside_window():
    now = dt.datetime(2026, 6, 17, 23, 55, tzinfo=ET)   # 4 min before 23:59
    assert is_expected_restart(now, "23:59", 10.0) is True


def test_expected_restart_outside_window():
    now = dt.datetime(2026, 6, 17, 12, 0, tzinfo=ET)
    assert is_expected_restart(now, "23:59", 10.0) is False


def test_expected_restart_wraps_past_midnight():
    now = dt.datetime(2026, 6, 18, 0, 5, tzinfo=ET)      # 6 min after a 23:59 restart
    assert is_expected_restart(now, "23:59", 10.0) is True


def test_next_weekly_probe_rolls_to_sunday():
    now = dt.datetime(2026, 6, 17, 12, 0, tzinfo=ET)    # Wed 2026-06-17
    nxt = next_weekly_probe_dt(now, "09:00")
    assert nxt.weekday() == 6                            # Sunday
    assert (nxt.hour, nxt.minute) == (9, 0)
    assert nxt.date() == dt.date(2026, 6, 21)           # the coming Sunday


def test_next_weekly_probe_same_sunday_before_time():
    now = dt.datetime(2026, 6, 21, 7, 0, tzinfo=ET)     # Sunday, before 09:00
    nxt = next_weekly_probe_dt(now, "09:00")
    assert nxt.date() == dt.date(2026, 6, 21)


def test_next_weekly_probe_after_time_goes_next_week():
    now = dt.datetime(2026, 6, 21, 10, 0, tzinfo=ET)    # Sunday, after 09:00
    nxt = next_weekly_probe_dt(now, "09:00")
    assert nxt.date() == dt.date(2026, 6, 28)


def test_weekly_relogin_window_true_on_sunday_morning():
    now = dt.datetime(2026, 6, 21, 3, 0, tzinfo=ET)     # Sunday 03:00, between 01:00 and 09:00
    assert is_weekly_relogin_window(now, "01:00", "09:00") is True


def test_weekly_relogin_window_false_off_sunday():
    now = dt.datetime(2026, 6, 20, 3, 0, tzinfo=ET)     # Saturday
    assert is_weekly_relogin_window(now, "01:00", "09:00") is False


def test_blind_alert_unexpected_after_grace():
    assert should_alert_blind(120.0, expected=False,
                              alert_after_seconds=90.0, restart_window_min=10.0) is True
    assert should_alert_blind(30.0, expected=False,
                              alert_after_seconds=90.0, restart_window_min=10.0) is False


def test_blind_alert_expected_tolerates_full_window():
    # Inside an expected restart: a normal 3-min outage must NOT alert (180s < 600s),
    # but a 12-min stall during the window does.
    assert should_alert_blind(180.0, expected=True,
                              alert_after_seconds=90.0, restart_window_min=10.0) is False
    assert should_alert_blind(720.0, expected=True,
                              alert_after_seconds=90.0, restart_window_min=10.0) is True

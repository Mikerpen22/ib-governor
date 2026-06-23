# tests/live/test_daemon_core.py
import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from governor.config import LiveConfig, RulesConfig
from governor.live.daemon import BrakeDaemon, next_briefing_dt

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

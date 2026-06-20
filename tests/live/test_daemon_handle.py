# tests/live/test_daemon_handle.py
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from governor.actions.lockout import LockoutStore
from governor.actions.tokens import ConfirmTokenGate
from governor.config import RulesConfig
from governor.live.daemon import is_stale, BrakeDaemon
from governor.model import ActionType, AssetClass, Severity, StateSnapshot, Trip

ET = ZoneInfo("America/New_York")


def _trip(action):
    return Trip(rule_id="r", asset_class=AssetClass.FUTURE, severity=Severity.HARD,
               message="m", action=action)


def _snap():
    return StateSnapshot(ts="2026-06-17T15:50:00-04:00", nav=250_000.0)


class FakeExecutor:
    def __init__(self):
        self.cancels = 0
        self.lockouts = []
        self.trims = []

    def cancel_all_orders(self):
        self.cancels += 1
        return True

    def lockout(self, kind, until, reason, now):
        self.lockouts.append((kind, reason))

    def trim_futures(self, target_contracts):
        self.trims.append(target_contracts)
        return True


def _daemon(tmp_path, token="TOK1"):
    d = BrakeDaemon(RulesConfig())
    d.executor = FakeExecutor()
    d.tokens = ConfirmTokenGate(300, token_factory=lambda: token)
    d.lockout_store = LockoutStore(tmp_path / "l.json")
    d._alerts = []
    d.alert = d._alerts.append  # stub: capture alert text, no Telegram/macOS
    return d


def test_is_stale():
    now = dt.datetime(2026, 6, 17, 12, 0, tzinfo=ET)
    fresh = now - dt.timedelta(seconds=30)
    old = now - dt.timedelta(seconds=200)
    assert is_stale(last=fresh, now=now, max_age=90) is False
    assert is_stale(last=old, now=now, max_age=90) is True
    assert is_stale(last=None, now=now, max_age=90) is False  # never-built yet: not "stale"


def test_handle_stages_action_without_executing(tmp_path):
    d = _daemon(tmp_path)
    d.handle([_trip(ActionType.TRIM_FUTURES)], _snap(), "briefing")
    assert d.executor.trims == []                      # staged, NOT executed
    assert any("TOK1" in a for a in d._alerts)         # token offered for confirm


def test_on_confirm_valid_token_executes_once(tmp_path):
    d = _daemon(tmp_path)
    d.handle([_trip(ActionType.TRIM_FUTURES)], _snap(), "briefing")
    d.on_confirm("CONFIRM TOK1")
    assert len(d.executor.trims) == 1


def test_on_confirm_wrong_token_executes_nothing(tmp_path):
    d = _daemon(tmp_path)
    d.handle([_trip(ActionType.TRIM_FUTURES)], _snap(), "briefing")
    d.on_confirm("CONFIRM WRONGTOKEN")
    assert d.executor.trims == []


def test_alert_only_trip_stages_no_token(tmp_path):
    d = _daemon(tmp_path)
    d.handle([_trip(ActionType.ALERT_ONLY)], _snap(), "briefing")
    assert d.executor.trims == [] and d.executor.lockouts == []


def test_execute_branches_route_to_executor(tmp_path):
    d = _daemon(tmp_path)
    d._execute(ActionType.LOCKOUT_FUTURES_48H)
    d._execute(ActionType.PLATFORM_OFF_TODAY)
    d._execute(ActionType.TRIM_FUTURES)
    assert len(d.executor.lockouts) == 2   # both lockout kinds -> lockout()
    assert len(d.executor.trims) == 1      # trim -> trim_futures()


def test_unreadable_lockout_on_fill_fails_closed(tmp_path):
    """SAFETY: a present-but-corrupt lockout file when a fill arrives must make the
    daemon scream BRAKE BLIND (assume locked), never silently proceed."""
    d = _daemon(tmp_path)
    (tmp_path / "l.json").write_text("{garbage")
    d.handle([], _snap(), "fill")
    assert any("BRAKE BLIND" in a for a in d._alerts)


def test_failed_action_alerts_loudly(tmp_path):
    """If a confirmed action raises while executing, the daemon must alert that the
    brake may not be armed — not let the failure hide in the telegram-loop catch."""
    d = _daemon(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    d.executor.lockout = boom
    d._execute(ActionType.LOCKOUT_FUTURES_48H)
    assert any("FAILED" in a for a in d._alerts)


def test_action_within_cooldown_is_not_restaged(tmp_path):
    """SAFETY (no over-trim): after an action executes, a re-tripping rule must NOT
    re-stage the same action during the cooldown window — no fresh confirm token."""
    d = _daemon(tmp_path)
    d._last_executed["trim_futures"] = d._now()   # just executed
    d.handle([_trip(ActionType.TRIM_FUTURES)], _snap(), "briefing")
    assert not any("TOK1" in a for a in d._alerts)            # no confirm token re-staged
    assert any("cooldown" in a.lower() for a in d._alerts)    # told why


def test_action_restaged_after_cooldown(tmp_path):
    """Once the cooldown elapses, the action stages normally again."""
    d = _daemon(tmp_path)
    d._last_executed["trim_futures"] = d._now() - dt.timedelta(
        seconds=d.config.live.action_cooldown_seconds + 10
    )
    d.handle([_trip(ActionType.TRIM_FUTURES)], _snap(), "briefing")
    assert any("TOK1" in a for a in d._alerts)                # re-staged after cooldown


def test_execute_records_cooldown_only_on_success(tmp_path):
    """The cooldown starts on a successful execute, but NOT on a failed one (so a
    failed trim can still be retried)."""
    d = _daemon(tmp_path)
    d._execute(ActionType.TRIM_FUTURES)
    assert "trim_futures" in d._last_executed                 # success -> cooldown armed

    d2 = _daemon(tmp_path)

    def boom(*a, **k):
        raise OSError("exchange down")

    d2.executor.trim_futures = boom
    d2._execute(ActionType.TRIM_FUTURES)
    assert "trim_futures" not in d2._last_executed            # failure -> no cooldown, retry allowed


# --- edge-triggered soft alerts (no 3x/day repeat-spam of a standing WARN) ---

def _warn(rule_id="equities.sector_concentration"):
    return Trip(rule_id=rule_id, asset_class=AssetClass.EQUITY, severity=Severity.WARN,
                message="Technology is 56% of NAV", action=ActionType.ALERT_ONLY)


def test_standing_warn_alerts_once_then_suppressed(tmp_path):
    """A persistent WARN (e.g. sector concentration) is announced once; subsequent
    briefings stay quiet about it — no repeat spam across the day."""
    d = _daemon(tmp_path)
    d.handle([_warn()], _snap(), "briefing")
    assert sum("sector_concentration" in a for a in d._alerts) == 1
    d.handle([_warn()], _snap(), "briefing")   # same WARN, next briefing
    d.handle([_warn()], _snap(), "briefing")   # and the next
    assert sum("sector_concentration" in a for a in d._alerts) == 1   # still just once


def test_cleared_warn_announced_then_rearmed(tmp_path):
    """When a standing WARN resolves the daemon says 'cleared' once; if it later
    re-trips, the edge is re-armed and it alerts again."""
    d = _daemon(tmp_path)
    d.handle([_warn()], _snap(), "briefing")          # appears
    d.handle([], _snap(), "briefing")                 # resolves
    assert any("cleared" in a.lower() for a in d._alerts)
    d._alerts.clear()
    d.handle([_warn()], _snap(), "briefing")          # returns
    assert sum("sector_concentration" in a for a in d._alerts) == 1


def test_hard_trip_alerts_every_time(tmp_path):
    """HARD trips are NOT edge-suppressed — they alert on every evaluation."""
    d = _daemon(tmp_path)
    d.handle([_trip(ActionType.ALERT_ONLY)], _snap(), "briefing")   # _trip is HARD
    d.handle([_trip(ActionType.ALERT_ONLY)], _snap(), "briefing")
    assert sum("[hard]" in a for a in d._alerts) == 2


# --- staleness watchdog: quiet refresh in an idle market, scream only on real stall ---

def test_stale_snapshot_refreshes_silently(tmp_path):
    """A quiet market ages the snapshot — the watchdog refreshes WITHOUT a BRAKE BLIND
    alert (idle != blind)."""
    d = _daemon(tmp_path)
    d.conn.ib.isConnected = lambda: True
    d._last_built = d._now() - dt.timedelta(seconds=10_000)   # very stale (quiet market)
    refreshed = []
    d.evaluate_and_handle = lambda reason: refreshed.append(reason)
    d._refresh_if_stale()
    assert refreshed == ["staleness"]                          # it DID refresh
    assert not any("BLIND" in a for a in d._alerts)            # but stayed quiet


def test_stale_refresh_failure_screams_blind(tmp_path):
    """If the forced refresh raises (socket up but data dead), THAT is the real
    blind condition → alert."""
    d = _daemon(tmp_path)
    d.conn.ib.isConnected = lambda: True
    d._last_built = d._now() - dt.timedelta(seconds=10_000)

    def boom(reason):
        raise RuntimeError("reqAccountUpdates timed out")

    d.evaluate_and_handle = boom
    d._refresh_if_stale()
    assert any("BLIND" in a for a in d._alerts)


def test_fresh_snapshot_is_noop(tmp_path):
    """A fresh snapshot → no refresh, no alert."""
    d = _daemon(tmp_path)
    d.conn.ib.isConnected = lambda: True
    d._last_built = d._now()                                   # fresh
    refreshed = []
    d.evaluate_and_handle = lambda reason: refreshed.append(reason)
    d._refresh_if_stale()
    assert refreshed == [] and d._alerts == []


def test_disconnected_is_noop(tmp_path):
    """When the socket is down the disconnect path owns the alert — the staleness
    watchdog stays out of it."""
    d = _daemon(tmp_path)
    d.conn.ib.isConnected = lambda: False
    d._last_built = d._now() - dt.timedelta(seconds=10_000)
    refreshed = []
    d.evaluate_and_handle = lambda reason: refreshed.append(reason)
    d._refresh_if_stale()
    assert refreshed == [] and d._alerts == []

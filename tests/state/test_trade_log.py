import datetime as dt

from governor.state.trade_log import WeeklyTradeLog

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def test_counts_within_week_and_prunes(tmp_path):
    log = WeeklyTradeLog(tmp_path / "trades.json")
    log.record("NVDA", "ord-1", T0 - dt.timedelta(days=1))
    log.record("NVDA", "ord-2", T0 - dt.timedelta(days=2))
    log.record("NVDA", "ord-3", T0 - dt.timedelta(days=10))   # outside the 7-day window
    log.record("AMD", "ord-4", T0)
    assert log.count("NVDA", now=T0, days=7) == 2
    assert log.count("AMD", now=T0, days=7) == 1
    assert log.count("TSLA", now=T0, days=7) == 0


def test_persists(tmp_path):
    p = tmp_path / "trades.json"
    WeeklyTradeLog(p).record("NVDA", "ord-1", T0)
    assert WeeklyTradeLog(p).count("NVDA", now=T0, days=7) == 1


def test_partial_fill_dedup(tmp_path):
    """Recording the same order_id twice for a symbol counts as one trade."""
    log = WeeklyTradeLog(tmp_path / "trades.json")
    log.record("NVDA", "ord-42", T0)
    log.record("NVDA", "ord-42", T0 + dt.timedelta(seconds=1))  # second partial fill
    assert log.count("NVDA", now=T0 + dt.timedelta(seconds=2), days=7) == 1


def test_different_order_ids_count_separately(tmp_path):
    """Two distinct order_ids for the same symbol count as two trades."""
    log = WeeklyTradeLog(tmp_path / "trades.json")
    log.record("AAPL", "ord-10", T0)
    log.record("AAPL", "ord-11", T0 + dt.timedelta(hours=1))
    assert log.count("AAPL", now=T0 + dt.timedelta(hours=2), days=7) == 2

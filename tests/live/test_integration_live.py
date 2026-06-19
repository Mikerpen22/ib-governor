"""Read-only integration tests against a live/paper TWS. Skip when TWS is unreachable.
Run with: .venv/bin/pytest -m integration -v   (requires TWS running, API enabled)."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from governor.config import load_config
from governor.live.connection import BrakeConnection
from governor.live.snapshot import build_snapshot

pytestmark = pytest.mark.integration

ET = ZoneInfo("America/New_York")


@pytest.fixture(scope="module")
def conn():
    cfg = load_config("config/rules.yaml")
    c = BrakeConnection(cfg.live)
    try:
        c.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"TWS not reachable: {exc}")
    yield c
    c.disconnect()


def test_live_snapshot_is_well_formed(conn):
    cfg = load_config("config/rules.yaml")
    snap = build_snapshot(now=dt.datetime.now(tz=ET),
                          account_values=conn.ib.accountValues(),
                          portfolio_items=conn.ib.portfolio(),
                          fills=conn.ib.fills(), cfg=cfg.live)
    # contract assertions, not value assertions (values change intraday)
    assert snap.nav > 0
    assert 0.0 <= snap.margin_cushion <= 5.0
    assert snap.futures_trade_count_today >= 0
    assert isinstance(snap.contract_trade_counts_today, dict)

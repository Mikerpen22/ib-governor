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


# A distinct, read-only client id so this test runs ALONGSIDE the live daemon
# (which holds client_id 4) rather than colliding with it. A clientId clash makes
# connect() raise, the fixture skip, and the test silently verify nothing while
# the daemon is up — which is exactly when you'd want the real read path checked.
# 11 is clear of daemon=4 / gate=5 / daily=6 / technicals=7 and the agent sandbox
# range (20–119). readonly=True is the correct posture for a pure read test.
_TEST_CLIENT_ID = 11


@pytest.fixture(scope="module")
def conn():
    cfg = load_config("config/rules.yaml")
    test_live = cfg.live.model_copy(update={"client_id": _TEST_CLIENT_ID, "readonly": True})
    c = BrakeConnection(test_live)
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

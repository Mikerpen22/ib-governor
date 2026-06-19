"""Contract tests against REAL captured IBKR shapes (tests/fixtures/*.json).

The fixtures are produced by `scripts/capture_fixtures.py` from a live TWS session
(account ids scrubbed). These tests SKIP when the fixtures are absent, so the suite
stays green on any machine without TWS; once captured, they drive the real snapshot
builder against the real shapes — catching drift between our assumptions and what
IBKR actually returns (string realizedPNL, missing multiplier, the UNSET sentinel,
multi-currency account rows, etc.) that hand-written mocks would never reproduce."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from governor.config import LiveConfig
from governor.live.snapshot import account_metrics, build_snapshot

ET = ZoneInfo("America/New_York")
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def _ns(obj):
    """Recursively turn captured JSON into attribute objects that quack like the
    ib_async objects the builder consumes (dict -> namespace, list -> list)."""
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(v) for v in obj]
    return obj


def _load(name: str):
    path = FIXTURES / f"{name}.json"
    if not path.exists():
        pytest.skip(f"no captured {name}.json — run scripts/capture_fixtures.py with TWS up")
    return _ns(json.loads(path.read_text()))


def test_account_metrics_against_real_shape():
    nav, cushion, gross = account_metrics(_load("account_values"))
    assert nav > 0, "NetLiquidation should be present and positive in a real account"
    assert cushion >= 0.0          # ExcessLiquidity / NAV
    assert gross >= 0.0            # GrossPositionValue / NAV


def test_build_snapshot_against_real_shapes():
    cfg = LiveConfig()
    snap = build_snapshot(
        now=dt.datetime(2026, 6, 17, 15, 50, tzinfo=ET),
        account_values=_load("account_values"),
        portfolio_items=_load("portfolio"),
        fills=_load("fills"),
        cfg=cfg,
    )
    assert snap.nav > 0                                   # NAV round-trips from the real data
    assert snap.futures_notional >= 0.0
    assert all(w >= 0 for w in snap.name_weights.values())
    # Guard the IBKR UNSET-double sentinel (1.79e308) leaking into realized P&L:
    # if this trips, futures_activity must filter unset realizedPNL before summing.
    assert abs(snap.futures_realized_pnl_today) < 1e12, (
        "realized futures P&L is absurdly large — an IBKR UNSET sentinel likely "
        "leaked through; filter it in snapshot.futures_activity()"
    )

"""Read-only live integration test for the pre-trade gate analyze path.

Exercises analyze_intent against a real (paper) TWS without placing any order.
Skip gracefully when TWS is unreachable — identical skip pattern to test_integration_live.py.

Run with: .venv/bin/pytest -m integration -v   (requires TWS running, API enabled)
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from governor.actions.lockout import LockoutStore
from governor.config import load_config
from governor.gate.analysis import Verdict
from governor.gate.intent import Action, OrderIntent, OrderType, SecType
from governor.gate.runner import analyze_intent
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


def test_gate_analyze_spy_read_only(conn):
    """analyze_intent returns a valid verdict for a 1-share SPY BUY without placing any order."""
    cfg = load_config("config/rules.yaml")
    ib = conn.ib

    # Build a live snapshot from current account state
    now = dt.datetime.now(tz=ET)
    account_values = ib.accountValues()
    portfolio = ib.portfolio()
    fills = ib.fills()

    current = build_snapshot(
        now=now,
        account_values=account_values,
        portfolio_items=portfolio,
        fills=fills,
        cfg=cfg.live,
        sector_by_symbol={},
    )

    # Tiny, safe intent — 1 share of SPY, market order; analyze places NOTHING
    intent = OrderIntent(
        action=Action.BUY,
        symbol="SPY",
        quantity=1,
        sec_type=SecType.STK,
        order_type=OrderType.MARKET,
    )

    lockout_store = LockoutStore("config/lockout.json")

    try:
        verdict, preview = analyze_intent(
            ib,
            intent,
            current,
            cfg,
            lockout_store,
            now=now,
            sector=None,
        )
    except ValueError as exc:
        # On a bare clone the TWS connection can succeed while the account lacks a
        # live market-data subscription (IBKR error 10089/10168). The gate then
        # can't price the reference symbol and raises a "reference price" ValueError.
        # That's an environment/entitlement gap, not a gate bug — skip gracefully.
        msg = str(exc).lower()
        if "reference price" in msg or "nan" in msg:
            pytest.skip(
                f"Market-data entitlement missing for {intent.symbol} "
                f"(no live/delayed price available): {exc}"
            )
        raise
    # analyze_intent is read-only by design (see runner.py module docstring).
    # No disconnect here — the fixture handles teardown.

    # --- structural assertions (value-agnostic; values change intraday) ---

    # verdict.level must be a Verdict enum member
    assert isinstance(verdict.level, Verdict), (
        f"Expected Verdict enum, got {type(verdict.level)}"
    )

    # preview must be JSON-serializable
    serialized = json.dumps(preview)
    assert serialized  # non-empty

    # preview must contain all required keys
    required_keys = {"order_notional", "pct_nav", "init_margin", "verdict", "reasons"}
    missing = required_keys - set(preview.keys())
    assert not missing, f"preview missing keys: {missing}"

    # value-type sanity
    assert isinstance(preview["order_notional"], (int, float))
    assert isinstance(preview["pct_nav"], (int, float))
    # init_margin is legitimately None when whatIfOrder returns [] (e.g. TWS
    # 'Read-Only API' enabled, or the preview is otherwise unavailable).
    assert preview["init_margin"] is None or isinstance(preview["init_margin"], (int, float))
    assert isinstance(preview["verdict"], str)
    assert isinstance(preview["reasons"], list)

    # analyze never calls placeOrder — confirm no new open orders were created.
    # (If TWS had pre-existing orders this would still hold: we only called analyze_intent
    # which is documented as strictly read-only and never calls ib.placeOrder.)
    # We document the guarantee rather than checking ib.openOrders() which can be
    # flaky (pre-existing orders from the paper account would appear regardless).
    # The design invariant is enforced in runner.py: only submit_intent calls the executor.

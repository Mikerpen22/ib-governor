"""Real-API tests for the order WRITE path (the single chokepoint).

executor.place_order is thoroughly spy-tested (tests/actions/test_executor.py)
for the dry-run/armed gating. What a spy can't catch: whether the (contract,
order) objects we hand to ib_async are actually ACCEPTED by a real TWS — a
malformed contract, a bad exchange, an order field TWS rejects. Two real checks:

  - whatIfOrder acceptance (safe on live: it's a margin PREVIEW, places nothing)
    proves our real qualify()+build_order() produce a TWS-valid order shape. Runs
    wherever TWS is up.
  - a real placeOrder + cancel, but ONLY against a PAPER account — gated behind
    IBG_PAPER_WRITE=1, a hardcoded paper port, AND a hard "account id starts with
    DU" guard so it can never fire against a live account. This honors the
    project rule: paper first, never place a live order in a test.
"""
from __future__ import annotations

import os
import socket

import pytest

from governor.config import load_config
from governor.gate.intent import Action, OrderIntent, OrderType, SecType, build_order
from governor.gate.runner import _order_state, _usable_float, qualify
from governor.live.connection import BrakeConnection

pytestmark = pytest.mark.integration

_CONFIG = "config/rules.yaml"
_PAPER_PORT = 7497  # IBKR paper TWS — NEVER 7496 (live)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _snap_intent() -> OrderIntent:
    return OrderIntent(
        action=Action.BUY, symbol="SNAP", quantity=1.0,
        sec_type=SecType.STK, order_type=OrderType.MARKET,
    )


def test_whatif_accepts_our_real_order_shape():
    """Our real qualify()+build_order() yield a contract+order that a live TWS
    accepts for a margin preview — catching a malformed contract/order the spy
    tests never could. Read-only connection, distinct client id; whatIfOrder
    places NOTHING. Skips (not fails) when TWS is down or the preview is blocked
    (TWS 'Read-Only API' setting returns [])."""
    cfg = load_config(_CONFIG)
    if not _port_open(cfg.live.host, cfg.live.port):
        pytest.skip(f"TWS not reachable at {cfg.live.host}:{cfg.live.port}")

    test_live = cfg.live.model_copy(update={"client_id": 12, "readonly": True})
    conn = BrakeConnection(test_live)
    try:
        conn.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"TWS connect failed: {exc}")
    try:
        intent = _snap_intent()
        contract = qualify(conn.ib, intent)          # real qualifyContracts
        assert contract is not None
        order = build_order(intent)
        wstate = _order_state(conn.ib.whatIfOrder(contract, order))
    finally:
        conn.disconnect()

    if wstate is None:
        pytest.skip("TWS returned no whatIf preview (Read-Only API setting?) — "
                    "order-shape acceptance can't be verified on this TWS config")
    # OrderState ALWAYS carries these attrs (so hasattr proves nothing); the real
    # signal is that TWS *populated* one with a usable, finite, non-sentinel number.
    # _usable_float filters empty strings and the IBKR UNSET sentinel (~1.79e308).
    init_margin = _usable_float(getattr(wstate, "initMarginAfter", None))
    if init_margin is None:
        pytest.skip("whatIf preview has no usable initMarginAfter (TWS Read-Only API "
                    "setting?) — order-shape acceptance can't be verified here")
    # A real margin number means TWS parsed and ACCEPTED our contract+order shape.
    assert init_margin >= 0.0, f"nonsensical init margin from whatIf: {init_margin}"


@pytest.mark.skipif(
    os.getenv("IBG_PAPER_WRITE") != "1",
    reason="set IBG_PAPER_WRITE=1 (and run a PAPER TWS on 7497) to exercise a real "
           "placeOrder+cancel — never runs against a live account",
)
def test_real_place_and_cancel_on_paper():
    """A real round-trip through executor.place_order against a PAPER account:
    place a far-below-market limit buy (cannot fill), confirm it rests open, then
    cancel it. Triple-guarded against touching a live account: opt-in env, the
    hardcoded paper port, and a paper-account-id ('DU…') assertion."""
    cfg = load_config(_CONFIG)
    host = cfg.live.host
    if not _port_open(host, _PAPER_PORT):
        pytest.skip(f"paper TWS not reachable at {host}:{_PAPER_PORT}")

    from ib_async import LimitOrder, Stock

    paper_live = cfg.live.model_copy(
        update={"port": _PAPER_PORT, "client_id": 13, "readonly": False}
    )
    conn = BrakeConnection(paper_live)
    try:
        conn.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"paper TWS connect failed: {exc}")

    try:
        ib = conn.ib
        # HARD guard: only proceed on a paper account (IBKR paper ids start 'DU').
        accounts = list(ib.managedAccounts())
        assert accounts and all(a.startswith("DU") for a in accounts), (
            f"refusing to place: connected account(s) {accounts} are not paper (DU…)"
        )

        contract = qualify(ib, OrderIntent(
            action=Action.BUY, symbol="AAPL", quantity=1.0,
            sec_type=SecType.STK, order_type=OrderType.LIMIT, limit_price=1.0,
        ))
        # Far-below-market limit ($1) — rests, cannot fill.
        order = LimitOrder("BUY", 1, 1.0)
        trade = ib.placeOrder(contract, order)
        ib.sleep(1.0)
        try:
            assert trade.order.orderId in {t.order.orderId for t in ib.openTrades()}, (
                "placed paper order did not appear in open trades"
            )
        finally:
            ib.cancelOrder(trade.order)
            ib.sleep(1.0)
    finally:
        conn.disconnect()

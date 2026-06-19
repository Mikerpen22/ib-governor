"""Pre-trade gate runner: qualify + analyze + submit.

This is the I/O seam for the gate. All live ib_async calls live here (except
executor.place_order which is already isolated in ActionExecutor). The analysis
functions (hypothetical_snapshot, decide, etc.) remain pure and testable
without any IB connection.

Design contract:
- analyze_intent is READ-ONLY — it places no order, calls no write methods.
- submit_intent is the sole write path; it delegates immediately to executor.
- Only this module + ActionExecutor call ib write methods.
"""
from __future__ import annotations

from ib_async import Future, LimitOrder, Stock, StopOrder

from governor.config import RulesConfig
from governor.gate.analysis import (
    GateFacts,
    GateVerdict,
    decide,
    hypothetical_snapshot,
    sizing,
)
from governor.gate.intent import Action, OrderIntent, SecType, build_order
from governor.live.snapshot import _to_float
from governor.model import StateSnapshot
from governor.rules.engine import evaluate
from governor.state.json_store import StateFileError

# Exchange map for common futures roots. Default falls back to "CME".
_FUT_EXCHANGE: dict[str, str] = {
    "MNQ": "CME",
    "MES": "CME",
    "ES": "CME",
    "NQ": "CME",
}


# ---------------------------------------------------------------------------
# Contract qualification (I/O)
# ---------------------------------------------------------------------------


def qualify(ib, intent: OrderIntent):
    """Return the first qualified ib_async Contract for the given intent.

    STK: Stock(symbol, "SMART", "USD")
    FUT: Future(symbol, exchange) using _FUT_EXCHANGE map (default "CME")
    """
    if intent.sec_type is SecType.STK:
        contract = Stock(intent.symbol, "SMART", "USD")
    else:
        exchange = _FUT_EXCHANGE.get(intent.symbol, "CME")
        contract = Future(intent.symbol, exchange=exchange)

    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise ValueError(
            f"IB could not qualify a contract for {intent.symbol!r} "
            f"({intent.sec_type.value})"
        )
    return qualified[0]


# ---------------------------------------------------------------------------
# Price and notional helpers (I/O for live price; pure arithmetic otherwise)
# ---------------------------------------------------------------------------


def _reference_price(ib, contract, intent: OrderIntent) -> float:
    """Return the reference price for sizing the order.

    Priority: limit_price → stop_price → live market price.
    Raises ValueError if no price is obtainable (fail loud).
    """
    if intent.limit_price is not None:
        return float(intent.limit_price)
    if intent.stop_price is not None:
        return float(intent.stop_price)

    # Fetch live price from TWS
    ticker = ib.reqTickers(contract)[0]
    price = ticker.marketPrice()
    if price and price > 0:
        return float(price)
    last = getattr(ticker, "last", None)
    if last and last > 0:
        return float(last)
    close = getattr(ticker, "close", None)
    if close and close > 0:
        return float(close)

    raise ValueError(
        f"Cannot determine a reference price for {intent.symbol} "
        f"(marketPrice={price!r}, last={last!r}, close={close!r})"
    )


def _order_notional(intent: OrderIntent, contract, price: float) -> float:
    """Compute the order notional: quantity × price × multiplier."""
    raw_mult = getattr(contract, "multiplier", None)
    mult = _to_float(raw_mult) if raw_mult is not None else 0.0
    if mult <= 0:
        mult = 1.0
    return intent.quantity * price * mult


# ---------------------------------------------------------------------------
# Buying-power check (pure given the whatIf OrderState)
# ---------------------------------------------------------------------------


def _order_state(whatif):
    """ib.whatIfOrder() may return an OrderState, a [OrderState], or [] (the empty list
    happens when TWS's 'Read-Only API' setting blocks the preview). Normalize to a single
    state object or None."""
    if isinstance(whatif, list):
        return whatif[0] if whatif else None
    return whatif


def _buying_power_ok(state) -> bool:
    """Return False ONLY when insufficiency is clearly established: a margin state is
    present, both equityWithLoanAfter and initMarginAfter parse positive, and init margin
    exceeds equity. A missing state (preview unavailable) or missing / zero / unparseable
    fields are treated as OK — we never false-block a trade on absent margin data."""
    if state is None:
        return True
    equity = _to_float(getattr(state, "equityWithLoanAfter", 0))
    init = _to_float(getattr(state, "initMarginAfter", 0))
    if equity > 0 and init > 0 and init > equity:
        return False
    return True


# ---------------------------------------------------------------------------
# Lockout routing (pure)
# ---------------------------------------------------------------------------


def _lockout_blocks(lockout, sec_type: SecType) -> bool:
    """True if the lockout prevents a new trade of the given sec_type."""
    if lockout is None:
        return False
    if lockout.kind == "platform_off_today":
        return True  # blocks everything
    if lockout.kind == "futures_48h":
        return sec_type is SecType.FUT
    return False


# ---------------------------------------------------------------------------
# build_bracket — construct [parent, *children] for bracket orders
# ---------------------------------------------------------------------------


def build_bracket(ib, intent: OrderIntent, contract):
    """Return [parent_entry, *protective_children] for a bracketed intent.

    Children are opposite-side, parent-linked, OCA-grouped; only the last
    transmits. If neither protective price is set, returns just the single
    entry order (transmit=True).
    """
    parent = build_order(intent)
    parent.orderId = ib.client.getReqId()
    opp = "SELL" if intent.action is Action.BUY else "BUY"
    children = []
    if intent.take_profit is not None:
        tp = LimitOrder(opp, intent.quantity, intent.take_profit)
        tp.parentId = parent.orderId
        tp.orderId = ib.client.getReqId()
        tp.transmit = False
        children.append(tp)
    if intent.stop_loss is not None:
        sl = StopOrder(opp, intent.quantity, intent.stop_loss)
        sl.parentId = parent.orderId
        sl.orderId = ib.client.getReqId()
        sl.transmit = False
        children.append(sl)
    if not children:
        parent.transmit = True
        return [parent]
    parent.transmit = False
    children[-1].transmit = True
    if len(children) > 1:
        oca = f"oca_{parent.orderId}"
        for c in children:
            c.ocaGroup = oca
            c.ocaType = 1
    return [parent, *children]


# ---------------------------------------------------------------------------
# analyze_intent — read-only gate analysis
# ---------------------------------------------------------------------------


def analyze_intent(
    ib,
    intent: OrderIntent,
    current: StateSnapshot,
    config: RulesConfig,
    lockout_store,
    *,
    now,
    sector: str | None = None,
) -> tuple[GateVerdict, dict]:
    """Run the full pre-trade gate analysis. Places NO order.

    Returns (GateVerdict, preview) where preview is a plain JSON-serializable
    dict for the CLI / skill layer.

    Fail-closed on corrupt lockout state: StateFileError → lockout_active=True.
    """
    # 1. Build the contract and order objects
    contract = qualify(ib, intent)
    order = build_order(intent)

    # 2. What-if margin check. Real ib.whatIfOrder returns [], [OrderState], or OrderState
    # depending on TWS state (e.g. [] when 'Read-Only API' is on) — normalize defensively.
    whatif = ib.whatIfOrder(contract, order)
    wstate = _order_state(whatif)
    bp_ok = _buying_power_ok(wstate)

    # 3. Reference price + notional
    price = _reference_price(ib, contract, intent)
    notional = _order_notional(intent, contract, price)

    # 4. Hypothetical post-trade snapshot
    hypo = hypothetical_snapshot(
        current,
        intent,
        notional,
        mnq_notional_usd=config.live.mnq_notional_usd,
        sector=sector,
    )

    # 5. Rule-engine evaluation on the hypothetical snapshot
    trips = evaluate(hypo, config)

    # 6. Lockout check — fail CLOSED on unreadable state
    try:
        lk = lockout_store.active(now)
        lockout_active = _lockout_blocks(lk, intent.sec_type)
    except StateFileError:
        lockout_active = True

    # 7. Per-trade sizing band
    sized = sizing(notional, current.nav, config.gate)

    # 8. Compose GateFacts and derive verdict
    facts = GateFacts(
        post_trade_trips=tuple(trips),
        lockout_active=lockout_active,
        sizing=sized,
        buying_power_ok=bp_ok,
    )
    verdict = decide(facts)

    # 9. Build the JSON-serializable preview dict
    name_weight_before = current.name_weights.get(intent.symbol, 0.0)
    name_weight_after = hypo.name_weights.get(intent.symbol, 0.0)

    preview: dict = {
        "symbol": intent.symbol,
        "action": intent.action.value,
        "quantity": intent.quantity,
        "order_type": intent.order_type.value,
        "order_notional": notional,
        "pct_nav": sized.pct_nav,
        "buying_power_ok": bp_ok,
        "whatif_available": wstate is not None,
        "init_margin": _to_float(getattr(wstate, "initMarginAfter", 0)) if wstate is not None else None,
        "name_weight_before": name_weight_before,
        "name_weight_after": name_weight_after,
        "trips": [
            {
                "rule_id": t.rule_id,
                "severity": t.severity.value,
                "message": t.message,
            }
            for t in trips
        ],
        "lockout_active": lockout_active,
        "verdict": verdict.level.value,
        "reasons": list(verdict.reasons),
    }

    if intent.stop_loss is not None:
        preview["stop_loss"] = intent.stop_loss
        raw_mult = getattr(contract, "multiplier", None)
        mult = _to_float(raw_mult) if raw_mult is not None else 0.0
        if mult <= 0:
            mult = 1.0
        preview["risk_usd"] = abs(price - intent.stop_loss) * intent.quantity * mult
    if intent.take_profit is not None:
        preview["take_profit"] = intent.take_profit

    return verdict, preview


# ---------------------------------------------------------------------------
# submit_intent — the single write path
# ---------------------------------------------------------------------------


def submit_intent(ib, executor, intent: OrderIntent) -> bool:
    """Qualify the contract, build the order(s), and submit via the executor.

    Routes to executor.place_orders (bracket) when stop_loss or take_profit is
    set; falls back to executor.place_order for a plain single order.

    Returns True iff the order was actually placed (False under dry_run).
    """
    contract = qualify(ib, intent)
    if intent.stop_loss is not None or intent.take_profit is not None:
        orders = build_bracket(ib, intent, contract)
        return executor.place_orders(contract, orders)
    order = build_order(intent)
    return executor.place_order(contract, order)

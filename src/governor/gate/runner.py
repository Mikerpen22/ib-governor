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

import datetime as dt
import math
from zoneinfo import ZoneInfo

from ib_async import Future, LimitOrder, Stock, StopOrder

from governor.config import RulesConfig
from governor.gate.render import render_panels
from governor.gate.analysis import (
    GateFacts,
    GateVerdict,
    decide,
    hypothetical_snapshot,
    sizing,
)
from governor.gate.intent import Action, OrderIntent, SecType, build_order
from governor.live.builder import live_mnq_notional
from governor.live.history import fetch_daily_bars
from governor.live.snapshot import _PNL_SENTINEL, _to_float
from governor.technicals.assess import assess_setup, setup_to_dict
from governor.model import StateSnapshot
from governor.rules.engine import evaluate
from governor.state.json_store import StateFileError

# Exchange map for futures roots. NO silent default — an unmapped root is a
# hard error (see qualify). Routing a future to the wrong exchange would qualify
# the wrong contract and trade real money against it, so we fail loud instead.
_FUT_EXCHANGE: dict[str, str] = {
    # CME (equity-index + FX futures)
    "ES": "CME", "MES": "CME", "NQ": "CME", "MNQ": "CME", "RTY": "CME", "M2K": "CME",
    "6E": "CME", "6J": "CME", "6B": "CME", "6A": "CME", "6C": "CME",
    # NYMEX (energy)
    "CL": "NYMEX", "MCL": "NYMEX", "NG": "NYMEX", "RB": "NYMEX", "HO": "NYMEX",
    # COMEX (metals)
    "GC": "COMEX", "MGC": "COMEX", "SI": "COMEX", "HG": "COMEX",
    # CBOT (rates + grains)
    "ZB": "CBOT", "UB": "CBOT", "ZN": "CBOT", "ZF": "CBOT", "ZT": "CBOT",
    "YM": "CBOT", "MYM": "CBOT", "ZC": "CBOT", "ZS": "CBOT", "ZW": "CBOT",
}

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Contract qualification (I/O)
# ---------------------------------------------------------------------------


def _today_et() -> str:
    """Today's date in America/New_York as 'YYYYMMDD'.

    A module-level seam so front-month selection is deterministic in tests
    (monkeypatch runner._today_et). Futures roll on the exchange's clock, so
    ET — not UTC — is the right reference for "is this expiry still live".
    """
    return dt.datetime.now(tz=ET).strftime("%Y%m%d")


def _front_future(ib, symbol: str, exchange: str):
    """Resolve the front-month FUT contract for *symbol* on *exchange*.

    The real ib.qualifyContracts(Future(symbol, exchange)) — with the default
    returnAll=False — does NOT disambiguate multiple listed expiries: it appends
    None to the result. So we list the expiries via reqContractDetails, pick the
    earliest non-expired one ourselves, then qualify that fully-specified
    contract. Raises ValueError (fail-loud) if no usable contract is found.
    """
    details = ib.reqContractDetails(Future(symbol, exchange=exchange))
    contracts = [
        d.contract for d in details if getattr(d.contract, "secType", None) == "FUT"
    ]
    if not contracts:
        raise ValueError(f"No FUT contracts found for {symbol!r} on {exchange}")

    # lastTradeDateOrContractMonth is 'YYYYMMDD' or 'YYYYMM' — normalize to a
    # comparable 'YYYYMMDD' (pad a bare month to end-of-month-ish) before sorting.
    def _norm(c) -> str:
        d = c.lastTradeDateOrContractMonth or ""
        if len(d) == 8:
            return d
        if len(d) == 6:
            return d + "31"
        return "99999999"

    today = _today_et()
    live = [c for c in contracts if _norm(c) >= today]
    pool = live or contracts  # all expired? still pick the earliest (visible, not None)
    front = min(pool, key=_norm)

    qualified = [c for c in ib.qualifyContracts(front) if c is not None]
    if not qualified:
        raise ValueError(f"Could not qualify front-month {symbol!r}")
    return qualified[0]


def qualify(ib, intent: OrderIntent):
    """Return the single qualified ib_async Contract for the given intent.

    STK: Stock(symbol, "SMART", currency, primaryExchange=...). The result must
         be exactly one contract — zero is a fail-loud error, and >1 means the
         symbol is ambiguous (e.g. listed in multiple currencies) and the caller
         must disambiguate via currency / primary_exchange.
    FUT: Front-month resolution via _front_future (the under-specified Future
         path returns None from the real qualifyContracts — see that helper).
         The exchange comes from _FUT_EXCHANGE with NO silent default.

    All qualifyContracts results are stripped of None BEFORE the truthiness
    check: the real lib appends None for an under-specified contract, and a bare
    `if not [None]` is False — letting that None slip through to whatIfOrder /
    reqTickers / placeOrder, where it fails opaquely.
    """
    if intent.sec_type is SecType.STK:
        contract = Stock(
            intent.symbol,
            "SMART",
            intent.currency,
            primaryExchange=intent.primary_exchange or "",
        )
        qualified = [c for c in ib.qualifyContracts(contract) if c is not None]
        if not qualified:
            raise ValueError(
                f"IB could not qualify a contract for {intent.symbol!r} "
                f"({intent.sec_type.value})"
            )
        if len(qualified) > 1:
            raise ValueError(
                f"{intent.symbol!r} is ambiguous ({len(qualified)} matches); "
                f"specify currency/primary_exchange."
            )
        return qualified[0]

    # SecType.FUT — fail loud on an unmapped root rather than guessing CME.
    exchange = _FUT_EXCHANGE.get(intent.symbol)
    if exchange is None:
        raise ValueError(
            f"Unknown futures root {intent.symbol!r}; add it to _FUT_EXCHANGE "
            f"with its correct exchange before trading."
        )
    return _front_future(ib, intent.symbol, exchange)


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


def _usable_float(value) -> float | None:
    """Parse an OrderState margin field to a usable finite number, or None when the field
    is absent / empty / unparseable / non-finite / the IBKR UNSET sentinel (≈1.79e308).

    The None return is what lets _buying_power_ok tell "field genuinely present with a real
    number" from "no value" — a distinction _to_float (which maps None/'' → 0.0) erases, and
    the reason a 1.79e308 sentinel must not masquerade as a real reading."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or abs(v) >= _PNL_SENTINEL:
        return None
    return v


def _buying_power_ok(state) -> bool:
    """Return False ONLY when insufficiency is clearly established. [M1] Prefer the real
    binding constraint — the *after-trade* free funds (availableFundsAfter, then
    excessLiquidityAfter): if either is present and NEGATIVE, the order overdraws margin →
    BLOCK. Fall back to the init-margin-vs-equity check when no usable after-funds field
    is present.

    All reads are sentinel/finite-guarded via _usable_float, so a 1.79e308 UNSET sentinel
    can NEVER silently pass as "huge free funds, definitely ok" — it is treated as no value,
    and we move on to the fallback (and ultimately fail-open) rather than false-pass.

    Fail-OPEN contract preserved: a missing state (preview unavailable) or missing / zero /
    unparseable / sentinel-only fields are treated as OK — we never false-block a trade on
    absent margin data.
    """
    if state is None:
        return True

    # 1. Real binding constraint: after-trade free funds. Only act when a usable number is
    # present (None => field absent/sentinel => skip to fallback, never a false-pass).
    for field in ("availableFundsAfter", "excessLiquidityAfter"):
        funds_after = _usable_float(getattr(state, field, None))
        if funds_after is not None:
            return funds_after >= 0.0  # negative free funds => insufficient

    # 2. Fallback: init margin must not exceed equity (both sentinel-guarded).
    equity = _usable_float(getattr(state, "equityWithLoanAfter", None))
    init = _usable_float(getattr(state, "initMarginAfter", None))
    if equity is not None and init is not None and equity > 0 and init > 0 and init > equity:
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
    # Parent inherits intent.tif and (for MKT/LMT) the Adaptive algo via build_order.
    parent = build_order(intent)
    parent.orderId = ib.client.getReqId()
    opp = "SELL" if intent.action is Action.BUY else "BUY"
    children = []
    # Protective children get protective_tif (default GTC) so they outlive the
    # session and keep protecting the filled entry overnight. They are built
    # directly here (NOT via build_order/_apply_adaptive) — Adaptive on a STP
    # child is rejected by TWS, so it must never reach them.
    if intent.take_profit is not None:
        tp = LimitOrder(opp, intent.quantity, intent.take_profit)
        tp.parentId = parent.orderId
        tp.orderId = ib.client.getReqId()
        tp.transmit = False
        tp.tif = intent.protective_tif
        children.append(tp)
    if intent.stop_loss is not None:
        sl = StopOrder(opp, intent.quantity, intent.stop_loss)
        sl.parentId = parent.orderId
        sl.orderId = ib.client.getReqId()
        sl.transmit = False
        sl.tif = intent.protective_tif
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

    # 1b. Candidate setup read (fail-soft): one reqHistoricalData on this same socket,
    # then a pure Stage-2/VCP (equity) or trend/vol/location/momentum (futures) assessment.
    bars = fetch_daily_bars(ib, contract, config.setup.history_duration)
    setup = assess_setup(intent.sec_type, intent.action, bars, config.setup)

    # 2. What-if margin check. Real ib.whatIfOrder returns [], [OrderState], or OrderState
    # depending on TWS state (e.g. [] when 'Read-Only API' is on) — normalize defensively.
    whatif = ib.whatIfOrder(contract, order)
    wstate = _order_state(whatif)
    bp_ok = _buying_power_ok(wstate)

    # 3. Reference price + notional
    price = _reference_price(ib, contract, intent)
    notional = _order_notional(intent, contract, price)

    # 4. Hypothetical post-trade snapshot. [C2] Use the SAME live MNQ divisor the daemon
    # uses (live_mnq_notional), falling back to the static config only when unavailable —
    # otherwise the gate and daemon disagree on MNQ-equivalent contracts near the overnight
    # trip (e.g. live ~$61k vs stale $42k config).
    mnq = live_mnq_notional(ib) or config.live.mnq_notional_usd
    hypo = hypothetical_snapshot(
        current,
        intent,
        notional,
        mnq_notional_usd=mnq,
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
        setup=setup,
    )
    verdict = decide(facts)

    # 9. Build the JSON-serializable preview dict
    name_weight_before = current.name_weights.get(intent.symbol, 0.0)
    name_weight_after = hypo.name_weights.get(intent.symbol, 0.0)

    # [MEDIUM] initMarginAfter can be the IBKR UNSET sentinel (~1.79e308) even when
    # a real OrderState came back. Reuse the sentinel/finite guard so we never show
    # the sentinel as a dollar figure — None signals "not available" instead.
    init_margin = _usable_float(getattr(wstate, "initMarginAfter", None)) if wstate is not None else None

    preview: dict = {
        "symbol": intent.symbol,
        "action": intent.action.value,
        "quantity": intent.quantity,
        "order_type": intent.order_type.value,
        "order_notional": notional,
        "pct_nav": sized.pct_nav,
        "buying_power_ok": bp_ok,
        "whatif_available": wstate is not None,
        "init_margin": init_margin,
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
        "setup": setup_to_dict(setup),
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

    # Rendered confirmation panels (ORDER / RISK / SETUP) — pure, no I/O.
    # Added after the preview dict is fully built so all keys are present.
    preview["panels"] = render_panels(preview)

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

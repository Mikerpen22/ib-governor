"""Post-trade state analysis for the pre-trade gate.

Provides `hypothetical_snapshot`: a pure function that returns a NEW
`StateSnapshot` reflecting what the account would look like *if* a given
`OrderIntent` filled at the specified notional.  The rule engine then
evaluates this hypothetical snapshot to decide whether to allow the trade.

Design notes:
- NAV is treated as unchanged — a fill swaps cash for position, leaving NAV
  flat for the purposes of pre-trade risk evaluation.
- Never mutates the input snapshot or any of its dict fields.
- All dict copies are made before any edit (immutability contract).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from governor.config import GateRules
from governor.gate.intent import Action, OrderIntent, SecType
from governor.model import Severity, StateSnapshot, Trip


def hypothetical_snapshot(
    current: StateSnapshot,
    intent: OrderIntent,
    order_notional: float,
    *,
    mnq_notional_usd: float = 0.0,
    sector: str | None = None,
) -> StateSnapshot:
    """Return a new snapshot as if *intent* filled at *order_notional*.

    Args:
        current: The current (real) account snapshot.  Never mutated.
        intent: The trade about to be placed.
        order_notional: Absolute USD notional of the order
            (e.g. qty * price for equities; contract_value for futures).
        mnq_notional_usd: MNQ single-contract notional in USD.  Used to
            convert ``futures_notional`` into a contract count.  Pass 0.0
            (default) to leave ``futures_contracts_overnight`` unchanged.
        sector: The sector string for equity trades.  Defaults to ``"unknown"``
            when *None*.

    Returns:
        A new ``StateSnapshot`` with weights/notional updated to reflect the
        hypothetical fill.  All other fields are preserved unchanged.
    """
    is_buy = intent.action is Action.BUY

    if intent.sec_type is SecType.STK:
        return _apply_stk(current, intent, order_notional, is_buy, sector)

    # SecType.FUT
    return _apply_fut(current, is_buy, order_notional, mnq_notional_usd)


# ---------------------------------------------------------------------------
# Private helpers — keep the main function readable
# ---------------------------------------------------------------------------


def _apply_stk(
    current: StateSnapshot,
    intent: OrderIntent,
    order_notional: float,
    is_buy: bool,
    sector: str | None,
) -> StateSnapshot:
    """Model a STK fill on the SIGNED per-name exposure so concentration tracks
    magnitude (audit H1): BUY grows-a-long / covers-a-short, SELL shrinks-a-long /
    grows-a-short — the affected weights move by |Δ| in the right direction.

    The signed map is the source of truth; ``name_weights`` and the sector bucket carry
    only the absolute magnitude (``abs(new_signed)``).  The sector delta is the *change*
    in this name's magnitude (it can decrease the bucket when covering/reducing).
    """
    sym = intent.symbol
    nav = current.nav
    delta_w = order_notional / nav if nav > 0 else 0.0

    cur_signed = current.name_exposure_signed.get(sym, 0.0)
    new_signed = cur_signed + (delta_w if is_buy else -delta_w)
    new_weight = abs(new_signed)
    weight_change = new_weight - abs(cur_signed)  # signed change in this name's magnitude

    new_name_exposure_signed = dict(current.name_exposure_signed)
    new_name_exposure_signed[sym] = new_signed

    new_name_weights = dict(current.name_weights)
    new_name_weights[sym] = new_weight

    sector_key = sector or "unknown"
    new_sector_weights = dict(current.sector_weights)
    new_sector_weights[sector_key] = max(
        0.0,
        current.sector_weights.get(sector_key, 0.0) + weight_change,
    )

    return replace(
        current,
        name_weights=new_name_weights,
        sector_weights=new_sector_weights,
        name_exposure_signed=new_name_exposure_signed,
    )


def _apply_fut(
    current: StateSnapshot,
    is_buy: bool,
    order_notional: float,
    mnq_notional_usd: float,
) -> StateSnapshot:
    """Model a FUT fill on the SIGNED net notional (audit C1): adding to a SHORT (a SELL
    against a net-short book) must INCREASE exposure, not shrink it.  The old code treated
    the stored *absolute* notional as signed and wrongly netted a SELL down.
    """
    order_signed = order_notional if is_buy else -order_notional
    new_signed = current.futures_notional_signed + order_signed
    new_notional = abs(new_signed)

    if mnq_notional_usd > 0:
        new_contracts = new_notional / mnq_notional_usd
    else:
        new_contracts = current.futures_contracts_overnight

    return replace(
        current,
        futures_notional=new_notional,
        futures_contracts_overnight=new_contracts,
        futures_notional_signed=new_signed,
    )


# ---------------------------------------------------------------------------
# Sizing check — per-trade notional as a fraction of NAV
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SizingCheck:
    pct_nav: float  # order notional as a fraction of NAV
    over_band: bool  # True if it exceeds gate.max_trade_pct_nav


def sizing(order_notional: float, nav: float, cfg: GateRules) -> SizingCheck:
    """Per-trade size as a fraction of NAV, and whether it exceeds the gate band.

    A trade larger than the band is a CAUTION (still allowed on confirm), not a block.
    """
    pct = order_notional / nav if nav > 0 else 0.0
    return SizingCheck(pct_nav=pct, over_band=pct > cfg.max_trade_pct_nav)


# ---------------------------------------------------------------------------
# Gate verdict — pure composition, no I/O
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    GO = "GO"
    CAUTION = "CAUTION"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class GateFacts:
    """The checkable inputs the verdict reasons over.

    Built by the live runner (a later task) from whatIf + the rule engine run
    on the hypothetical post-trade snapshot.
    """

    post_trade_trips: tuple[Trip, ...] = ()  # evaluate() output on the hypothetical snapshot
    lockout_active: bool = False             # an active lockout for this asset class
    sizing: SizingCheck | None = None        # per-trade size vs the NAV band
    buying_power_ok: bool = True             # whatIf shows sufficient buying power


@dataclass(frozen=True)
class GateVerdict:
    level: Verdict
    reasons: tuple[str, ...]


def decide(facts: GateFacts) -> GateVerdict:
    """Compose facts into a verdict. Precedence: BLOCK > CAUTION > GO.

    BLOCK: active lockout, OR any HARD-severity post-trade trip, OR
           insufficient buying power.
    CAUTION: any WARN-severity trip, OR sizing over the band.
    GO: none of the above.
    INFO trips are not surfaced as caution or block.
    ``reasons`` explains every contributing factor.
    """
    block: list[str] = []
    caution: list[str] = []

    if facts.lockout_active:
        block.append("an active lockout blocks new trades in this asset class")
    if not facts.buying_power_ok:
        block.append("insufficient buying power (whatIf)")

    for t in facts.post_trade_trips:
        line = f"{t.rule_id}: {t.message}"
        if t.severity is Severity.HARD:
            block.append(line)
        elif t.severity is Severity.WARN:
            caution.append(line)
        # INFO trips are intentionally not surfaced

    if facts.sizing is not None and facts.sizing.over_band:
        caution.append(
            f"trade is {facts.sizing.pct_nav:.1%} of NAV (over the sizing band)"
        )

    if block:
        return GateVerdict(Verdict.BLOCK, tuple(block))
    if caution:
        return GateVerdict(Verdict.CAUTION, tuple(caution))
    return GateVerdict(Verdict.GO, ())

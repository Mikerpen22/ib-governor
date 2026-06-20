"""VCP contraction-sequence detection (v1 fractal heuristic). Pure.

A Volatility Contraction Pattern is a staircase of progressively shallower
pullbacks into a pivot. We approximate it from daily bars with a fixed-window
fractal swing detector: a bar is a swing high if its high tops the +/-K window
(swing low symmetric). We then pair each swing high with the next swing low to
form a contraction leg, measure the retracement, and read whether the trailing
legs are tightening into the most-recent swing high (the pivot).

No mutation; bars are never modified; the result is a frozen VcpResult.
"""
from __future__ import annotations

from governor.config import EquitySetupRules
from governor.technicals.types import Bar, VcpResult

_K = 5  # fractal window for swing detection (needs >=K bars on each side)


def _swings(bars: list[Bar]) -> list[tuple[int, str, float]]:
    """Return (index, 'H'|'L', price) swing points via a +/-_K fractal window."""
    out: list[tuple[int, str, float]] = []
    for i in range(_K, len(bars) - _K):
        window = bars[i - _K : i + _K + 1]
        if bars[i].high == max(b.high for b in window):
            out.append((i, "H", bars[i].high))
        elif bars[i].low == min(b.low for b in window):
            out.append((i, "L", bars[i].low))
    return out


def _grade(pct: float, cfg: EquitySetupRules) -> str:
    if pct < 0.08:
        return "excellent"
    if pct < 0.12:
        return "good"
    if pct < cfg.contraction_loose_pct:
        return "acceptable"
    return "too_loose"


def _band(distance_pct: float, cfg: EquitySetupRules) -> str:
    if distance_pct < 0:
        return "pre_breakout"
    if distance_pct <= cfg.pivot_extended_pct:
        return "actionable"
    if distance_pct <= 0.10:
        return "extended"
    if distance_pct <= cfg.pivot_too_late_pct:
        return "wait"
    return "too_late"


def compute_vcp(bars: list[Bar], cfg: EquitySetupRules) -> VcpResult:
    swings = _swings(bars)

    # Build (high, low, retracement) legs: each swing High paired with the next
    # swing Low after it. Advance past the low so legs don't overlap.
    legs: list[tuple[float, float, float, int, int]] = []  # high, low, retr, hi_idx, lo_idx
    i = 0
    while i < len(swings) - 1:
        idx_h, kind_h, ph = swings[i]
        if kind_h != "H":
            i += 1
            continue
        j = i + 1
        while j < len(swings) and swings[j][1] != "L":
            j += 1
        if j >= len(swings):
            break
        idx_l, _, pl = swings[j]
        retr = (ph - pl) / ph if ph > 0 else 0.0
        legs.append((ph, pl, retr, idx_h, idx_l))
        i = j

    legs = legs[-4:]
    if len(legs) < 2:
        return VcpResult(available=False)

    retrs = [lg[2] for lg in legs]
    is_contracting = all(retrs[k] <= retrs[k - 1] * 1.1 for k in range(1, len(retrs)))
    pivot = legs[-1][0]
    price = bars[-1].close
    distance_pct = (price - pivot) / pivot if pivot > 0 else 0.0
    last_pct = retrs[-1]

    # volume dry-up: mean volume over the final leg's bars vs the earlier legs'.
    def _leg_vol(lg: tuple[float, float, float, int, int]) -> float:
        a, b = lg[3], lg[4]
        seg = bars[a : b + 1] or [bars[a]]
        return sum(x.volume for x in seg) / len(seg)

    last_vol = _leg_vol(legs[-1])
    earlier_vol = sum(_leg_vol(lg) for lg in legs[:-1]) / max(len(legs) - 1, 1)
    volume_dryup = last_vol < earlier_vol

    return VcpResult(
        available=True,
        contractions=tuple((lg[0], lg[1], lg[2]) for lg in legs),
        is_contracting=is_contracting,
        last_contraction_pct=last_pct,
        last_grade=_grade(last_pct, cfg),
        pivot=pivot,
        distance_pct=distance_pct,
        distance_band=_band(distance_pct, cfg),
        volume_dryup=volume_dryup,
    )

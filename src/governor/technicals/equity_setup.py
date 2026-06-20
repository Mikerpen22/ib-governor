"""Compose Stage-2 + VCP into an equity setup, with the 'poor' (=> CAUTION) flag.

Minervini is a LONG-entry methodology: a BUY is judged on setup quality; a SELL
(exit/trim) is never flagged poor for 'not Stage 2'.
"""
from __future__ import annotations

from governor.config import EquitySetupRules
from governor.gate.intent import Action
from governor.technicals.stage2 import compute_stage2
from governor.technicals.types import Bar, EquitySetup
from governor.technicals.vcp import compute_vcp


def compute_equity_setup(bars: list[Bar], action: Action, cfg: EquitySetupRules) -> EquitySetup:
    stage2 = compute_stage2(bars, cfg)
    vcp = compute_vcp(bars, cfg)
    is_buy = action is Action.BUY
    extended = bool(vcp.available and is_buy and vcp.distance_pct > cfg.pivot_extended_pct)
    loose = bool(vcp.available and vcp.last_grade == "too_loose")
    not_confirmed = stage2.classification != "confirmed"
    poor = bool(is_buy and (not_confirmed or extended or loose))
    return EquitySetup(stage2=stage2, vcp=vcp, extended=extended, poor=poor)

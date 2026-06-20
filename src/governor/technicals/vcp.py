# src/governor/technicals/vcp.py  (Phase-1 stub; real body in Task 11)
from __future__ import annotations
from governor.config import EquitySetupRules
from governor.technicals.types import Bar, VcpResult

def compute_vcp(bars: list[Bar], cfg: EquitySetupRules) -> VcpResult:
    return VcpResult(available=False)

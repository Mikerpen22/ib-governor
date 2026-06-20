"""Top-level setup assessment + JSON serializer for the gate preview.

assess_setup is the single entry point the gate runner calls. It is pure: bars in
(or None), SetupAssessment out. Insufficient history -> available=False, never raises.
"""
from __future__ import annotations

from governor.config import SetupRules
from governor.gate.intent import Action, SecType
from governor.technicals.equity_setup import compute_equity_setup
from governor.technicals.futures_setup import compute_futures_setup
from governor.technicals.types import Bar, EquitySetup, FuturesSetup, SetupAssessment


def _equity_reasons(eq: EquitySetup, action: Action) -> tuple[str, ...]:
    if action is not Action.BUY:
        return ()
    out: list[str] = []
    if eq.stage2.classification != "confirmed":
        out.append(f"setup: not a confirmed Stage 2 ({eq.stage2.pass_count}/7)")
    if eq.extended:
        out.append(f"setup: entry {eq.vcp.distance_pct:+.0%} past pivot (extended)")
    if eq.vcp.available and eq.vcp.last_grade == "too_loose":
        out.append(f"setup: last contraction {eq.vcp.last_contraction_pct:.0%} (too loose)")
    return tuple(out)


def _futures_reasons(fs: FuturesSetup) -> tuple[str, ...]:
    out: list[str] = []
    if fs.counter_trend:
        out.append(f"setup: counter-trend ({fs.trend_label})")
    if fs.chasing:
        out.append("setup: chasing — at the recent range extreme")
    if fs.vol_elevated:
        out.append(f"setup: elevated volatility (ATR {fs.atr_pctile:.0%} pct)")
    return tuple(out)


def assess_setup(sec_type: SecType, action: Action, bars: list[Bar] | None,
                 setup_cfg: SetupRules) -> SetupAssessment:
    asset_class = "equity" if sec_type is SecType.STK else "future"
    if bars is None or len(bars) < setup_cfg.min_bars:
        return SetupAssessment(available=False, asset_class=asset_class, poor=False,
                               caution_reasons=())
    if sec_type is SecType.STK:
        eq = compute_equity_setup(bars, action, setup_cfg.equities)
        return SetupAssessment(available=True, asset_class="equity", poor=eq.poor,
                               caution_reasons=_equity_reasons(eq, action), equity=eq)
    fs = compute_futures_setup(bars, action, setup_cfg.futures)
    return SetupAssessment(available=True, asset_class="future", poor=fs.poor,
                           caution_reasons=_futures_reasons(fs), futures=fs)


def setup_to_dict(s: SetupAssessment) -> dict:
    """JSON-serializable view for the gate preview + renderer. Flat primitives only."""
    d: dict = {
        "available": s.available,
        "asset_class": s.asset_class,
        "poor": s.poor,
        "caution_reasons": list(s.caution_reasons),
    }
    if s.equity is not None:
        e = s.equity
        d["equity"] = {
            "stage2": {
                "classification": e.stage2.classification,
                "pass_count": e.stage2.pass_count,
                "price": e.stage2.price,
                "ma50": e.stage2.ma50, "ma150": e.stage2.ma150, "ma200": e.stage2.ma200,
                "slope_up": e.stage2.slope_up,
                "position_pct": e.stage2.position_pct,
                "range_ratio": e.stage2.range_ratio,
                "criteria": [list(c) for c in e.stage2.criteria],
            },
            "vcp": {
                "available": e.vcp.available, "pivot": e.vcp.pivot,
                "distance_pct": e.vcp.distance_pct, "distance_band": e.vcp.distance_band,
                "last_contraction_pct": e.vcp.last_contraction_pct,
                "last_grade": e.vcp.last_grade, "volume_dryup": e.vcp.volume_dryup,
                "contractions": [list(c) for c in e.vcp.contractions],
            },
            "extended": e.extended,
        }
    if s.futures is not None:
        f = s.futures
        d["futures"] = {
            "with_trend": f.with_trend, "counter_trend": f.counter_trend,
            "trend_label": f.trend_label, "atr": f.atr, "atr_pctile": f.atr_pctile,
            "vol_label": f.vol_label, "vol_expanding": f.vol_expanding,
            "vol_elevated": f.vol_elevated, "dist_from_high_pct": f.dist_from_high_pct,
            "dist_from_low_pct": f.dist_from_low_pct, "chasing": f.chasing,
            "rsi": f.rsi, "roc": f.roc, "momentum_label": f.momentum_label,
        }
    return d

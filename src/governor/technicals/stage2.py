"""Minervini Stage-2 confirmation: a pure 7-point checklist over daily bars."""
from __future__ import annotations

from governor.config import EquitySetupRules
from governor.technicals.indicators import rolling_sma, sma, slope_up
from governor.technicals.types import Bar, Stage2Result


def compute_stage2(bars: list[Bar], cfg: EquitySetupRules) -> Stage2Result:
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    price = closes[-1]
    ma50, ma150, ma200 = sma(closes, 50), sma(closes, 150), sma(closes, 200)
    high52, low52 = max(highs), min(lows)
    rng = high52 - low52
    position_pct = (price - low52) / rng if rng > 0 else 0.0
    range_ratio = high52 / low52 if low52 > 0 else 0.0
    ma200_series = rolling_sma(closes, 200)
    slope = slope_up(ma200_series, cfg.ma200_slope_lookback)

    criteria: list[tuple[str, bool]] = [
        ("price > MA50", ma50 is not None and price > ma50),
        ("price > MA150", ma150 is not None and price > ma150),
        ("price > MA200", ma200 is not None and price > ma200),
        ("MA50>MA150>MA200", None not in (ma50, ma150, ma200) and ma50 > ma150 > ma200),
        ("MA200 rising", slope),
        ("52wk position", position_pct >= cfg.high_proximity_pct),
        ("range >= ratio", range_ratio >= cfg.min_range_ratio),
    ]
    pass_count = sum(1 for _, ok in criteria if ok)
    if pass_count >= cfg.stage2_confirmed_min:
        classification = "confirmed"
    elif pass_count >= cfg.stage2_candidate_min:
        classification = "candidate"
    else:
        classification = "none"

    return Stage2Result(
        price=price, ma50=ma50, ma150=ma150, ma200=ma200, slope_up=slope,
        position_pct=position_pct, range_ratio=range_ratio, high52=high52, low52=low52,
        criteria=tuple(criteria), pass_count=pass_count, classification=classification,
    )

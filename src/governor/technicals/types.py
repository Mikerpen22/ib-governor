"""Frozen result types for the technical setup read. Pure data, no behavior."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Stage2Result:
    price: float
    ma50: float | None
    ma150: float | None
    ma200: float | None
    slope_up: bool
    position_pct: float       # (price-low52)/(high52-low52), 0..1
    range_ratio: float        # high52/low52
    high52: float
    low52: float
    criteria: tuple[tuple[str, bool], ...]   # (label, passed)
    pass_count: int
    classification: str       # "confirmed" | "candidate" | "none"


@dataclass(frozen=True)
class VcpResult:
    available: bool
    contractions: tuple[tuple[float, float, float], ...] = ()  # (high, low, retracement_pct)
    is_contracting: bool = False
    last_contraction_pct: float = 0.0
    last_grade: str = "n/a"   # "excellent"|"good"|"acceptable"|"too_loose"|"n/a"
    pivot: float = 0.0
    distance_pct: float = 0.0  # (price-pivot)/pivot
    distance_band: str = "n/a" # "actionable"|"extended"|"wait"|"too_late"|"pre_breakout"|"n/a"
    volume_dryup: bool = False


@dataclass(frozen=True)
class EquitySetup:
    stage2: Stage2Result
    vcp: VcpResult
    extended: bool
    poor: bool


@dataclass(frozen=True)
class FuturesSetup:
    with_trend: bool
    counter_trend: bool
    trend_label: str
    atr: float | None
    atr_pctile: float
    vol_label: str            # "elevated"|"normal"|"compressed"
    vol_expanding: bool
    vol_elevated: bool
    dist_from_high_pct: float
    dist_from_low_pct: float
    chasing: bool
    rsi: float | None
    roc: float | None
    momentum_label: str       # "overbought"|"oversold"|"neutral"
    poor: bool


@dataclass(frozen=True)
class SetupAssessment:
    available: bool
    asset_class: str          # "equity" | "future"
    poor: bool
    caution_reasons: tuple[str, ...]
    equity: EquitySetup | None = None
    futures: FuturesSetup | None = None

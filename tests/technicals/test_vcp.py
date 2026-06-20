"""VCP fractal detector (Phase 4 / Task 11).

The synthetic series here are built deliberately so the +/-K=5 fractal window
actually resolves each turning point into a single clean swing. We build a
piecewise-linear close path between named price levels, with >=11 bars per leg
so every peak/trough clears the 5-bars-each-side separation the detector needs.

A canonical VCP staircase: rise to 100, pull back 30% to 70, rise to 105, pull
back ~15% to 89.25, rise to 108, pull back ~7% to 100.44, then drift toward the
108 pivot. That yields three decreasing contractions (0.30 -> 0.15 -> 0.07) with
rising pivots -- the real path we want to assert against, not a tent we hope
trips something.
"""
from governor.config import EquitySetupRules
from governor.technicals.vcp import compute_vcp
from governor.technicals.types import Bar

# Swing prices come off the bar HIGH (= close + 0.2 below), so a swing high at
# close level L is detected at price L + _HIGH_OFFSET.
_HIGH_OFFSET = 0.2


def _bar(i: int, close: float, vol: float = 100.0) -> Bar:
    return Bar(date=str(i), open=close, high=close + 0.2, low=close - 0.2,
               close=close, volume=vol)


def _ramp(start: float, end: float, n: int) -> list[float]:
    """n linearly-spaced points from start to end (inclusive)."""
    if n <= 1:
        return [end]
    step = (end - start) / (n - 1)
    return [start + step * k for k in range(n)]


def _build(segments: list[tuple[float, int]],
           seg_vols: list[float] | None = None) -> list[Bar]:
    """Piecewise-linear close path.

    segments: list of (level, n_bars). The first entry seeds the starting level
    (its n_bars is ignored); each subsequent (level, n_bars) ramps from the
    previous level to `level` over `n_bars` bars. seg_vols, if given, assigns a
    volume to every bar produced by that segment index.
    """
    closes: list[float] = [segments[0][0]]
    seg_of_close: list[int] = [0]
    prev = segments[0][0]
    for si, (level, nb) in enumerate(segments[1:], start=1):
        leg = _ramp(prev, level, nb + 1)[1:]  # drop duplicated start point
        closes.extend(leg)
        seg_of_close.extend([si] * len(leg))
        prev = level
    bars: list[Bar] = []
    for i, c in enumerate(closes):
        vol = seg_vols[seg_of_close[i]] if seg_vols is not None else 100.0
        bars.append(_bar(i, float(c), float(vol)))
    return bars


# Segment indices: 0 start | 1 up->P1 | 2 down(L1) | 3 up->P2 | 4 down(L2)
#                  5 up->P3 | 6 down(L3, the *last* leg) | 7 final drift
_VCP_SEGMENTS: list[tuple[float, int]] = [
    (40.0, 1),
    (100.0, 30),     # P1 = 100
    (70.0, 20),      # L1: (100-70)/100   = 0.30
    (105.0, 25),     # P2 = 105
    (89.25, 18),     # L2: (105-89.25)/105 = 0.15
    (108.0, 20),     # P3 = 108  (the pivot)
    (100.44, 15),    # L3: (108-100.44)/108 = 0.07  -> grade "excellent"
    (106.0, 12),     # drift up under the pivot -> price below pivot
]


def _vcp_bars(final_level: float = 106.0,
              seg_vols: list[float] | None = None) -> list[Bar]:
    segs = _VCP_SEGMENTS[:-1] + [(final_level, 12)]
    return _build(segs, seg_vols)


def test_unavailable_when_flat():
    flat = [_bar(i, 100.0) for i in range(60)]
    r = compute_vcp(flat, EquitySetupRules())
    assert r.available is False
    assert r.contractions == ()
    assert r.is_contracting is False


def test_unavailable_when_monotonic_uptrend():
    # A pure staircase up has no pullback swings -> no contraction legs.
    mono = [_bar(i, 50.0 + i * 0.5) for i in range(150)]
    assert compute_vcp(mono, EquitySetupRules()).available is False


def test_detects_three_decreasing_contractions():
    bars = _vcp_bars()
    r = compute_vcp(bars, EquitySetupRules())

    assert r.available is True
    assert r.is_contracting is True
    assert len(r.contractions) == 3

    retrs = [retr for _, _, retr in r.contractions]
    # Strictly decreasing 0.30 -> 0.15 -> 0.07.
    assert retrs[0] > retrs[1] > retrs[2]
    assert abs(retrs[0] - 0.30) < 0.02
    assert abs(retrs[1] - 0.15) < 0.02
    assert abs(retrs[2] - 0.07) < 0.02

    # Final leg ~7.4% retracement -> "excellent" (< 8%).
    assert abs(r.last_contraction_pct - retrs[2]) < 1e-9
    assert r.last_grade == "excellent"

    # Pivot is the most-recent swing high (P3=108, detected off the bar high).
    assert abs(r.pivot - (108.0 + _HIGH_OFFSET)) < 1e-6

    # Drift ends at 106, below the 108.2 pivot -> negative distance, pre_breakout.
    assert r.distance_pct < 0
    assert r.distance_band == "pre_breakout"


def test_distance_band_actionable_just_above_pivot():
    # Final drift ends at 110: (110-108.2)/108.2 ~= +1.7% -> within 5% -> actionable.
    r = compute_vcp(_vcp_bars(final_level=110.0), EquitySetupRules())
    assert r.available is True
    assert 0 < r.distance_pct <= EquitySetupRules().pivot_extended_pct
    assert r.distance_band == "actionable"


def test_distance_band_extended_when_stretched_past_pivot():
    # Final drift ends at 115: ~+6.3% past the pivot -> extended (>5%, <=10%).
    r = compute_vcp(_vcp_bars(final_level=115.0), EquitySetupRules())
    assert r.distance_band == "extended"
    assert r.distance_pct > EquitySetupRules().pivot_extended_pct


def test_distance_band_is_a_known_label():
    r = compute_vcp(_vcp_bars(), EquitySetupRules())
    assert r.distance_band in {
        "actionable", "extended", "wait", "too_late", "pre_breakout", "n/a",
    }


def test_volume_dryup_true_when_late_legs_lighter():
    # Heavy volume on the early legs, light on the final contraction.
    seg_vols = [100, 300, 280, 250, 230, 120, 90, 90]
    r = compute_vcp(_vcp_bars(seg_vols=seg_vols), EquitySetupRules())
    assert r.available is True
    assert r.volume_dryup is True


def test_volume_dryup_false_when_late_legs_heavier():
    # Same price structure, volume rising into the final leg -> no dry-up.
    seg_vols = [100, 90, 90, 120, 130, 260, 300, 300]
    r = compute_vcp(_vcp_bars(seg_vols=seg_vols), EquitySetupRules())
    assert r.available is True
    assert r.volume_dryup is False


def test_two_legs_minimum_is_available():
    # Exactly two contractions (rise, pull back 30%, rise, pull back ~15%, drift).
    segs = [
        (40.0, 1),
        (100.0, 30),    # P1
        (70.0, 20),     # L1 = 0.30
        (105.0, 25),    # P2 (pivot)
        (89.25, 18),    # L2 = 0.15
        (103.0, 12),    # drift below the 105 pivot
    ]
    r = compute_vcp(_build(segs), EquitySetupRules())
    assert r.available is True
    assert len(r.contractions) == 2
    assert r.is_contracting is True
    assert abs(r.pivot - (105.0 + _HIGH_OFFSET)) < 1e-6

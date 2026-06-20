# Pre-Trade Deep Setup Read — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a systematic, IBKR-bar-driven setup-quality read (Minervini Stage 2 + VCP for equities; trend/vol/location/momentum for futures) to the pre-trade gate, escalating poor setups to CAUTION, and render a beautiful decision-first confirmation screen — all on the gate's existing single connection.

**Architecture:** The gate stays "facts in → verdict out". The setup read is one more pure-computation fact (`technicals/`), fed by one more I/O call (`reqHistoricalData` in `analyze_intent`), surfaced through `GateFacts.setup` and one new CAUTION clause in `decide()`, and rendered by a pure `gate/render.py`. Thresholds are config-driven (`SetupRules`); the bar fetch is fail-soft so the safety-critical risk gate is never blocked by a setup failure.

**Tech Stack:** Python 3.12, `ib_async`, pydantic v2, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-19-pre-trade-deep-setup-design.md`

**Conventions (read before starting):**
- `pyproject.toml` sets `pythonpath = ["src"]`; run `pytest -q` from the repo root.
- Everything in `technicals/` is **pure** — no `ib_async` import, no I/O, no mutation (frozen dataclasses; build new objects).
- Match existing style: small focused files, `from __future__ import annotations`, module-level `log = logging.getLogger("governor.<area>")`.
- Commit after each green task. Conventional commits (`feat:`/`test:`/`docs:`), NO attribution trailer.

---

## File Structure

```
src/governor/technicals/            NEW — pure compute, no IBKR
  __init__.py
  types.py          frozen results: Bar, Stage2Result, VcpResult, EquitySetup, FuturesSetup, SetupAssessment
  indicators.py     sma, rolling_sma, slope_up, true_ranges, atr, rolling_atr, rsi, roc, pct_from_high/low, percentile_rank
  stage2.py         compute_stage2(bars, cfg) -> Stage2Result
  vcp.py            compute_vcp(bars, cfg) -> VcpResult         (Phase 4)
  equity_setup.py   compute_equity_setup(bars, action, cfg) -> EquitySetup
  futures_setup.py  compute_futures_setup(bars, action, cfg) -> FuturesSetup
  assess.py         assess_setup(sec_type, action, bars, setup_cfg) -> SetupAssessment; setup_to_dict(s) -> dict
src/governor/live/history.py        NEW — fetch_daily_bars(ib, contract, duration) -> list[Bar] | None  (fail-soft)
src/governor/gate/render.py         NEW — render_panels(preview: dict) -> str  (pure)

MODIFY:
src/governor/config.py              add EquitySetupRules, FuturesSetupRules, SetupRules; RulesConfig.setup
src/governor/gate/analysis.py       GateFacts.setup field; decide() setup-CAUTION clause
src/governor/gate/runner.py         analyze_intent: fetch bars + assess + thread into GateFacts + preview
config/rules.yaml                   document the new setup: block (ships SAFE; no behavior change)
skills/pre-trade-equities/SKILL.md  Step 4 -> "Setup (Minervini)"; Step 6 -> banner + panels
skills/pre-trade-futures/SKILL.md   add futures setup section + panels
docs/FORCLAUDE.md, docs/HANDBOOK.md refresh
```

---

# Phase 1 — `technicals/` core (pure, no IBKR)

### Task 1: Result types

**Files:**
- Create: `src/governor/technicals/__init__.py` (empty)
- Create: `src/governor/technicals/types.py`
- Test: `tests/technicals/test_types.py` (create `tests/technicals/__init__.py` empty too)

- [ ] **Step 1: Write the failing test**

```python
# tests/technicals/test_types.py
import pytest
from governor.technicals.types import (
    Bar, Stage2Result, VcpResult, EquitySetup, FuturesSetup, SetupAssessment,
)

def test_bar_is_frozen():
    b = Bar(date="2026-06-19", open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
    assert b.close == 1.5
    with pytest.raises(Exception):
        b.close = 9.0  # frozen

def test_setup_assessment_unavailable_default():
    s = SetupAssessment(available=False, asset_class="equity", poor=False,
                        caution_reasons=(), equity=None, futures=None)
    assert s.available is False and s.poor is False and s.caution_reasons == ()
```

- [ ] **Step 2: Run — expect FAIL** (`pytest tests/technicals/test_types.py -q`) — "No module named governor.technicals".

- [ ] **Step 3: Implement**

```python
# src/governor/technicals/types.py
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
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git add src/governor/technicals tests/technicals && git commit -m "feat: technicals result types"`

---

### Task 2: Indicators

**Files:**
- Create: `src/governor/technicals/indicators.py`
- Test: `tests/technicals/test_indicators.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/technicals/test_indicators.py
from governor.technicals.indicators import (
    sma, rolling_sma, slope_up, atr, rsi, roc, pct_from_high, pct_from_low, percentile_rank,
)
from governor.technicals.types import Bar

def _bars(closes):
    # flat-range bars; high/low straddle close by 1 so TR is well-defined
    return [Bar(date=str(i), open=c, high=c + 1, low=c - 1, close=c, volume=100.0)
            for i, c in enumerate(closes)]

def test_sma_last_n():
    assert sma([1, 2, 3, 4, 5], 3) == 4.0          # (3+4+5)/3
    assert sma([1, 2], 3) is None                   # too short

def test_rolling_sma_len_and_values():
    s = rolling_sma([1, 2, 3, 4], 2)
    assert s == [1.5, 2.5, 3.5]

def test_slope_up():
    assert slope_up([1, 2, 3, 4], 2) is True        # 4 > 2
    assert slope_up([4, 3, 2, 1], 2) is False
    assert slope_up([1, 2], 5) is False             # too short

def test_rsi_extremes():
    assert rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], 14) == 100.0
    assert rsi(list(range(16, 0, -1)), 14) == 0.0

def test_roc():
    assert roc([100, 110], 1) == 0.10

def test_pct_from_high_low():
    assert pct_from_high(90, 100) == -0.10
    assert pct_from_low(110, 100) == 0.10

def test_percentile_rank():
    assert percentile_rank([1, 2, 3, 4], 3) == 0.75   # 3 of 4 <= 3
    assert percentile_rank([], 5) == 0.0

def test_atr_simple():
    # each TR = 2 (high-low), so ATR = 2
    bars = _bars([10, 10, 10, 10, 10])
    assert atr(bars, 3) == 2.0
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# src/governor/technicals/indicators.py
"""Pure technical indicators over plain float series / Bar lists.

ATR/RSI use simple (SMA) and Wilder smoothing respectively. All functions return
None when there is insufficient data rather than raising — the caller decides what
"not enough history" means. No mutation; inputs are never modified.
"""
from __future__ import annotations

from governor.technicals.types import Bar


def sma(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def rolling_sma(values: list[float], n: int) -> list[float]:
    if n <= 0 or len(values) < n:
        return []
    return [sum(values[i - n + 1 : i + 1]) / n for i in range(n - 1, len(values))]


def slope_up(values: list[float], lookback: int) -> bool:
    if lookback <= 0 or len(values) <= lookback:
        return False
    return values[-1] > values[-1 - lookback]


def pct_from_high(price: float, high: float) -> float:
    return (price - high) / high if high > 0 else 0.0


def pct_from_low(price: float, low: float) -> float:
    return (price - low) / low if low > 0 else 0.0


def percentile_rank(values: list[float], x: float) -> float:
    """Fraction of *values* <= x, in [0, 1]. Empty -> 0.0."""
    if not values:
        return 0.0
    return sum(1 for v in values if v <= x) / len(values)


def true_ranges(bars: list[Bar]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def atr(bars: list[Bar], n: int) -> float | None:
    trs = true_ranges(bars)
    if n <= 0 or len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def rolling_atr(bars: list[Bar], n: int) -> list[float]:
    return rolling_sma(true_ranges(bars), n)


def rsi(closes: list[float], n: int) -> float | None:
    """Wilder's RSI. All-gains -> 100.0, all-losses -> 0.0."""
    if n <= 0 or len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def roc(closes: list[float], n: int) -> float | None:
    if n <= 0 or len(closes) < n + 1 or closes[-1 - n] == 0:
        return None
    return closes[-1] / closes[-1 - n] - 1.0
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: technical indicators (sma/atr/rsi/roc/percentile)"`

---

### Task 3: Stage 2 (Minervini 7-point)

**Files:**
- Create: `src/governor/technicals/stage2.py`
- Test: `tests/technicals/test_stage2.py`

**Config dependency (implement the full config now):** `compute_stage2` takes an `EquitySetupRules`. Implement the **entire `SetupRules` config in this task** — the three pydantic models (`EquitySetupRules`, `FuturesSetupRules`, `SetupRules`) AND the `RulesConfig.setup` field — exactly as written in **Task 8, Step 3**. Everything from Phase 2 onward reads `config.setup`, so it must exist from Phase 1. Task 8 later adds only the `config/rules.yaml` docs + a dedicated config test. Tests here import `from governor.config import EquitySetupRules`.

- [ ] **Step 1: Write the failing test**

```python
# tests/technicals/test_stage2.py
from governor.config import EquitySetupRules
from governor.technicals.stage2 import compute_stage2
from governor.technicals.types import Bar

def _trend_bars(n, start, step, spread=1.0):
    bars = []
    for i in range(n):
        c = start + i * step
        bars.append(Bar(date=str(i), open=c, high=c + spread, low=c - spread, close=c, volume=100.0))
    return bars

def test_clean_uptrend_is_confirmed():
    # 260 bars rising 50 -> ~180: price above all MAs, stacked, rising, near highs
    bars = _trend_bars(260, 50.0, 0.5)
    r = compute_stage2(bars, EquitySetupRules())
    assert r.classification == "confirmed"
    assert r.pass_count >= 6
    assert r.slope_up is True

def test_downtrend_is_not_stage2():
    bars = _trend_bars(260, 180.0, -0.5)
    r = compute_stage2(bars, EquitySetupRules())
    assert r.classification == "none"
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# src/governor/technicals/stage2.py
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
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: Minervini Stage-2 checklist"`

---

### Task 4: Futures setup (trend / vol / location / momentum)

**Files:**
- Create: `src/governor/technicals/futures_setup.py`
- Test: `tests/technicals/test_futures_setup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/technicals/test_futures_setup.py
from governor.config import FuturesSetupRules
from governor.gate.intent import Action
from governor.technicals.futures_setup import compute_futures_setup
from governor.technicals.types import Bar

def _trend_bars(n, start, step, spread=2.0):
    return [Bar(date=str(i), open=start + i * step, high=start + i * step + spread,
                low=start + i * step - spread, close=start + i * step, volume=100.0)
            for i in range(n)]

def test_long_into_uptrend_is_with_trend():
    bars = _trend_bars(260, 100.0, 1.0)
    fs = compute_futures_setup(bars, Action.BUY, FuturesSetupRules())
    assert fs.with_trend is True and fs.counter_trend is False

def test_short_into_uptrend_is_counter_trend_and_poor():
    bars = _trend_bars(260, 100.0, 1.0)
    fs = compute_futures_setup(bars, Action.SELL, FuturesSetupRules())
    assert fs.counter_trend is True
    assert fs.poor is True   # counter-trend alone -> poor

def test_buying_at_20d_high_is_chasing():
    bars = _trend_bars(260, 100.0, 1.0)  # last close is the highest -> at the 20d high
    fs = compute_futures_setup(bars, Action.BUY, FuturesSetupRules())
    assert fs.chasing is True
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# src/governor/technicals/futures_setup.py
"""Futures setup-quality read: trend alignment, volatility regime, location, momentum.

VCP/Stage-2 is a stock methodology; futures get a four-factor read off the same bars.
Pure — direction comes from the order Action, thresholds from FuturesSetupRules.
"""
from __future__ import annotations

from governor.config import FuturesSetupRules
from governor.gate.intent import Action
from governor.technicals.indicators import atr, percentile_rank, roc, rolling_atr, rsi, sma
from governor.technicals.types import Bar, FuturesSetup

_VOL_EXPAND_LOOKBACK = 5  # bars; ATR now vs ATR a week ago -> expanding


def compute_futures_setup(bars: list[Bar], action: Action, cfg: FuturesSetupRules) -> FuturesSetup:
    closes = [b.close for b in bars]
    price = closes[-1]
    is_long = action is Action.BUY

    # --- Trend alignment ---
    ma_f, ma_m, ma_s = sma(closes, cfg.ma_fast), sma(closes, cfg.ma_mid), sma(closes, cfg.ma_slow)
    have_mas = None not in (ma_f, ma_m, ma_s)
    bullish = have_mas and price > ma_f > ma_m > ma_s
    bearish = have_mas and price < ma_f < ma_m < ma_s
    with_trend = (is_long and bullish) or (not is_long and bearish)
    counter_trend = (is_long and bearish) or (not is_long and bullish)
    trend_label = "uptrend" if bullish else "downtrend" if bearish else "mixed"

    # --- Volatility regime ---
    a = atr(bars, cfg.atr_period)
    atr_series = rolling_atr(bars, cfg.atr_period)
    window = atr_series[-cfg.atr_lookback:]
    atr_pctile = percentile_rank(window, a) if (a is not None and window) else 0.0
    vol_elevated = atr_pctile > cfg.atr_elevated_pctile
    vol_compressed = atr_pctile < cfg.atr_compressed_pctile
    vol_label = "elevated" if vol_elevated else "compressed" if vol_compressed else "normal"
    vol_expanding = (
        len(atr_series) > _VOL_EXPAND_LOOKBACK
        and atr_series[-1] > atr_series[-1 - _VOL_EXPAND_LOOKBACK]
    )

    # --- Location / extension (20-day range) ---
    win = bars[-cfg.range_lookback:]
    hi = max(b.high for b in win)
    lo = min(b.low for b in win)
    dist_from_high_pct = (price - hi) / hi if hi > 0 else 0.0
    dist_from_low_pct = (price - lo) / lo if lo > 0 else 0.0
    if is_long:
        chasing = price >= hi * (1 - cfg.extension_chase_pct)
    else:
        chasing = price <= lo * (1 + cfg.extension_chase_pct)

    # --- Momentum ---
    r = rsi(closes, cfg.rsi_period)
    rc = roc(closes, cfg.rsi_period)
    if r is None:
        momentum_label = "n/a"
    elif r >= cfg.rsi_overbought:
        momentum_label = "overbought"
    elif r <= cfg.rsi_oversold:
        momentum_label = "oversold"
    else:
        momentum_label = "neutral"

    poor = counter_trend or chasing or vol_elevated

    return FuturesSetup(
        with_trend=with_trend, counter_trend=counter_trend, trend_label=trend_label,
        atr=a, atr_pctile=atr_pctile, vol_label=vol_label, vol_expanding=vol_expanding,
        vol_elevated=vol_elevated, dist_from_high_pct=dist_from_high_pct,
        dist_from_low_pct=dist_from_low_pct, chasing=chasing, rsi=r, roc=rc,
        momentum_label=momentum_label, poor=poor,
    )
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: futures setup read (trend/vol/location/momentum)"`

---

### Task 5: Equity setup composer + assess + serializer

**Files:**
- Create: `src/governor/technicals/equity_setup.py`
- Create: `src/governor/technicals/assess.py`
- Test: `tests/technicals/test_assess.py`

**Phase-1 VCP stub:** `compute_equity_setup` imports `compute_vcp` from `vcp.py` (Task 11). For Phase 1, create `vcp.py` now with a stub that returns `VcpResult(available=False)` so equities run on Stage-2 only; Task 11 replaces the body. Create:

```python
# src/governor/technicals/vcp.py  (Phase-1 stub; real body in Task 11)
from __future__ import annotations
from governor.config import EquitySetupRules
from governor.technicals.types import Bar, VcpResult

def compute_vcp(bars: list[Bar], cfg: EquitySetupRules) -> VcpResult:
    return VcpResult(available=False)
```

- [ ] **Step 1: Write the failing test**

```python
# tests/technicals/test_assess.py
from governor.config import SetupRules
from governor.gate.intent import Action, SecType
from governor.technicals.assess import assess_setup, setup_to_dict
from governor.technicals.types import Bar

def _trend_bars(n, start, step, spread=1.0):
    return [Bar(date=str(i), open=start + i * step, high=start + i * step + spread,
                low=start + i * step - spread, close=start + i * step, volume=100.0)
            for i in range(n)]

def test_short_history_unavailable():
    s = assess_setup(SecType.STK, Action.BUY, _trend_bars(50, 10, 1), SetupRules())
    assert s.available is False and s.poor is False

def test_none_bars_unavailable():
    s = assess_setup(SecType.STK, Action.BUY, None, SetupRules())
    assert s.available is False

def test_buy_confirmed_uptrend_not_poor():
    s = assess_setup(SecType.STK, Action.BUY, _trend_bars(260, 50, 0.5), SetupRules())
    assert s.available is True and s.asset_class == "equity"
    assert s.poor is False     # confirmed Stage 2, VCP stubbed unavailable

def test_buy_downtrend_is_poor_with_reason():
    s = assess_setup(SecType.STK, Action.BUY, _trend_bars(260, 180, -0.5), SetupRules())
    assert s.poor is True
    assert any("Stage 2" in r for r in s.caution_reasons)

def test_equity_sell_never_poor():
    # an exit/trim is not judged on buy-setup quality
    s = assess_setup(SecType.STK, Action.SELL, _trend_bars(260, 180, -0.5), SetupRules())
    assert s.poor is False

def test_setup_to_dict_roundtrips_keys():
    s = assess_setup(SecType.STK, Action.BUY, _trend_bars(260, 50, 0.5), SetupRules())
    d = setup_to_dict(s)
    assert d["available"] is True and d["asset_class"] == "equity" and "equity" in d
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# src/governor/technicals/equity_setup.py
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
```

```python
# src/governor/technicals/assess.py
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
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: equity setup composer + assess_setup + serializer"`

---

# Phase 2 — Bar fetch + wire into the gate (fail-soft, no verdict change yet)

### Task 6: `live/history.py`

**Files:**
- Create: `src/governor/live/history.py`
- Test: `tests/live/test_history.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/live/test_history.py
from types import SimpleNamespace
from governor.live.history import fetch_daily_bars
from governor.technicals.types import Bar

class _FakeIB:
    def __init__(self, bars=None, raise_exc=False):
        self._bars, self._raise = bars, raise_exc
    def reqHistoricalData(self, *a, **k):
        if self._raise:
            raise RuntimeError("hist timeout")
        return self._bars

def _raw(n):
    return [SimpleNamespace(date=f"2026-01-{i+1:02d}", open=1.0, high=2.0, low=0.5,
                            close=1.5, volume=100.0) for i in range(n)]

def test_returns_bars():
    out = fetch_daily_bars(_FakeIB(_raw(3)), object(), "1 Y")
    assert len(out) == 3 and isinstance(out[0], Bar) and out[0].close == 1.5

def test_failsoft_on_exception_returns_none():
    assert fetch_daily_bars(_FakeIB(raise_exc=True), object(), "1 Y") is None

def test_empty_returns_none():
    assert fetch_daily_bars(_FakeIB([]), object(), "1 Y") is None
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# src/governor/live/history.py
"""Fail-soft daily-bar fetch for the candidate symbol, on the gate's existing socket.

Mirrors live/daily.py::_fetch_daily_bars: reqHistoricalData (EOD bars are broadly
entitled even without live market data) wrapped so ANY error yields None — the setup
read degrades to 'unavailable', never sinking the risk gate.
"""
from __future__ import annotations

import logging

from governor.technicals.types import Bar

log = logging.getLogger("governor.live.history")


def fetch_daily_bars(ib, contract, duration: str, *, what_to_show: str = "TRADES",
                     use_rth: bool = True) -> list[Bar] | None:
    try:
        raw = ib.reqHistoricalData(
            contract, endDateTime="", durationStr=duration,
            barSizeSetting="1 day", whatToShow=what_to_show, useRTH=use_rth,
        ) or []
    except Exception:  # noqa: BLE001 — setup is best-effort; a fetch failure must not sink the gate
        log.warning("setup: historical bars unavailable for %r", contract, exc_info=True)
        return None
    bars: list[Bar] = []
    for b in raw:
        try:
            bars.append(Bar(
                date=str(getattr(b, "date", "")),
                open=float(b.open), high=float(b.high), low=float(b.low),
                close=float(b.close), volume=float(getattr(b, "volume", 0.0) or 0.0),
            ))
        except (TypeError, ValueError):
            continue
    return bars or None
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: fail-soft candidate bar fetch (live/history)"`

---

### Task 7: Fetch + assess inside `analyze_intent`; add `setup` to preview

**Files:**
- Modify: `src/governor/gate/runner.py` (imports near top; new step between current step 1 (qualify) and step 2 (whatIf); preview dict near line 407)
- Test: `tests/gate/test_runner_setup.py`

**Depends on the `SetupRules` config implemented in Task 3.** It reads `config.setup.history_duration`.

- [ ] **Step 1: Write the failing test** — verify the preview carries a `setup` block, using a fake `ib` whose `reqHistoricalData` returns a known uptrend.

```python
# tests/gate/test_runner_setup.py
# Reuse the gate runner harness pattern from tests/gate/test_runner.py (fake ib).
# The key new assertion:
def test_preview_includes_setup(monkeypatch):
    # ... build fake ib that also implements reqHistoricalData(...) -> ~260 rising bars ...
    # verdict, preview = analyze_intent(ib, intent_buy_stk, current, config, lockout_store, now=now)
    assert "setup" in preview
    assert preview["setup"]["available"] is True
    assert preview["setup"]["asset_class"] == "equity"
```

(Implementer: model the fake `ib` on the existing `tests/gate/` fakes — add a `reqHistoricalData` method returning `SimpleNamespace` bars like Task 6. If no gate-runner fake exists, build a minimal one: methods `qualifyContracts`, `whatIfOrder`, `reqTickers`, `reqHistoricalData`, `client.getReqId`.)

- [ ] **Step 2: Run — expect FAIL** (`KeyError: 'setup'`).

- [ ] **Step 3: Implement** — in `src/governor/gate/runner.py`:

Add imports near the existing imports:
```python
from governor.live.history import fetch_daily_bars
from governor.technicals.assess import assess_setup, setup_to_dict
```

In `analyze_intent`, right after `contract = qualify(ib, intent)` (step 1), add:
```python
    # 1b. Candidate setup read (fail-soft): one reqHistoricalData on this same socket,
    # then a pure Stage-2/VCP (equity) or trend/vol/location/momentum (futures) assessment.
    bars = fetch_daily_bars(ib, contract, config.setup.history_duration)
    setup = assess_setup(intent.sec_type, intent.action, bars, config.setup)
```

In the `preview` dict literal, add a key (e.g. after `"reasons"`):
```python
        "setup": setup_to_dict(setup),
```

Return value and signature are unchanged for now (verdict wiring is Task 9–10). Keep `setup` in a local var; Task 10 threads it into `GateFacts`.

- [ ] **Step 4: Run — expect PASS.** Also run the full suite (`pytest -q`) — no regressions.
- [ ] **Step 5: Commit** — `git commit -am "feat: gate fetches + surfaces candidate setup (no verdict change)"`

---

# Phase 3 — Config + verdict escalation

### Task 8: `SetupRules` config models

**Files:**
- Modify: `src/governor/config.py` (add three models before `RulesConfig`; add `setup` field to `RulesConfig`)
- Modify: `config/rules.yaml` (document the `setup:` block — optional; defaults apply if absent)
- Test: `tests/test_config_setup.py`

> The config models + `RulesConfig.setup` are **already implemented in Task 3** (Phase 1). This task adds only the `config/rules.yaml` documentation block + a dedicated config test. The Step 3 code below is the reference for what Task 3 built — if for any reason it wasn't, implement it here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_setup.py
from governor.config import RulesConfig, SetupRules, EquitySetupRules, FuturesSetupRules

def test_defaults_present():
    c = RulesConfig()
    assert isinstance(c.setup, SetupRules)
    assert c.setup.min_bars == 200
    assert c.setup.equities.stage2_confirmed_min == 6
    assert c.setup.futures.atr_elevated_pctile == 0.70

def test_override_via_validate():
    c = RulesConfig.model_validate({"setup": {"equities": {"stage2_confirmed_min": 7}}})
    assert c.setup.equities.stage2_confirmed_min == 7
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** — add to `src/governor/config.py` (before `RulesConfig`):

```python
class EquitySetupRules(BaseModel):
    """Minervini Stage-2 + VCP thresholds (defaults seeded from the /vcp skill)."""
    stage2_confirmed_min: NonNegativeInt = 6     # of 7 criteria -> "confirmed"
    stage2_candidate_min: NonNegativeInt = 4     # 4-5 -> "candidate"; <=3 -> "none"
    high_proximity_pct: float = Field(0.75, gt=0, le=1)   # 52wk position to count as "near high"
    min_range_ratio: PositiveFloat = 1.30        # 52wk high/low
    ma200_slope_lookback: NonNegativeInt = 20    # bars
    pivot_extended_pct: float = Field(0.05, gt=0, le=1)   # past pivot -> extended -> CAUTION
    pivot_too_late_pct: float = Field(0.15, gt=0, le=1)
    contraction_loose_pct: float = Field(0.18, gt=0, le=1)


class FuturesSetupRules(BaseModel):
    """Futures four-factor setup thresholds."""
    ma_fast: NonNegativeInt = 20
    ma_mid: NonNegativeInt = 50
    ma_slow: NonNegativeInt = 200
    atr_period: NonNegativeInt = 14
    atr_lookback: NonNegativeInt = 100           # window for the ATR percentile
    atr_elevated_pctile: float = Field(0.70, gt=0, le=1)
    atr_compressed_pctile: float = Field(0.30, gt=0, le=1)
    range_lookback: NonNegativeInt = 20          # 20-day high/low for location
    extension_chase_pct: float = Field(0.02, gt=0, le=1)
    rsi_period: NonNegativeInt = 14
    rsi_overbought: PositiveFloat = 70.0
    rsi_oversold: PositiveFloat = 30.0


class SetupRules(BaseModel):
    history_duration: str = "1 Y"                # reqHistoricalData duration for the candidate
    min_bars: NonNegativeInt = 200               # need 200+ for MA200; below -> "insufficient"
    equities: EquitySetupRules = Field(default_factory=EquitySetupRules)
    futures: FuturesSetupRules = Field(default_factory=FuturesSetupRules)
```

Add to `RulesConfig`:
```python
    setup: SetupRules = Field(default_factory=SetupRules)
```

Add to `config/rules.yaml` (documented, defaults — no behavior change; keep file SAFE):
```yaml
# Setup-quality read (pre-trade gate). All optional; omit to use defaults.
setup:
  history_duration: "1 Y"
  min_bars: 200
  equities:
    stage2_confirmed_min: 6
    pivot_extended_pct: 0.05
  futures:
    atr_elevated_pctile: 0.70
    extension_chase_pct: 0.02
```

- [ ] **Step 4: Run — expect PASS** (and `pytest -q` clean).
- [ ] **Step 5: Commit** — `git commit -am "feat: SetupRules config (equity + futures thresholds)"`

---

### Task 9: `GateFacts.setup` + `decide()` CAUTION clause

**Files:**
- Modify: `src/governor/gate/analysis.py` (import `SetupAssessment`; add field to `GateFacts` ~line 174; add clause in `decide()` ~line 212)
- Test: `tests/gate/test_decide_setup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_decide_setup.py
from governor.gate.analysis import GateFacts, decide, Verdict
from governor.technicals.types import SetupAssessment
from governor.model import Severity, Trip, AssetClass
from governor.model import ActionType

def _poor(reasons=("setup: not a confirmed Stage 2 (4/7)",)):
    return SetupAssessment(available=True, asset_class="equity", poor=True, caution_reasons=reasons)

def _ok():
    return SetupAssessment(available=True, asset_class="equity", poor=False, caution_reasons=())

def test_poor_setup_escalates_clean_trade_to_caution():
    v = decide(GateFacts(setup=_poor()))
    assert v.level is Verdict.CAUTION
    assert any("Stage 2" in r for r in v.reasons)

def test_good_setup_stays_go():
    assert decide(GateFacts(setup=_ok())).level is Verdict.GO

def test_setup_never_downgrades_a_block():
    hard = Trip(rule_id="daily_loss_stop", asset_class=AssetClass.FUTURE,
                severity=Severity.HARD, message="loss stop", action=ActionType.PLATFORM_OFF_TODAY)
    v = decide(GateFacts(post_trade_trips=(hard,), setup=_ok()))
    assert v.level is Verdict.BLOCK

def test_poor_setup_alone_never_blocks():
    assert decide(GateFacts(setup=_poor())).level is Verdict.CAUTION  # never BLOCK
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** — in `src/governor/gate/analysis.py`:

Add import:
```python
from governor.technicals.types import SetupAssessment
```
Add field to `GateFacts`:
```python
    setup: SetupAssessment | None = None     # candidate setup read; poor setup -> CAUTION
```
In `decide()`, after the sizing block (before `if block:`), add:
```python
    if facts.setup is not None and facts.setup.poor:
        caution.extend(facts.setup.caution_reasons)
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: poor setup escalates the gate verdict to CAUTION"`

---

### Task 10: Thread `setup` into `GateFacts` in the runner

**Files:**
- Modify: `src/governor/gate/runner.py` (`GateFacts(...)` construction ~line 390)
- Test: extend `tests/gate/test_runner_setup.py`

- [ ] **Step 1: Write the failing test** — a BUY of a clear downtrend stock (fake `reqHistoricalData` returns falling bars) yields `verdict == "CAUTION"` with a Stage-2 reason, while a confirmed uptrend with no risk flags yields `GO`.

```python
def test_downtrend_buy_is_caution_via_setup(monkeypatch):
    # fake ib.reqHistoricalData -> 260 falling bars; clean account/risk
    # verdict, preview = analyze_intent(...)
    assert preview["verdict"] == "CAUTION"
    assert any("Stage 2" in r for r in preview["reasons"])
```

- [ ] **Step 2: Run — expect FAIL** (currently GO — setup not wired into facts).

- [ ] **Step 3: Implement** — in `analyze_intent`, add `setup=setup` to the `GateFacts(...)` call:

```python
    facts = GateFacts(
        post_trade_trips=tuple(trips),
        lockout_active=lockout_active,
        sizing=sized,
        buying_power_ok=bp_ok,
        setup=setup,
    )
```

- [ ] **Step 4: Run — expect PASS** (and `pytest -q` clean).
- [ ] **Step 5: Commit** — `git commit -am "feat: wire candidate setup into the gate verdict"`

---

# Phase 4 — VCP (isolated; replaces the Task-5 stub)

### Task 11: VCP contraction detection

**Files:**
- Modify: `src/governor/technicals/vcp.py` (replace the stub)
- Test: `tests/technicals/test_vcp.py`

**Algorithm (v1 heuristic):**
1. Find swing highs/lows with a fixed fractal window `k=5` (a bar is a swing high if its high is the max of the ±k window; swing low symmetric).
2. Walk swing points chronologically; pair each swing high with the next swing low to form a contraction leg; `retracement_pct = (high - low) / high`.
3. Keep the trailing legs (last up to 4). `is_contracting` = the retracement sequence is non-increasing within tolerance (each leg ≤ previous × 1.1).
4. `pivot` = the most recent swing high. `distance_pct = (price - pivot) / pivot`. Band per the spec table.
5. `last_contraction_pct` = retracement of the final leg; grade: `<8% excellent, <12% good, <18% acceptable, else too_loose`.
6. `volume_dryup` = mean volume over the final leg's bars < mean volume over the earlier legs' bars.
7. `available` = at least 2 contraction legs found AND `len(bars) >= cfg`-relevant minimum; else `VcpResult(available=False)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/technicals/test_vcp.py
from governor.config import EquitySetupRules
from governor.technicals.vcp import compute_vcp
from governor.technicals.types import Bar

def _bar(i, c, vol=100.0):
    return Bar(date=str(i), open=c, high=c + 0.2, low=c - 0.2, close=c, volume=vol)

def _decreasing_contractions():
    """Synthetic: three contractions 30% -> 15% -> 7%, rising pivots, volume drying up."""
    seq = []
    # leg 1: up to 100 then down to 70 (30%)
    seq += [100 - abs(50 - i) for i in range(0, 100)]   # crude tent; implementer may refine
    bars, i = [], 0
    for c in seq:
        bars.append(_bar(i, float(max(c, 1))))
        i += 1
    return bars

def test_unavailable_when_no_contractions():
    flat = [_bar(i, 100.0) for i in range(60)]
    r = compute_vcp(flat, EquitySetupRules())
    assert r.available is False

def test_detects_pivot_and_distance_band():
    bars = _decreasing_contractions()
    r = compute_vcp(bars, EquitySetupRules())
    # pivot is a recent swing high; distance band is one of the known labels
    assert r.distance_band in {"actionable", "extended", "wait", "too_late", "pre_breakout", "n/a"}
```

(Implementer: tighten the synthetic series so `available` is True and grades are asserted; the two tests above are the RED-start contract — add precise assertions once the detector shape is in.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** — replace `vcp.py` with the fractal-based detector per the algorithm above. Key structure:

```python
# src/governor/technicals/vcp.py
"""VCP contraction-sequence detection (v1 fractal heuristic). Pure."""
from __future__ import annotations

from governor.config import EquitySetupRules
from governor.technicals.types import Bar, VcpResult

_K = 5  # fractal window for swing detection


def _swings(bars: list[Bar]) -> list[tuple[int, str, float]]:
    """Return (index, 'H'|'L', price) swing points via a ±_K fractal window."""
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
    # Build (high,low,retr) legs from each High followed by the next Low.
    legs: list[tuple[float, float, float, int, int]] = []  # high, low, retr, hi_idx, lo_idx
    i = 0
    while i < len(swings) - 1:
        idx_h, kind_h, ph = swings[i]
        if kind_h != "H":
            i += 1
            continue
        # next swing low after this high
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

    # volume dry-up: last leg vs earlier legs
    def _leg_vol(lg):
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
```

- [ ] **Step 4: Run — expect PASS.** Re-run `tests/technicals/test_assess.py` — the equity-extended path now activates; confirm no regressions (`pytest -q`).
- [ ] **Step 5: Commit** — `git commit -am "feat: VCP contraction detection (replaces stub)"`

---

# Phase 5 — Render + equities skill

### Task 12: `gate/render.py` panels

**Files:**
- Create: `src/governor/gate/render.py`
- Test: `tests/gate/test_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_render.py
from governor.gate.render import render_panels

def _preview(**over):
    base = {
        "symbol": "ORCL", "action": "buy", "quantity": 50, "order_type": "market",
        "order_notional": 7260.0, "pct_nav": 0.02, "buying_power_ok": True,
        "init_margin": 3630.0, "name_weight_before": 0.041, "name_weight_after": 0.058,
        "trips": [], "verdict": "CAUTION", "reasons": ["trade is 2.0% of NAV (over the sizing band)"],
        "setup": {"available": True, "asset_class": "equity", "poor": True,
                  "caution_reasons": ["setup: not a confirmed Stage 2 (5/7)"],
                  "equity": {"stage2": {"classification": "candidate", "pass_count": 5,
                                        "position_pct": 0.68, "range_ratio": 1.9, "slope_up": True,
                                        "ma50": 140, "ma150": 135, "ma200": 130, "price": 145.2,
                                        "criteria": []},
                             "vcp": {"available": True, "pivot": 147.8, "distance_pct": 0.07,
                                     "distance_band": "extended", "last_contraction_pct": 0.06,
                                     "last_grade": "excellent", "volume_dryup": True, "contractions": []},
                             "extended": True}},
    }
    base.update(over)
    return base

def test_renders_three_panels():
    out = render_panels(_preview())
    assert "ORDER" in out and "RISK" in out and "SETUP" in out
    assert "ORCL" in out
    assert "Stage 2" in out

def test_setup_unavailable_panel():
    out = render_panels(_preview(setup={"available": False, "asset_class": "equity",
                                        "poor": False, "caution_reasons": []}))
    assert "unavailable" in out.lower() or "insufficient" in out.lower()
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** — `render_panels(preview)` returns a markdown string with the three panels (ORDER / RISK & SIZING / SETUP). Build it from primitives; no I/O. Choose glyphs: ✅ pass, 🟡 soft-miss, 🔴 fail/counter-trend, ⚠️ over-band. The renderer emits ONLY the three middle panels — the skill adds the banner + verdict + vault + confirm (so the headline reflects the final, vault-aware verdict). Implement both an equity SETUP panel (Stage 2 table + pivot line) and a futures SETUP panel (the four factors), branching on `setup["asset_class"]`. Render "insufficient data — setup unavailable" when `not setup["available"]`.

(Full code is the implementer's; the test above plus the spec mockups are the exact contract. Keep functions small: `_order_panel`, `_risk_panel`, `_equity_setup_panel`, `_futures_setup_panel`, `_setup_panel` dispatcher.)

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: pretty pre-trade panels (gate/render)"`

---

### Task 13: Add `panels` to the preview; update equities skill

**Files:**
- Modify: `src/governor/gate/runner.py` (preview dict — add `panels`)
- Modify: `skills/pre-trade-equities/SKILL.md`
- Test: extend `tests/gate/test_runner_setup.py` (`assert "panels" in preview and isinstance(preview["panels"], str)`)

- [ ] **Step 1: Write the failing test** — `assert "panels" in preview`.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement:**
  - In `analyze_intent`, after building `preview` (it must exist first), add `preview["panels"] = render_panels(preview)` and import `from governor.gate.render import render_panels`.
  - In `skills/pre-trade-equities/SKILL.md`: retitle Step 4 to **"Step 4 — Setup (Minervini, from the gate)"** and state that the gate now returns `setup` + `panels` (no separate `/vcp` call needed); rewrite **Step 6** to present the screen as: banner (the final verdict) → `panels` (verbatim from the gate JSON) → VERDICT paragraph (synthesize setup + vault) → VAULT → the confirm line. Keep the staged-token submit unchanged.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: gate emits rendered panels; equities skill uses them"`

---

# Phase 6 — Futures skill parity + docs

### Task 14: Futures skill + docs

**Files:**
- Modify: `skills/pre-trade-futures/SKILL.md` (add the Setup section + the futures panel in Step 6, mirroring equities)
- Modify: `docs/FORCLAUDE.md` (new "Setup read" subsection: the `technicals/` package, the one-connection bar fetch, the CAUTION escalation)
- Modify: `docs/HANDBOOK.md` (operator note: what the SETUP panel means, how to tune `setup:` thresholds)
- Test: none (docs). Run `pytest -q` to confirm the whole suite is green.

- [ ] **Step 1:** Update `skills/pre-trade-futures/SKILL.md` — add "Setup (futures read)" describing the four factors and that the gate returns them; Step 6 presents `panels` like equities.
- [ ] **Step 2:** Update `docs/FORCLAUDE.md` and `docs/HANDBOOK.md` per above (engaging, plain-language, per the project doc style).
- [ ] **Step 3:** `pytest -q` — expect all green.
- [ ] **Step 4: Commit** — `git commit -am "docs: futures setup parity + FORCLAUDE/HANDBOOK setup read"`

---

## Final verification (after all tasks)

- [ ] `pytest -q` — full suite green (target 80%+ coverage on `technicals/`).
- [ ] Manual smoke (TWS up, SAFE config): `PYTHONPATH=src .venv/bin/python -m governor.gate analyze buy 1 AAPL --sec-type stk --type market --adaptive --json` → preview includes `setup` + `panels`; verdict reflects the setup; **dry_run still true** (no order).
- [ ] Confirm `config/rules.yaml` still ships SAFE (`dry_run: true`, `readonly: true`) and the armed local copy was never committed.
- [ ] Open a PR; do NOT merge the armed `rules.yaml`.

## Notes / risks

- **VCP is a v1 heuristic.** The fractal detector is intentionally simple; it's isolated in Phase 4 so it can be refined without touching the risk path. If it proves noisy, the equity "poor" flag still works on Stage-2 alone (Phases 1–3).
- **Equity SELL:** never flagged poor (Minervini judges entries, not exits). Encoded in `_equity_reasons`.
- **min_bars=200** means freshly-listed names (<200 daily bars) show "setup unavailable" and fall back to risk-only — correct and safe.
```

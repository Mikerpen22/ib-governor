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

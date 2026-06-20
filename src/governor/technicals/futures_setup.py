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

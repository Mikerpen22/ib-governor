"""Pure pre-trade panel renderer.

render_panels(preview: dict) -> str

Emits ONLY the three middle panels — 📋 ORDER, 💰 RISK & SIZING, 📈 SETUP —
as a markdown string ready to paste into the confirmation screen.

The skill owns the banner, the 🧭 VERDICT paragraph, the 📓 VAULT section,
and the confirm line so the headline always reflects the final, vault-aware
verdict. This module is intentionally pure: dict in, str out; no I/O, no
ib_async, no side effects.

Glyph convention:
  ✅  pass / neutral
  🟡  caution-level factor
  🔴  primary / strong warning (e.g. counter-trend)
  ⚠️  over-band / extended
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Order panel
# ---------------------------------------------------------------------------


def _order_panel(preview: dict) -> str:
    sym = preview.get("symbol", "?")
    action = preview.get("action", "?").capitalize()
    qty = preview.get("quantity", "?")
    otype = preview.get("order_type", "?").capitalize()
    notional = preview.get("order_notional", 0.0)
    lines = [
        "📋 ORDER",
        f"   {action} {qty} {sym} · {otype}",
        f"   Notional ${notional:,.0f}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Risk & Sizing panel
# ---------------------------------------------------------------------------


def _risk_panel(preview: dict) -> str:
    notional = preview.get("order_notional", 0.0)
    pct_nav = preview.get("pct_nav", 0.0)
    bp_ok = preview.get("buying_power_ok", True)
    init_margin = preview.get("init_margin")
    w_before = preview.get("name_weight_before", 0.0)
    w_after = preview.get("name_weight_after", 0.0)
    trips = preview.get("trips", [])

    bp_glyph = "✅" if bp_ok else "🔴"
    size_glyph = "⚠️" if pct_nav > 0.015 else "✅"
    margin_str = f"${init_margin:,.0f}" if init_margin is not None else "n/a"

    lines = [
        "💰 RISK & SIZING",
        f"   Size          ${notional:,.0f}   {pct_nav * 100:.1f}% NAV      {size_glyph}",
        f"   Buying power  {bp_glyph}      init margin {margin_str}",
        f"   Weight        {w_before * 100:.1f}% → {w_after * 100:.1f}%",
    ]
    if trips:
        for t in trips:
            sev = t.get("severity", "?").upper()
            msg = t.get("message", "?")
            glyph = "🔴" if sev in ("HARD", "BLOCK") else "🟡"
            lines.append(f"   Rule trip     {glyph} [{sev}] {msg}")
    else:
        lines.append("   Rule trips    none")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Equity SETUP panel
# ---------------------------------------------------------------------------


def _criteria_glyph(passed: bool, soft: bool = False) -> str:
    if passed:
        return "✅"
    return "🟡" if soft else "🔴"


def _equity_setup_panel(setup: dict) -> str:
    eq = setup.get("equity", {})
    s2 = eq.get("stage2", {})
    vcp = eq.get("vcp", {})

    clf = s2.get("classification", "none")
    passes = s2.get("pass_count", 0)
    price = s2.get("price", 0.0)
    ma50 = s2.get("ma50")
    ma150 = s2.get("ma150")
    ma200 = s2.get("ma200")
    slope = s2.get("slope_up", False)
    pos_pct = s2.get("position_pct", 0.0)
    range_ratio = s2.get("range_ratio", 0.0)

    # MA above checks
    above_ma50 = ma50 is not None and price > ma50
    above_ma150 = ma150 is not None and price > ma150
    above_ma200 = ma200 is not None and price > ma200
    ma_stack = (ma50 is not None and ma150 is not None and ma200 is not None
                and ma50 > ma150 > ma200)

    g50 = _criteria_glyph(above_ma50)
    g150 = _criteria_glyph(above_ma150)
    g200 = _criteria_glyph(above_ma200)
    g_stack = _criteria_glyph(ma_stack)
    g_slope = _criteria_glyph(slope)

    # 52-wk position (want >= 75%)
    pos_ok = pos_pct >= 0.75
    g_pos = _criteria_glyph(pos_ok, soft=True)

    # Range ratio (want >= 1.3x)
    range_ok = range_ratio >= 1.3
    g_range = _criteria_glyph(range_ok)

    header = f"📈 SETUP — Minervini   (Stage 2: {passes}/7 · {clf})"

    lines = [
        header,
        f"   vs MA50/150/200    {g50} {g150} {g200}",
        f"   MA stack           {g_stack} 50>150>200",
        f"   MA200 slope        {g_slope} {'rising' if slope else 'flat/falling'} 1mo",
        f"   52-wk position     {g_pos} {pos_pct * 100:.0f}%   (want ≥75%)",
        f"   Range ≥1.3x        {g_range} {range_ratio:.1f}x",
    ]

    # VCP pivot line
    if vcp.get("available"):
        pivot = vcp.get("pivot", 0.0)
        dist_pct = vcp.get("distance_pct", 0.0)
        band = vcp.get("distance_band", "n/a")
        last_pct = vcp.get("last_contraction_pct", 0.0)
        last_grade = vcp.get("last_grade", "n/a")
        vol_dry = vcp.get("volume_dryup", False)

        # Contraction line
        contr_glyph = "🟢" if last_grade in ("excellent", "good") else "🟡"
        contr_label = f"{last_pct * 100:.0f}%"
        lines.append(
            f"   VCP last contract  {contr_glyph} {contr_label}    {last_grade}"
            + ("  💧 vol dry" if vol_dry else "")
        )

        # Pivot distance line — band drives the glyph
        if band == "actionable":
            pivot_glyph = "✅"
        elif band in ("extended", "wait"):
            pivot_glyph = "⚠️"
        elif band == "too_late":
            pivot_glyph = "🔴"
        else:
            pivot_glyph = "🟡"

        sign = "+" if dist_pct >= 0 else ""
        lines.append(
            f"   Pivot ${pivot:.2f}      {pivot_glyph} {sign}{dist_pct * 100:.0f}%   {band}"
        )
    else:
        lines.append("   VCP             n/a (not enough contractions)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Futures SETUP panel
# ---------------------------------------------------------------------------


def _futures_setup_panel(setup: dict) -> str:
    fut = setup.get("futures", {})
    if not fut:
        return "📈 SETUP — Futures read\n   (no data)"

    with_trend = fut.get("with_trend", False)
    counter_trend = fut.get("counter_trend", False)
    trend_label = fut.get("trend_label", "mixed")

    atr_pctile = fut.get("atr_pctile", 0.0)
    vol_label = fut.get("vol_label", "normal")
    vol_expanding = fut.get("vol_expanding", False)

    dist_from_high = fut.get("dist_from_high_pct", 0.0)
    # dist_from_low_pct is available in the gate payload but intentionally unused here —
    # the location line always shows distance from the recent high (the trade-relevant side).
    _dist_from_low = fut.get("dist_from_low_pct", 0.0)  # noqa: F841  (kept for completeness)
    chasing = fut.get("chasing", False)

    rsi_val = fut.get("rsi")
    momentum_label = fut.get("momentum_label", "neutral")

    # Header warning for counter-trend
    warning = "  ⚠️ counter-trend" if counter_trend else ""
    header = f"📈 SETUP — Futures read{warning}"

    # 1. Trend alignment
    if with_trend:
        trend_glyph = "✅"
        trend_note = f"aligned — with the {trend_label}"
    elif counter_trend:
        trend_glyph = "🔴"
        if trend_label == "uptrend":
            trend_note = "above all — shorting an uptrend"
        elif trend_label == "downtrend":
            trend_note = "below all — buying a downtrend"
        else:
            trend_note = f"counter-trend ({trend_label})"
    else:
        trend_glyph = "🟡"
        trend_note = f"mixed trend ({trend_label})"

    # 2. Vol regime
    if vol_label == "elevated":
        vol_glyph = "🟡"
    else:
        vol_glyph = "✅"
    expand_note = " — expanding, widen stops" if vol_expanding else ""
    vol_line = f"   Vol regime        {vol_glyph} ATR {atr_pctile * 100:.0f}th pct{expand_note}"

    # 3. Location (vs 20-day range; dist_from_high is negative when at/above high)
    if chasing:
        loc_glyph = "🔴"
        loc_note = "at the range extreme — chasing"
    else:
        loc_glyph = "🟢"
        # Show whichever is the trade-relevant distance
        # Use dist_from_high (negative means price is above the 20d high which can't happen normally;
        # negative means price < 20d high, i.e. below — show as positive offset from high)
        loc_pct = dist_from_high * 100  # negative = below 20d high
        loc_note = f"{loc_pct:+.1f}% vs 20d high"

    # 4. Momentum
    if momentum_label == "overbought":
        mom_glyph = "🟡"
        mom_note = f"RSI({rsi_val:.0f}) overbought — supports a fade" if rsi_val else "overbought"
    elif momentum_label == "oversold":
        mom_glyph = "🟡"
        mom_note = f"RSI({rsi_val:.0f}) oversold" if rsi_val else "oversold"
    elif rsi_val is None:
        mom_glyph = "🟡"
        mom_note = "n/a"
    else:
        mom_glyph = "✅"
        mom_note = f"RSI {rsi_val:.0f} neutral"

    lines = [
        header,
        f"   Trend 20/50/200   {trend_glyph} {trend_note}",
        vol_line,
        f"   Location          {loc_glyph} {loc_note}",
        f"   Momentum RSI(14)  {mom_glyph} {mom_note}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Setup panel dispatcher
# ---------------------------------------------------------------------------


def _setup_panel(setup: dict) -> str:
    if not setup.get("available", False):
        return "📈 SETUP\n   insufficient data — setup unavailable"
    asset_class = setup.get("asset_class", "equity")
    if asset_class == "future":
        return _futures_setup_panel(setup)
    return _equity_setup_panel(setup)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_panels(preview: dict) -> str:
    """Render the three middle panels from a gate preview dict.

    Pure: dict in, markdown str out. No I/O, no ib_async import, no mutation.
    Never raises on a setup with available=False or missing equity/futures
    sub-dicts.
    """
    setup = preview.get("setup", {"available": False, "asset_class": "equity",
                                   "poor": False, "caution_reasons": []})
    parts = [
        _order_panel(preview),
        "",
        _risk_panel(preview),
        "",
        _setup_panel(setup),
    ]
    return "\n".join(parts)

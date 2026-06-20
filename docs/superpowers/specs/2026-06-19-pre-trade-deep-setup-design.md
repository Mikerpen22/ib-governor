# Pre-Trade Deep Setup Read — Design Spec

**Date:** 2026-06-19
**Status:** Approved (brainstorm) → implementation
**Scope:** Both pre-trade analysts (equities + futures), deep.

## Goal

Make the pre-trade gate **comprehensive** (systematic setup-quality analysis on real
IBKR bars), **fast** (one connection, one call returns risk + setup), and
**beautiful** (a structured, decision-first confirmation screen) — without weakening
the existing risk-discipline gate.

## Problem (confirmed by code audit)

- The gate fetches **zero technicals** for the candidate symbol. No historical bars,
  no MAs, no 52-week position, no volume, no pivot. It is a pure *account-risk* gate
  (sizing, margin, concentration, rule trips).
- The rigorous Minervini logic exists only in the separate `/vcp` skill, on a
  **different** TWS connection (the `ibkr-tws` MCP, client 3), invoked by hand.
- "Slowness" is structural: every `analyze` is a cold process that opens its own TWS
  socket (client 5) and tears it down; layering a VCP read on top today means a
  **second** connection plus the model doing chart math in prose across tool round-trips.
- Presentation is unspecified: `pre-trade-equities` Step 6 just says "show … concisely",
  so the confirmation screen varies run to run.

## Architecture

The gate's existing philosophy holds and is *extended*, not replaced:

> **gate = deterministic facts + verdict (pure); skill = judgment + render.**

The setup read enters as **one more fact** in `GateFacts`, fed by **one more I/O call**
(`reqHistoricalData`) on the connection `analyze_intent` already holds. Everything
computed from the bars is **pure** (mirrors `rules/`), config-driven (mirrors
`config.py`), and fail-soft (mirrors `collect_market_backdrop` in `live/daily.py`).

### Chosen approach (A) vs alternatives

| | Where technicals live | Verdict |
|---|---|---|
| **A — gate-native (CHOSEN)** | Gate fetches bars on its open socket; pure-Python compute | One connection, one call, deterministic + testable |
| B — skill calls `/vcp` (MCP) | Separate MCP connection; LLM does the math | Second connection + in-prose math = the slowness we're removing |
| C — standalone analyzer process | A third service | More moving parts, another cold start |

## Locked decisions

- **Scope:** both paths deep.
- **Setup → verdict:** a poor/extended setup escalates to **CAUTION**. It NEVER
  downgrades a risk BLOCK and NEVER hard-blocks on its own (mirrors `size > 1.5% NAV`).
- **Equities setup:** Minervini Stage 2 (7-point) + VCP contraction read.
- **Futures setup:** four factors — trend alignment, volatility regime,
  extension/location, momentum.
- **Render split:** Python renders the **Order / Risk / Setup** panels
  deterministically; the **skill** owns the banner + final Verdict + Vault + confirm,
  so the headline always reflects the user's final (possibly vault-escalated) call.
- **Out of scope (YAGNI):** in-gate parallelization of IBKR calls (optional later);
  a warm socket-reuse daemon; non-US-equity Stage 2 nuances.

## Component design & file structure

New work, as small focused files. `technicals/` is **pure** — no `ib_async` import.

```
src/governor/technicals/            (NEW — pure computation, no IBKR)
  __init__.py
  types.py          Bar, Stage2Result, VcpResult, EquitySetup, FuturesSetup, SetupAssessment
  indicators.py     sma, atr, rsi, roc, slope, pct_from_high, pct_from_low, percentile
  stage2.py         Minervini 7-point checklist  -> Stage2Result
  vcp.py            contraction-sequence detection, pivot, distance, volume dry-up -> VcpResult
  equity_setup.py   compose Stage2Result + VcpResult -> EquitySetup (+ poor/extended flags)
  futures_setup.py  trend / vol-regime / location / momentum -> FuturesSetup (+ poor flags)
  assess.py         assess_setup(asset_class, bars, intent, cfg) -> SetupAssessment

src/governor/live/history.py        (NEW)
  fetch_daily_bars(ib, contract, duration) -> list[Bar] | None   # fail-soft

src/governor/gate/render.py         (NEW)
  render_panels(preview: dict) -> str   # Order/Risk/Setup markdown; pure (dict in, str out)
```

Modified:

```
src/governor/gate/analysis.py   GateFacts gains `setup: SetupAssessment | None`;
                                decide() adds a setup-driven CAUTION clause.
src/governor/gate/runner.py     analyze_intent: after qualify(), fetch bars + assess_setup;
                                put setup into GateFacts; add setup + panels to preview dict.
src/governor/config.py          add SetupRules (EquitySetupRules + FuturesSetupRules + history);
                                RulesConfig gains `setup: SetupRules`.
config/rules.yaml               document the new `setup:` block (defaults below).
skills/pre-trade-equities/SKILL.md   Step 4 becomes "Setup (Minervini)"; Step 6 uses panels.
skills/pre-trade-futures/SKILL.md    add the futures setup section + panels.
```

## Data types (frozen; in `technicals/types.py`)

- `Bar(date: str, open: float, high: float, low: float, close: float, volume: float)`
- `Stage2Result`: `price, ma50, ma150, ma200, slope_up: bool, position_pct, range_ratio,
  criteria: tuple[tuple[str,bool],...], pass_count: int, classification: str`
  (`"confirmed" | "candidate" | "none"`).
- `VcpResult`: `available: bool, contractions: tuple[tuple[float,float,float],...]` (high,
  low, retr%), `is_contracting: bool, last_contraction_pct, last_grade: str, pivot,
  distance_pct, distance_band: str, volume_dryup: bool`.
- `EquitySetup`: `stage2: Stage2Result, vcp: VcpResult, extended: bool, poor: bool`.
- `FuturesSetup`: `with_trend: bool, trend_label, atr, atr_pctile, vol_label,
  vol_expanding: bool, dist_from_high_pct, dist_from_low_pct, chasing: bool, rsi,
  roc, momentum_label, counter_trend: bool, vol_elevated: bool, poor: bool`.
- `SetupAssessment`: `available: bool, asset_class: str, poor: bool,
  caution_reasons: tuple[str,...], equity: EquitySetup | None,
  futures: FuturesSetup | None`. This is the only type `GateFacts` carries.

## Thresholds (config-driven; defaults seeded from the `/vcp` skill)

```yaml
setup:
  history_duration: "1 Y"        # reqHistoricalData duration for the candidate
  min_bars: 200                  # need 200+ closes for MA200; below -> "insufficient"
  equities:
    stage2_confirmed_min: 6      # of 7 criteria -> "confirmed"
    stage2_candidate_min: 4      # 4-5 -> "candidate"; <=3 -> "none"
    high_proximity_pct: 0.75     # (price-low)/(high-low) >= this  (within 25% of 52wk high)
    min_range_ratio: 1.30        # 52wk high/low >= this
    ma200_slope_lookback: 20     # MA200[-1] > MA200[-1-lookback] -> rising
    pivot_extended_pct: 0.05     # > this past pivot -> extended -> CAUTION
    pivot_too_late_pct: 0.15     # > this past pivot -> "too late"
    contraction_loose_pct: 0.18  # last contraction deeper than this -> "too loose"
  futures:
    ma_fast: 20
    ma_mid: 50
    ma_slow: 200
    atr_period: 14
    atr_lookback: 100            # window for the ATR percentile
    atr_elevated_pctile: 0.70    # ATR percentile above this -> elevated vol -> CAUTION
    atr_compressed_pctile: 0.30
    range_lookback: 20           # 20-day high/low for the location check
    extension_chase_pct: 0.02    # entering within this of the 20d extreme in trade dir -> chasing
    rsi_period: 14
    rsi_overbought: 70
    rsi_oversold: 30
```

### Setup → CAUTION rules (in `assess.py`, surfaced by `decide()`)

**Equities** — `poor=True` (→ CAUTION) when any of:
- Stage 2 not confirmed (`classification != "confirmed"`), or
- past the pivot beyond `pivot_extended_pct` (extended), or
- last contraction looser than `contraction_loose_pct`.

**Futures** — `poor=True` (→ CAUTION) when any of:
- counter-trend (trade direction opposes the 20/50/200 alignment), or
- chasing (entering within `extension_chase_pct` of the 20-day extreme in the trade
  direction), or
- elevated vol (`atr_pctile > atr_elevated_pctile`).

`caution_reasons` carries a human string per triggered condition; `decide()` appends
them to the CAUTION list. Never BLOCK; never downgrade a risk BLOCK.

## Presentation (the confirmation screen)

Python `render_panels()` emits the middle three panels; the skill wraps them.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   <- banner: skill (final verdict)
🟡  CAUTION  ·  BUY 50 ORCL · Adaptive Mkt
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 ORDER                                        <- panels: render.py
   Buy 50 ORCL · Adaptive Market (Normal) · DAY
   Ref ~$145.20 · notional $7,260

💰 RISK & SIZING
   Size          $7,260   2.0% NAV      ⚠️ >1.5%
   Buying power  ok ✅      init margin $3,630
   ORCL weight   4.1% → 5.8%
   Rule trips    none

📈 SETUP — Minervini   (Stage 2: 5/7 · candidate)
   vs MA50/150/200    ✅ ✅ ✅   above all
   MA stack           ✅ 50>150>200
   MA200 slope        ✅ rising 1mo
   52-wk position     🟡 68%   (want ≥75%)
   Range ≥1.3x        ✅ 1.9x
   VCP last contract  🟢 6%    tight
   Pivot $147.80      🟡 +7%   extended

🧭 VERDICT — CAUTION                            <- skill (synthesizes setup + vault)
   <one paragraph: the call + the one thing that would change it>

📓 VAULT                                        <- skill
   <thesis / prior lessons, woven in>

─────────────────────────────────────────────
Confirm? Reply "yes" to submit · token expires ~5 min
```

Futures SETUP panel (four factors):

```
📈 SETUP — Futures read   (⚠️ counter-trend)
   Trend 20/50/200   🔴 above all — shorting an uptrend
   Vol regime        🟡 ATR 78th pct — expanding, widen stops
   Location          🟢 +0.4% vs 5d high — fading the extension
   Momentum RSI(14)  🟡 71 overbought — supports a fade
```

## Data flow

```
skill (pre-trade-equities | pre-trade-futures)
  → ONE call: python -m governor.gate analyze … --json
      gate (single socket, gate_client_id=5):
        qualify(contract)
        whatIfOrder(margin)            [risk]
        reqHistoricalData(1Y daily)    [setup]  ← NEW, same socket, right after qualify
        assess_setup(bars …)           [pure: Stage2+VCP | futures 4-factor]
        account snapshot + rule engine [risk trips]
        decide(facts incl. setup)      → verdict (risk + setup-CAUTION)
        render_panels(preview)         → markdown
      ← JSON { verdict, risk facts, setup, panels, token }
  → skill reads vault for the symbol
  → skill shows: banner + panels + VERDICT + VAULT + confirm
  → user "yes" → gate submit --token
```

## Error handling (fail-soft, safety-first)

- `fetch_daily_bars` wraps `reqHistoricalData` in try/except → returns `None` on any
  error (matches `_fetch_daily_bars` in `live/daily.py`). Never sinks the gate.
- Insufficient history (`< setup.min_bars`, e.g. a recent IPO) → `SetupAssessment(
  available=False)`; the SETUP panel shows "insufficient data", and the verdict falls
  back to **risk-only** (no setup escalation). The safety-critical risk gate is never
  blocked by a setup failure.
- Delayed/frozen market-data type 4 is fine — historical bars are entitled without a
  live subscription (the daily collector already relies on this).

## Testing strategy (TDD; maintain 80%+)

- **indicators.py:** unit tests vs textbook values (SMA of a known series; RSI of the
  canonical Wilder example; ATR; percentile).
- **stage2 / vcp / futures_setup:** synthetic `Bar` arrays encoding known patterns — a
  clean Stage 2, a non-Stage-2 downtrend, a textbook decreasing-contraction VCP, an
  extended entry, a counter-trend future, an elevated-vol regime.
- **assess.py:** poor-setup flags fire on the right inputs; `available=False` on short
  history.
- **decide() integration:** a poor setup escalates GO→CAUTION; never downgrades a HARD
  BLOCK; never produces BLOCK on its own (mirrors the daemon edge-trigger tests).
- **render.py:** panels contain the verdict-relevant numbers and the right flag glyphs;
  golden-string assertions on a fixed preview dict.
- **history.py:** fake `ib` returns bars → `list[Bar]`; raising `ib` → `None`.
- Live/integration paths skip when TWS is absent (existing convention).

## Phasing (each phase ships independently)

1. **`technicals/` core** — `types.py`, `indicators.py`, `stage2.py`,
   `futures_setup.py`, `assess.py` (equity path minus VCP, futures full) + tests.
   Pure, zero IBKR, high value, no risk.
2. **Bar fetch + wire-in** — `live/history.py`; `analyze_intent` fetches bars and calls
   `assess_setup`; `setup` added to the preview dict (fail-soft). No verdict change yet.
3. **Verdict escalation** — `GateFacts.setup` + `decide()` CAUTION clause + config
   `SetupRules` + tests.
4. **VCP** — `vcp.py` contraction detection + pivot/distance/volume; fold into
   `equity_setup.py`. Isolated so it can't destabilize phases 1–3.
5. **Render** — `gate/render.py` panels; JSON `panels` field; update both SKILL.md files
   (Step 4 "Setup", Step 6 banner+panels+confirm).
6. **Futures skill parity + docs** — `pre-trade-futures` setup section; refresh
   `docs/FORCLAUDE.md` (architecture) + `docs/HANDBOOK.md` (the new setup read).

## Safety invariants (unchanged)

- `analyze_intent` stays READ-ONLY; the only write path remains
  `actions/executor.py` (`_guarded`). The bar fetch is a read.
- `config/rules.yaml` continues to ship SAFE (`dry_run: true`, `readonly: true`);
  the armed local copy is never committed.
- Every submit stays confirm-gated behind the single-use token.

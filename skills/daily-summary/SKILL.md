---
name: daily-summary
description: Comprehensive, first-principles end-of-day trade summary. Pulls the day's fills / P&L / positions + market backdrop (indices, VIX), researches what drove the tape, classifies the macro regime, scores cross-asset signals, decomposes the book into the bets it actually expresses, and reads it through three investor lenses (Druckenmiller · Gerstner · Baker). Sends a concise Telegram recap and writes a visually rich, emoji-structured "Market Close" note to the research vault. Use at the close, or when asked for a daily trade summary / recap / market close note.
---

# 🌙 Daily Trade Summary — Druckenmiller · Gerstner · Baker

Produce a **comprehensive, first-principles** end-of-day recap: pull the account truth, read the regime, decompose the book into the bets it actually expresses, then judge it through three lenses. **Send a tight recap to Telegram** and **write a visually rich "Market Close" note** to the vault. Read-only on the account — it pulls data, sends a message, writes a note; it places **NO** trades.

> **Setup:** `GOVERNOR_HOME` = your ib-governor checkout (e.g. `export GOVERNOR_HOME=~/ib-governor`). `VAULT_DIR` = your Obsidian vault root (the auto-note is written under `$VAULT_DIR/invest/daily-recaps/`). If `VAULT_DIR` is unset, skip the vault note and just send the Telegram recap.

## The standard (read this first)
The reader is a serious investor. Match that bar:
- **First principles, not narration.** Not *"NVDA −2%"* — say *why it matters to THIS book* and *what it implies next.* Every claim traces to a number (a fill, a weight, a P&L, an index move) or a sourced fact.
- **Causal, not coincidental.** "X happened **because** Y, which means Z for our exposure" is the job.
- **Honest.** Praise genuine discipline; name churn, averaging-down, oversized risk, fighting the regime. These three would not flatter a sloppy day.
- **Visually clean.** Emojis as section anchors, tables for cross-sectional data, a blockquote tl;dr, tight bullets over walls of prose. Beautiful to skim, dense to read.
- **Right-sized.** Rich, but a daily recap — not a per-name deep-dive. One paragraph per lens, not ten.

The three lenses + the framework each carries:
- **🦅 Stan Druckenmiller — macro & risk / regime.** *The Fed / discount-rate is the master variable.* Classify the **regime** (Step 2) and ask: does the book fit it? Losers cut, winners pressed? Size matched to conviction? Leverage discipline. **VIX > 20 is a contrarian-long signal** (fear overpriced). *Not whether you're right — how much you make when right vs. lose when wrong. Liquidity & technicals time the market; valuation only sets how far it can go once the trend turns.*
- **🚀 Brad Gerstner — secular growth / AI.** Durable AI/secular thesis intact, or chasing? Platform vs. point-solution; is TAM growing faster than the company? Quality + efficiency of compounders; founder-led premium.
- **🎯 Gavin Baker — concentrated conviction / semis-tech.** Conviction-weighted into the 2–3 things that matter? Semis/compute-cycle read. Rule of 40 (growth + FCF margin); SBC is a *real* cost. Concentration deliberate, or drift?

---

## Step 1 — Pull the day's data (read-only)
```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" -m governor.live.daily --json
```
Parse: `date`, `nav`, `realized_pnl_today`, `margin_cushion`, `gross_leverage`, `fills[]`, `positions[]` (each `{symbol, sec_type, position, market_value, unrealized_pnl}`), `trips[]`, `indices` (`{SPY,QQQ,DIA,IWM}` → `{label,last,change_pct}` or `null`), `vix` (`{level,change_pct,elevated,signal}` or `null`).

If it errors (TWS down), **say so and STOP** — never fabricate numbers.

> ⚠️ **Known artifact — ignore it:** the collector builds its snapshot with an empty sector map, so you will see a trip `equities.sector_concentration: unknown is ~105% of NAV (sector unknown — verify)`. This is **not a real breach** — it's every equity lumped into one "unknown" bucket. Do your **own** sector classification in Step 4; never report this trip as a brake line.

## Step 2 — 🌍 Read the regime (backdrop → web → cross-asset score → classification)
Your account data says *what you hold*; this step says *what world you hold it in*. This is what makes the note first-principles instead of a position dump.

**(a) Backdrop** — from `indices` + `vix`: SPX/NDX/Dow/RUT % moves + breadth divergence (mega-cap vs small-cap), and the VIX level/Δ. Skip any `null` entry.

**(b) Web research (best-effort, 2–4 searches)** — attribute the day's move to **causes**: macro prints (CPI/PCE/NFP/claims), Fed/FOMC speak, 10Y/2Y yields, the dollar, oil/gold; single-name news for your largest/most-moved holdings + watchlist; sector action (SOX/software/mega-cap). Cross-check material numbers; flag single-source. If web is unavailable: "macro backdrop: web unavailable — account + index data only" (never invent a driver).

**(c) Cross-asset consensus score** — tally signals, then label the tape:
| Signal | 🟢 Bullish | 🔴 Bearish |
|---|---|---|
| Yield curve (2s10s) | steepening | inverting/deeply inverted |
| Credit spreads (HY) | tightening | widening >100bps |
| Copper/Gold | rising (reflation) | falling (risk-off) |
| DXY | weakening | strengthening (flight to safety) |
| Breadth (% > 200MA) | >60% | <40% |
| Put/Call, AAII | P/C >1.0 or bears >45% (contrarian +) | P/C <0.7 or bears <20% (complacency) |
| VIX term structure | contango (normal) | backwardation (stress) |

→ **6+ bullish = Strong Risk-On · 4–5 = Cautiously Bullish · 3–4 bearish = Cautiously Bearish · 6+ bearish = Risk-Off.** Use only the signals you actually have; note divergence explicitly — *a market that hasn't priced what another already has IS the insight.*

**(d) Name the regime** (Druckenmiller's "where are we in the cycle?"):
| PMI | Employment | Inflation | Fed | Regime → Playbook |
|---|---|---|---|---|
| >50 rising | low, falling | moderate | easing | **Early Expansion** → max risk-on; growth>value, small>large |
| >50 stable | low, stable | rising | neutral | **Mid Expansion** → selective growth; quality wins |
| >50 falling | low, rising | elevated | tightening | **Late Expansion** → reduce beta; FCF + quality |
| <50 falling | rising | falling | easing starts | **Early Contraction** → defensive; cash is a position |
| <50 bottoming | high | low | aggressive easing | **Trough** → start building; bet big |
Pick the closest regime (or "Transitional" → smaller sizes) and state its playbook in one line.

## Step 3 — 📓 (Optional) Vault continuity — only if `VAULT_DIR` is set
1. **FIRST** read `$VAULT_DIR/CLAUDE.md` if present — match the vault's structure, tags, linking style.
2. Read the most recent `invest/daily-recaps/*Market Close*` (and/or `invest/*Market Close*`) note: **close the loop** — did today confirm or refute yesterday's setup?
3. If deeper research notes exist for held/traded names (theses, regime notes), skim and **link** them (`[[Note Name]]`) so the daily note plugs into the knowledge graph.

## Step 4 — 🔬 First-principles book decomposition
Decompose the account into the **bets it actually expresses**, not a ticker list. Compute from `positions[]` + `nav`:

- **🎲 The one-liner:** *"This book is essentially a bet on: {the 1–2 macro/secular factors that drive most of the P&L}"* (e.g. "US mega-cap AI capex + a soft-landing rate path"). This single sentence is the most important line in the note.
- **Dominant bets:** group holdings into themes/sectors **yourself** (AI-semis: NVDA/AMD/ADI; mega-cap platforms: MSFT/GOOGL/META/AMZN; software: NET/SHOP/ORCL; …). State the top 2–3 as % of NAV.
- **Concentration:** top single names + top sectors as `STK market_value / nav`. Flag >15% name / >25% sector. (Equity concentration uses **STK**; treat **OPT** and **FUT** as separate exposure buckets — option `market_value` is premium, not underlying notional; futures `market_value` is contract notional. Note option delta direction where obvious.)
- **⚠️ Correlation risk:** which holdings share the *same* macro sensitivity (rates, AI-capex, the dollar, one earnings cycle)? Concentration hides in correlation — "5 names, 1 bet" is the trap. Call it out.
- **Pressing winners or watering losers?** Rank by `unrealized_pnl`; are the biggest weights the winners or the losers? Cross-ref today's `fills[]`: did today's actions cut losers or add to them?
- **Leverage vs. regime:** `gross_leverage` + `margin_cushion` vs. the Step 2 tape. **Important:** headline `gross_leverage` excludes futures notional — add FUT notional for *true* economic exposure (e.g. a 1.2× equity book + a large MNQ long can be ~1.7× net long). State the real number.
- **Futures / overnight:** any FUT (e.g. MNQ) — notional vs. NAV, direction, overnight risk.
- **🩺 Druckenmiller gut check:** *"If I started from cash today, would I buy each of these at today's price?"* Name any position that fails — that's the honest sell/trim candidate.
- **🗓️ Cluster risk:** any week where several holdings report earnings / a macro print hits the book's main bet — flag it (consider trimming option/LEAP exposure into it).

## Step 5 — 🔭 Three-lens deep read
Ground EVERY claim in Step 1/2/4 data. One tight paragraph (or bullets) per lens:
- **🦅 Druck:** the named regime + consensus score, the Fed/rate/liquidity master variable, whether today cut or added risk, and whether true (futures-inclusive) leverage fits the tape. If VIX > 20, address the contrarian-long signal explicitly (signal, not advice).
- **🚀 Gerstner:** are the AI/tech names durable compounders with the secular thesis intact, or momentum-chasing? Platform vs. point-solution; quality/efficiency of the exposure.
- **🎯 Baker:** semis/AI-infra conviction; concentration deliberate into the 2–3 things that matter, or drift? Rule-of-40 / quality sanity check on the biggest weights.

## Step 6 — 📝 Write the "Market Close" note
**Primary target:** `$VAULT_DIR/invest/daily-recaps/<YYYY-MM-DD> Market Close.md` (create the folder if missing; if today's note exists, **update** rather than duplicate). Follow the vault's `CLAUDE.md` conventions (structured H2→H3→bullets, link aggressively, minimal structured tags, a Connections section).

> 🔐 **Write-location fallback (important).** An **unattended/launchd run lacks macOS Full Disk Access to iCloud**, so writing under `$VAULT_DIR` (`~/Library/Mobile Documents/…`) fails with `open() → EPERM`. That is expected and fine. On any write failure there (or if `VAULT_DIR` is unset/unreachable), **fall back to `$GOVERNOR_HOME/daily-recaps/<YYYY-MM-DD> Market Close.md`** (a local, non-protected folder) and **state the fallback path in the Telegram recap**. **Never fail the run over the note write** — the Telegram recap + a saved note (vault *or* local fallback) must always succeed. Interactive runs (you're in Claude Code) write straight to the vault; only the scheduled job falls back.

Use **this template**:

```markdown
---
title: "<YYYY-MM-DD> Market Close — <≤12-word essence of the day>"
date: <YYYY-MM-DD>
note_type: market-close
source: ib-governor/daily-summary
nav: <nav>
realized_pnl_today: <realized>
gross_leverage: <lev>x
regime: "<named regime + consensus, e.g. 'Mid Expansion · Cautiously Bullish'>"
tags: [market-close, daily, "<YYYY-MM-DD>", trading, three-lens, regime/<regime-slug>]
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
---

# 🌙 Market Close — <YYYY-MM-DD>

> [!tldr] **<2–3 sentences: the day + the three-lens verdict + the single most important takeaway for the book.>**

## 📈 Market Backdrop & Regime
**Backdrop:** SPX <±%> · NDX <±%> · Dow <±%> · RUT <±%> · **VIX <level> (<calm | elevated → lean long>)**
- **Regime:** <named regime> → <one-line playbook> · **Cross-asset:** <Risk-On/Off score>
- **Breadth:** <broad vs narrow>
- **Rates / $ / commodities:** <10Y, 2Y, DXY, oil/gold if researched>
- **What moved it (causal):** <the day's real driver(s), sourced>
- *(When VIX > 20: one line on the contrarian-long signal — signal, not advice. Omit the section if feed + web were unavailable.)*

## 📊 Today's Trades
<table: Symbol · Side · Qty · Price · Realized P&L. If none: "No trades — book stance only.">

## 💰 P&L & Book
- **Realized today:** $<realized> · **NAV:** $<nav> · **Gross leverage:** <lev>x (**true econ. exposure incl. futures: ~<X>x**) · **Margin cushion:** <cushion%>
- **Top winners (unrealized):** <name +$> · <name +$> · <name +$>
- **Top losers (unrealized):** <name −$> · <name −$> · <name −$>

## 🔬 First-Principles Read
- **🎲 This book is a bet on:** <the 1–2 factors driving the P&L>
- **Dominant bets:** <theme — X% NAV> · <theme — Y%> · <theme — Z%>
- **Concentration:** <top names %; top sectors %; flag >15% name / >25% sector>
- **⚠️ Correlation risk:** <holdings sharing one macro sensitivity — "N names, 1 bet">
- **Pressing winners / watering losers?** <evidence from weights + today's fills>
- **🩺 Gut check (buy each at today's price?):** <fails, if any>

## 🛑 Brake Lines
<real trips only — rule_id · severity · message. Exclude the known "unknown sector" artifact. If clean: "None crossed.">

## 🔭 Three-Lens Read
### 🦅 Druckenmiller — macro & risk
<tight paragraph / bullets>
### 🚀 Gerstner — secular growth / AI
<tight paragraph / bullets>
### 🎯 Baker — concentrated conviction / semis-tech
<tight paragraph / bullets>

## ⚡ Tomorrow's Setup
- **📝 Today's View (one line):** <Druckenmiller-style directional lean>
- **Watch:** <catalysts, levels, earnings, macro prints>
- **If/then (kill criteria):** <e.g. "if 10Y breaks 4.55% → trim long-duration tech"; "if NDX loses <level> → cut the MNQ long">
- **Book action items:** <concrete, or "hold — no change warranted">

## 🔗 Connections
- [[<prior Market Close>]] · [[<relevant thesis/name notes>]] · [[<rule/plan notes>]]
> Suggested note: [[<missing note worth creating>]]
```

## Step 7 — 📤 Send the Telegram recap (formatted — `--html`)
Send a **scannable, formatted** recap with `--html` so it shows **bold section labels + blank-line spacing**, NOT one squished block. Keep it tight — the full analysis lives in the note.

**HTML rules (so it renders, not errors):** use `<b>…</b>` for section labels, emojis as anchors, and a blank line between sections. Telegram HTML treats `<`, `>`, `&` as special — so **avoid them in the text**: write "above"/"over" (not `>`), "and" (not `&`), "PnL" (not "P&L"), and use `−`/`-` for negatives. Quote the whole thing as one argument (real newlines).

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" -m governor.comms.send --html "🌙 <b>Market Close — <date></b>
<i><one-line essence — the day in ~5 words></i>

💰 <b>Book</b>
NAV <nav> · gross lev <lev>× (~<true>× true) · PnL today <realized> · cushion <c>%

📈 <b>Backdrop</b>
SPX <±%> · NDX <±%> · VIX <level> (<calm | elevated → lean long>)
Regime: <named regime · consensus score>

🎲 <b>The bet</b>
<one phrase — what the book is really long/short>

🔭 <b>Three-lens verdict</b>
<one or two lines — the honest call>

⚡ <b>Tomorrow</b>
<one-line directional View + the single top action / kill-criterion>

🛑 <b>Brake lines:</b> <real trips, or 'none'>"
```
- Drop the **Backdrop** block if the feed was unavailable — never fabricate a level. When VIX is elevated, keep the `(elevated → lean long)` tag.
- If the note went to the **local fallback** (unattended run, no iCloud access), add a final line: `📄 <b>Note:</b> local fallback`.

## Notes
- **Runs unattended** weekdays via launchd (`claude -p /daily-summary`) — sends Telegram + writes the note to the **local fallback** folder (`$GOVERNOR_HOME/daily-recaps/`), since launchd lacks iCloud access. Run it **by hand in Claude Code** any time to write straight to the Obsidian vault.
- **No fills?** Still produce the full note: the book's stance, the regime, the first-principles read, and the three-lens take on *doing nothing* (often the right call).
- **Right-sized:** this is a daily recap, not a per-name deep-dive — keep each lens to a paragraph; push deep single-name work to a dedicated research note and link it.
- **Read-only, always.** This skill never places, modifies, or cancels an order.

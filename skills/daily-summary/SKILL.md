---
name: daily-summary
description: End-of-day trade summary analyzed through three investor lenses (Druckenmiller · Gerstner · Baker). Pulls the day's fills / P&L / positions, sends a concise recap to Telegram, and optionally logs a full "Market Close" note to your research vault. Use at the close, or when asked for a daily trade summary/recap.
---

# Daily Trade Summary — Druckenmiller · Gerstner · Baker

Produce an end-of-day recap of the day's trading, analyzed through three lenses, then **send a concise version to Telegram** and (optionally) **log a full note to your research vault**. Read-only on the account — it pulls data, sends a message, writes a note; it places NO trades.

> **Setup:** set `GOVERNOR_HOME` to your ib-governor checkout (e.g. `export GOVERNOR_HOME=~/ib-governor`). Vault logging is optional — set `VAULT_DIR` to your notes folder to enable it; skip it to just send the Telegram recap.

The three lenses (use all three, in balance):
- **Stan Druckenmiller — macro & risk / regime.** Liquidity/rate/regime backdrop; does the book fit it? Are losers cut and winners pressed? Is size matched to conviction? Leverage discipline; the Fed/discount-rate as the master variable. Read the **📈 Market Backdrop** (index breadth + the VIX) as the regime/liquidity tape — **an elevated VIX (>20) is a contrarian-long signal** in this lens (fear is overpriced; consider leaning long / pressing high-conviction names). *Not whether you're right, but how much you make when right vs. lose when wrong.*
- **Brad Gerstner — secular growth / AI.** Are the trades consistent with the durable AI/secular thesis? Quality of the names? Riding the right wave, or chasing? Efficiency + conviction in compounders.
- **Gavin Baker — concentrated conviction / semis-tech.** Is exposure conviction-weighted into the 2–3 things that actually matter? Semis/compute cycle read. Is concentration deliberate, or drift?

## Step 1 — Pull the day's data (read-only)
```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" -m governor.live.daily --json
```
Parse: `date`, `nav`, `realized_pnl_today`, `margin_cushion`, `gross_leverage`, `fills[]` (today's executions), `positions[]` (current book), `trips[]` (breaker lines crossed), `indices` (broad-market move — `{SPY,QQQ,DIA,IWM}` → `{label, last, change_pct}` or `null`), `vix` (`{level, change_pct, elevated, signal}` or `null`). If it errors (TWS down), say so and STOP — don't fabricate numbers. The `indices`/`vix` feed is **best-effort**: any entry may be `null` (no market-data entitlement) — just omit what's missing, never fabricate a level.

### 📈 Market Backdrop (regime tape)
Before the lenses, read the day's broad-market context from `indices` + `vix`:
- **Index breadth:** report today's move for **S&P 500 (SPY) · Nasdaq 100 (QQQ) · Dow (DIA) · Russell 2000 (IWM)** as `change_pct` (e.g. "SPX +0.4% · NDX −0.2% · Dow +0.5% · RUT −0.5%"). Note breadth divergence (e.g. mega-cap up while small-caps down = narrow tape). Skip any index that came back `null`.
- **The VIX:** report `vix.level` (and `change_pct` vs. prior day). **When `vix.elevated` is true (VIX > 20)**, surface a clear note: *elevated fear is historically a **contrarian long** signal — consider leaning long / adding to high-conviction names.* Frame it explicitly as a **signal, not financial advice**. When calm (≤20), say so briefly.
- This is a **regime/liquidity read — wire it into the Druckenmiller lens** (does the book fit the tape? is an elevated-VIX contrarian-long tilt consistent with current leverage and conviction?).
- Produce a one-line **Backdrop:** string for reuse in the vault note and Telegram recap, e.g. `Backdrop: SPX +0.4% · NDX −0.2% · VIX 23 (elevated → lean long)` — or `VIX 14 (calm)` when not elevated. If the whole feed is `null`, write `Backdrop: market data unavailable` and move on.

## Step 2 — (Optional) Read your vault for voice + continuity
Only if `VAULT_DIR` is set:
1. FIRST read your vault conventions if present (e.g. `$VAULT_DIR/CLAUDE.md`) — match your own note structure, tags, and linking style.
2. Read your most recent morning/daily note to match its style and **close the loop on the day's setup** (did the day confirm or refute it?).
3. Skim your notes for the names actually traded today (link them).

Skip this step entirely if you don't keep a vault — the Telegram recap stands on its own.

## Step 3 — Analyze through the three lenses
Ground EVERY claim in the day's data (fills, realized P&L, positions, trips) — these three investors would not flatter a sloppy day. Cover:
- **Druck:** the regime read (rate/liquidity backdrop, e.g. a hawkish Fed as a discount-rate headwind to long-duration tech + leverage), whether today's trades cut risk or added to it, and whether index/futures exposure + current leverage fits the macro.
- **Gerstner:** are the tech/AI names quality compounders with the secular thesis intact, or is this momentum-chasing?
- **Baker:** semis/AI-infra conviction, concentration intent vs. drift.

Be honest and specific. Praise genuine discipline; call out churn, averaging-down, oversized risk, or fighting the regime.

## Step 4 — (Optional) Write the "Market Close" vault note
If `VAULT_DIR` is set, write to `$VAULT_DIR/<your trading-notes folder>/Market Close <YYYY-MM-DD>.md`, following your vault's conventions. Suggested structure:
- **Frontmatter:** `title`, `date`, `type: market-close`, `tags`, `created`, `updated`.
- `# 🌙 Market Close — <date>` + a **tl;dr blockquote** (2–3 sentences: the day + the three-lens verdict).
- `## 📈 Market Backdrop` — the index moves (SPX/NDX/Dow/RUT % today) + the VIX level; when `vix.elevated`, the one-line contrarian-long note (signal, not advice). Lead with the **Backdrop:** one-liner from Step 1a. Omit if the feed was entirely unavailable.
- `## 📊 Today's Trades` — table: symbol · side · qty · price · realized P&L. (If none: "no trades — book stance only".)
- `## 💰 P&L & Book` — realized today, NAV, leverage, margin cushion, top positions + unrealized.
- `## 🛑 Brake Lines` — any `trips` (e.g. notional, sector concentration), with the number.
- `## 🔭 Three-Lens Read` — ### Druckenmiller / ### Gerstner / ### Baker, each a tight paragraph or bullets.
- `## ⚡ Tomorrow's Setup` — action items / what to watch.
- `## Connections` — link related notes (your trading checklist, leverage plan, the relevant theses).

## Step 5 — Send a concise recap to Telegram
Keep it tight (Telegram-sized — the full analysis lives in the vault note, if you keep one):
```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" -m governor.comms.send "🌙 Market Close <date>
Realized <…> · NAV <…> · <N> trades · lev <…>x
Backdrop: SPX <±%> · NDX <±%> · VIX <level> (<elevated → lean long | calm>)
3-lens: <one-line verdict>
Lines: <trips or 'none'>"
```
(Drop the Backdrop line if the `indices`/`vix` feed was unavailable — never fabricate a level. When `vix.elevated`, the `(elevated → lean long)` tag flags the contrarian-long signal.)

## Notes
- Run manually at the close, or schedule it at EOD (a launchd-scheduled `claude -p /daily-summary`).
- If there were no fills, still produce a brief recap: the book's stance + any brake lines + the three-lens take on doing nothing.

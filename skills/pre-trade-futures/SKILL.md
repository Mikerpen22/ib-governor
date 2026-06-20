---
name: pre-trade-futures
description: Pre-trade gate for a futures order (MNQ, ES, NQ, MES, CL, …). Analyzes notional/leverage, margin, overnight risk, post-trade rule trips, setup quality, and (optionally) your vault learnings BEFORE you place it; refuses to submit until you confirm. Use when the user wants to trade a future.
---

# Pre-Trade Futures Analyst

Run BEFORE placing a deliberate futures trade. Combines the deterministic gate (notional, margin, rule trips, setup quality) with optional vault-grounded reasoning into a **GO / CAUTION / BLOCK** verdict. Nothing reaches the exchange without your explicit confirm.

For stocks, use `pre-trade-equities`.

> **Setup:** set `GOVERNOR_HOME` to your ib-governor checkout. `VAULT_DIR` (your research notes) is optional.

## Inputs
- direction → action: a long is `buy`; a **short** is `sell`. quantity (contracts), symbol (root, e.g. MNQ).
- order type (see Step 1): market / limit / stop / stop-limit, optionally `--adaptive`; prices for limit/stop.

## Step 1 — Choose the order type (help them pick)
Pick the type that fits the intent before running the gate. Show the full catalog with:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate order-types
```

…or recommend from this quick map:
- **Just get me in/out now, hands-off?** → **Adaptive Market** (`--type market --adaptive`, priority Normal) — better average fills than a naked market on a liquid future, and no limit price to guess.
- **Working a price?** → **Limit** (`--type limit --limit <px>`); add `--adaptive` to let the algo work it (priority Urgent if you'd rather get done than wait).
- **Breakout entry or a hard stop-out?** → **Stop** (`--type stop --stop <px>`) for fill-speed, or **Stop-Limit** (`--type stop-limit --stop <px> --limit <px>`) to cap slippage. (Adaptive is **not** valid on stops.)
- **Attach a protective stop / target?** → add `--stop-loss <px>` / `--take-profit <px>` to any entry (OCA-grouped, GTC legs — important for a futures position you might hold past the session).
- **Lifetime:** `--tif DAY|GTC` for the entry (default DAY); protective legs default `--protective-tif GTC` so an overnight stop isn't cancelled at the close.

State the recommended type (one line, with why), then carry the flags into Step 2.

## Step 2 — Deterministic gate (the facts)
Read-only; places NO order; stages a single-use ~5-min token:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate analyze <buy|sell> <qty> <ROOT> --sec-type fut \
  --type <market|limit|stop|stop-limit> [--adaptive] [--priority urgent|normal|patient] \
  [--limit <px>] [--stop <px>] [--stop-loss <px>] [--take-profit <px>] [--tif DAY|GTC] --json
```
("short 2 MNQ" → `analyze sell 2 MNQ --sec-type fut --type market --adaptive --json`.)

Parse the JSON — same fields as equities. The rules that matter for futures: `live_notional` (notional/NAV), `overnight_notional` (contracts into the close), `overtrading`, `same_contract_churn`, `house_money_lockout`, `daily_loss_stop` — these appear as entries inside `trips[].rule_id` (prefixed `futures.`), not as top-level keys. Note `order_notional`/`pct_nav` (one MNQ ≈ $40–60k notional — a few contracts is already a large fraction of NAV), `buying_power_ok`, `lockout_active`, and `token`.

The JSON also returns:
- `setup` — the four-factor read (see Step 4).
- `panels` — a pre-rendered string ready to paste at Step 6; contains 📋 ORDER / 💰 RISK & SIZING / 📈 SETUP.

If the gate errors (TWS down), report it and STOP.

## Step 3 — (Optional) Vault learnings (the judgment)
Only if you keep a research vault (`VAULT_DIR` set):
1. FIRST read your vault conventions if present (e.g. `$VAULT_DIR/CLAUDE.md`).
2. Search the vault for this contract + the macro/regime theme — your regime checklist, past futures post-mortems, and any trading rules you've written.
3. Surface what matters: is this consistent with your current macro/regime read, or an impulsive reaction?

Skip this step if you don't keep a vault.

## Step 4 — Setup (four-factor futures read, from the gate)
The gate now returns a `setup` block **and** a pre-rendered `panels` string directly in the JSON. For futures the setup is a four-factor structural read on the continuous contract's daily bars — **not** Minervini Stage-2 or VCP (those are equity-only). The four factors:

1. **Trend alignment** (`setup.futures.trend_label`, `with_trend`, `counter_trend`) — price vs the 20/50/200-day MAs on the continuous contract. A counter-trend trade (shorting an uptrend, buying a downtrend) is flagged 🔴; a mixed/choppy market 🟡; aligned with the dominant trend ✅.

2. **Volatility regime** (`setup.futures.vol_label`, `atr_pctile`) — ATR percentile over the prior 100 bars. `elevated` (above the 70th pctile) means wider stop requirements and larger adverse excursions; `compressed` may signal a breakout brewing. Elevated vol shows as 🟡 — stops and size should adjust accordingly.

3. **Location / extension** (`setup.futures.chasing`, `dist_from_high_pct`) — how close price is to the 20-day high/low. Chasing the range extreme (within 2% of the high for a long, or the low for a short) triggers 🔴 — it means you're buying the top or selling the bottom of the recent range.

4. **Momentum** (`setup.futures.momentum_label`, `rsi`) — RSI(14) / ROC. `overbought` (RSI > 70) on a long entry or `oversold` (RSI < 30) on a short flags 🟡 — momentum supports a fade, not the direction you're entering.

`setup.poor` is `true` when the gate has already escalated to CAUTION for setup reasons (counter-trend, chasing, or elevated vol). When it's true, the gate has already made the CAUTION call — your job in Steps 3–5 is to potentially escalate FURTHER via vault judgment, not to re-evaluate what the gate already decided.

`setup.caution_reasons` lists the human-readable strings the gate added to `reasons`.

A poor setup escalates the verdict to **CAUTION** — it never blocks (a BLOCK always comes from a hard rule or lockout, not a setup read).

## Step 5 — Synthesize the verdict
The futures questions that decide it:
- **Hedge vs. bet:** is this leverage *on a position you already hold* (a hedge), or a fresh directional bet/scalp?
- **Notional & margin:** what fraction of NAV is at risk; does it push `live_notional` / `overnight_notional` over the line?
- **Setup quality:** counter-trend, chasing the extreme, elevated vol, or stretched momentum?
- **Regime fit:** does it match your macro read?
- **House-money / loss state:** are you already up big today (house-money zone) or down (loss-stop zone)?

Escalate toward caution; never downgrade a deterministic BLOCK:
- **BLOCK** — gate blocks (active futures lockout, a HARD rule: daily-loss / overtrading / overnight-notional, or insufficient margin), or your vault rule says stop.
- **CAUTION** — notional climbing, a WARN rule trips, poor setup (counter-trend / chasing / elevated vol), it's a bet not a hedge, or it's house-money-churn territory.
- **GO** — sized sanely, regime-consistent, setup aligned, not into a lockout/loss state.

## Step 6 — Present, then confirm-gated submit
Show the confirmation screen in this exact order:

**1. Banner** — one line stating the FINAL verdict (yours, after vault escalation) and the order:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 GO  /  🟡 CAUTION  /  🔴 BLOCK  ·  <action> <qty> <ROOT> · <order type>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**2. Panels** — paste `preview["panels"]` verbatim. This is the pre-rendered 📋 ORDER / 💰 RISK & SIZING / 📈 SETUP output from the gate (no reformatting needed).

**3. 🧭 VERDICT** — one paragraph synthesizing the gate facts, setup read (trend alignment, vol regime, location, momentum), and vault into your call. State the one thing that would change it (e.g. "If price pulls back from the range high, this becomes a cleaner entry").

**4. 📓 VAULT** — the relevant notes from the vault: macro/regime read, past futures post-mortems, written rules that apply.

**5. Confirm line** — ask for explicit confirmation before submitting. Only if the user clearly confirms, submit with the staged token:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate submit --token <token> --json
```

Report placed vs `dry_run: true` (SAFE mode — logged, not sent; the shipped default). Never submit without an explicit confirm. Token is single-use, ~5-min TTL; if expired, re-run Step 2.

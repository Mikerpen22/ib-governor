---
name: pre-trade-equities
description: Pre-trade gate for an equities (stock) order. Analyzes size, concentration, post-trade rule trips, margin, and (optionally) your vault learnings BEFORE you place it, and refuses to submit until you explicitly confirm. Use when the user wants to buy/sell a stock.
---

# Pre-Trade Equities Analyst

Run this BEFORE placing a deliberate stock trade. It combines the **deterministic gate** (the hard numbers — margin, sizing, concentration, rule trips) with optional **vault-grounded reasoning** (your own notes on this name) into a single **GO / CAUTION / BLOCK** verdict. Nothing reaches the exchange without your explicit confirmation.

For futures (MNQ, ES, NQ, MES, CL, …) use `pre-trade-futures` instead.

> **Setup:** set `GOVERNOR_HOME` to your ib-governor checkout. `VAULT_DIR` (your research notes) is optional.

## Inputs
- `action` (buy / sell), `quantity`, `symbol`
- order type (see Step 1): market / limit / stop / stop-limit, optionally `--adaptive`; for limit/stop, the price(s)

## Step 1 — Choose the order type (help them pick)
Before running the gate, pick the order type that fits the user's intent. You can show the full catalog by running:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate order-types
```

…or just recommend from this quick map:
- **Want it filled now without watching the price?** → **Adaptive Market** (`--type market --adaptive`, priority Normal). Better average fills than a naked market, and no limit price to guess. This is the default recommendation for a "just get me in/out" stock order.
- **Have a price in mind / won't chase?** → **Limit** (`--type limit --limit <px>`). Add `--adaptive` to have the algo work it toward your limit.
- **Trigger on a level breaking (breakout entry or stop exit)?** → **Stop** (`--type stop --stop <px>`), or **Stop-Limit** (`--type stop-limit --stop <px> --limit <px>`) if you refuse to fill worse than a price. (Adaptive is **not** valid on stops.)
- **Want a protective stop / target attached to the entry?** → add `--stop-loss <px>` and/or `--take-profit <px>` to *any* entry above (OCA-grouped, GTC protective legs).
- **Lifetime:** `--tif DAY|GTC` for the entry (default DAY); protective legs default to `--protective-tif GTC` so a stop survives the session close.

State the recommended type (and why) in one line, then carry the chosen flags into Step 2.

## Step 2 — Deterministic gate (the facts)
Run the gate's READ-ONLY analysis with the chosen flags. It places NO order; it stages a single-use, ~5-minute token:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate analyze <buy|sell> <qty> <SYMBOL> --sec-type stk \
  --type <market|limit|stop|stop-limit> [--adaptive] [--priority urgent|normal|patient] \
  [--limit <px>] [--stop <px>] [--stop-loss <px>] [--take-profit <px>] [--tif DAY|GTC] --json
```

Examples:
- Just get me in, hands-off: `analyze buy 50 ORCL --sec-type stk --type market --adaptive --json`
- Limit with a protective stop: `analyze buy 50 ORCL --sec-type stk --type limit --limit 145 --stop-loss 138 --json`

Parse the JSON:
- `verdict` (GO/CAUTION/BLOCK) + `reasons` — the deterministic call.
- `order_notional`, `pct_nav` — trade size vs NAV (gate flags > 1.5%).
- `buying_power_ok`, `init_margin` — from IBKR whatIf.
- `name_weight_before` / `name_weight_after` — concentration impact of THIS trade.
- `trips` — rules that would fire on the post-trade book (single-name, sector, retrade-churn, add-into-drawdown, leverage, drawdown).
- `lockout_active` — whether a lockout blocks this trade.
- `token` — needed for Step 6.

If the gate errors (e.g. TWS not reachable), report it and STOP. Do not guess the numbers.

## Step 3 — (Optional) Vault learnings (the judgment)
Only if you keep a research vault (`VAULT_DIR` set):
1. FIRST read your vault conventions if present (e.g. `$VAULT_DIR/CLAUDE.md`).
2. Search the vault for the symbol and its theme — theses, post-mortems, and any prior lesson about this name or this kind of setup.
3. Surface what's relevant: Is there an active thesis? A prior loss on this name? A written rule about chasing, averaging down, or position size?

Skip this step if you don't keep a vault.

## Step 4 — Setup (Minervini, from the gate)
The gate now returns a `setup` block **and** a pre-rendered `panels` string directly in the JSON — no separate `/vcp` call is needed. The `setup` block includes:
- `setup.equity.stage2` — a 7-point Minervini checklist: MA stack (50/150/200), MA200 slope, 52-week position, and range ratio. Classified as `confirmed` (≥6/7), `candidate` (4–5), or `none` (≤3).
- `setup.equity.vcp` — VCP contraction sequence: pivot, distance from pivot (with band: `pre_breakout` = price still below the pivot / `actionable` = ≤5% above / `extended` = 5–10% above / `wait` = 10–15% above, let it come back / `too_late` = >15% above), last contraction grade (`excellent` / `good` / `acceptable` / `too_loose`), and volume dry-up flag. Note: the boolean `extended` flag (fires when price is >5% past pivot) is the gate trigger for CAUTION; the `distance_band` string is the finer-grained descriptive bucket.
- `setup.poor` — `true` when the gate has already escalated the verdict to CAUTION for setup reasons (not confirmed Stage 2, extended past pivot, or too-loose contraction). When `setup.poor` is true, the gate has already made the CAUTION call — your job in Steps 3–5 is to potentially escalate FURTHER via vault judgment, not to re-evaluate or double-count what the gate already decided.
- `setup.caution_reasons` — the human-readable strings the gate added to `reasons`.

The rendered `panels` string (ORDER / RISK & SIZING / SETUP) is ready to display verbatim — paste it directly into the confirmation screen in Step 6.

## Step 5 — Synthesize the verdict
Combine the deterministic facts (Step 2) with any vault judgment (Steps 3–4). You may **escalate** the gate's verdict (toward caution) but never **downgrade** a deterministic BLOCK:
- **BLOCK** — the gate blocks (active lockout, a HARD rule, or insufficient margin), OR your vault has an explicit rule against this trade.
- **CAUTION** — size > 1.5% NAV, concentration climbing toward a cap, a WARN rule trips, the entry looks extended, or your notes flag a relevant past mistake.
- **GO** — clean on the numbers AND consistent with your thesis.

Equities methodology to apply: size-vs-stop ≤ 1.5% NAV, the concentration impact of *this* add (`name_weight_before → after`), an extended/chasing check, and (if present) your vault thesis for the ticker.

## Step 6 — Present, then confirm-gated submit
Show the confirmation screen in this exact order:

**1. Banner** — one line stating the FINAL verdict (yours, after vault escalation) and the order:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 GO  /  🟡 CAUTION  /  🔴 BLOCK  ·  <action> <qty> <SYMBOL> · <order type>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**2. Panels** — paste `preview["panels"]` verbatim. This is the pre-rendered ORDER / RISK & SIZING / SETUP output from the gate (no reformatting needed).

**3. 🧭 VERDICT** — one paragraph synthesizing the gate facts, setup read, and vault into your call. State the one thing that would change it (e.g. "If price pulls back to the pivot, this becomes a clean actionable entry").

**4. 📓 VAULT** — the relevant notes from the vault: active thesis, prior losses on this name, written rules that apply.

**5. Confirm line** — ask for explicit confirmation before submitting. Only if the user clearly confirms, submit with the staged token:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate submit --token <token> --json
```

Report the result. If `dry_run: true`, the gate is in SAFE mode — the order was logged, NOT sent (this is the shipped default). Never submit without an explicit confirm. The token is single-use and expires (~5 min); if expired, re-run Step 2.

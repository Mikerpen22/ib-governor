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

## Step 4 — (Optional) Compose other analysis
If you have companion analysis tools, use them as relevant — e.g. a Stage-2 / VCP read (sound entry vs. extended/chasing), or a current portfolio leverage + concentration check.

## Step 5 — Synthesize the verdict
Combine the deterministic facts (Step 2) with any vault judgment (Steps 3–4). You may **escalate** the gate's verdict (toward caution) but never **downgrade** a deterministic BLOCK:
- **BLOCK** — the gate blocks (active lockout, a HARD rule, or insufficient margin), OR your vault has an explicit rule against this trade.
- **CAUTION** — size > 1.5% NAV, concentration climbing toward a cap, a WARN rule trips, the entry looks extended, or your notes flag a relevant past mistake.
- **GO** — clean on the numbers AND consistent with your thesis.

Equities methodology to apply: size-vs-stop ≤ 1.5% NAV, the concentration impact of *this* add (`name_weight_before → after`), an extended/chasing check, and (if present) your vault thesis for the ticker.

## Step 6 — Present, then confirm-gated submit
Show the user, concisely:
- the trade (action / qty / symbol / type / price),
- the key facts (size %, margin, concentration before→after, any trips),
- the vault learnings that matter (if any),
- your **VERDICT** with one-paragraph reasoning.

Then **ask for explicit confirmation.** Only if the user clearly confirms, submit with the staged token:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate submit --token <token> --json
```

Report the result. If `dry_run: true`, the gate is in SAFE mode — the order was logged, NOT sent (this is the shipped default). Never submit without an explicit confirm. The token is single-use and expires (~5 min); if expired, re-run Step 2.

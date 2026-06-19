---
name: pre-trade-futures
description: Pre-trade gate for a futures order (MNQ, ES, NQ, MES, CL, …). Analyzes notional/leverage, margin, overnight risk, post-trade rule trips, and (optionally) your vault learnings BEFORE you place it; refuses to submit until you confirm. Use when the user wants to trade a future.
---

# Pre-Trade Futures Analyst

Run BEFORE placing a deliberate futures trade. Combines the deterministic gate (notional, margin, rule trips) with optional vault-grounded reasoning into a **GO / CAUTION / BLOCK** verdict. Nothing reaches the exchange without your explicit confirm.

For stocks, use `pre-trade-equities`.

> **Setup:** set `GOVERNOR_HOME` to your ib-governor checkout. `VAULT_DIR` (your research notes) is optional.

## Inputs
- direction → action: a long is `buy`; a **short** is `sell`. quantity (contracts), symbol (root, e.g. MNQ).
- order type: market / limit / stop / stop-limit (default market); prices for limit/stop.

## Step 1 — Deterministic gate (the facts)
Read-only; places NO order; stages a single-use ~5-min token:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate analyze <buy|sell> <qty> <ROOT> --sec-type fut \
  --type <market|limit|stop|stop-limit> [--limit <px>] [--stop <px>] --json
```
("short 2 MNQ" → `analyze sell 2 MNQ --sec-type fut ...`.)

Parse the JSON — same fields as equities. The rules that matter for futures: `live_notional` (notional/NAV), `overnight_notional` (contracts into the close), `overtrading`, `same_contract_churn`, `house_money_lockout`, `daily_loss_stop` — these appear as entries inside `trips[].rule_id` (prefixed `futures.`), not as top-level keys. Note `order_notional`/`pct_nav` (one MNQ ≈ $40–60k notional — a few contracts is already a large fraction of NAV), `buying_power_ok`, `lockout_active`, and `token`.

If the gate errors (TWS down), report it and STOP.

## Step 2 — (Optional) Vault learnings (the judgment)
Only if you keep a research vault (`VAULT_DIR` set):
1. FIRST read your vault conventions if present (e.g. `$VAULT_DIR/CLAUDE.md`).
2. Search the vault for this contract + the macro/regime theme — your regime checklist, past futures post-mortems, and any trading rules you've written.
3. Surface what matters: is this consistent with your current macro/regime read, or an impulsive reaction?

Skip this step if you don't keep a vault.

## Step 3 — (Optional) Compose other analysis
If available: a leverage / margin / futures-notional read, and a trend/structure chart for the entry.

## Step 4 — Synthesize the verdict
The futures questions that decide it:
- **Hedge vs. bet:** is this leverage *on a position you already hold* (a hedge), or a fresh directional bet/scalp?
- **Notional & margin:** what fraction of NAV is at risk; does it push `live_notional` / `overnight_notional` over the line?
- **Regime fit:** does it match your macro read?
- **House-money / loss state:** are you already up big today (house-money zone) or down (loss-stop zone)?

Escalate toward caution; never downgrade a deterministic BLOCK:
- **BLOCK** — gate blocks (active futures lockout, a HARD rule: daily-loss / overtrading / overnight-notional, or insufficient margin), or your vault rule says stop.
- **CAUTION** — notional climbing, a WARN rule trips, it's a bet not a hedge, or it's house-money-churn territory.
- **GO** — sized sanely, regime-consistent, not into a lockout/loss state.

## Step 5 — Present, then confirm-gated submit
Show: the trade, the facts (notional %, margin, any trips, lockout), the regime read, and your **VERDICT** + reasoning. **Ask for explicit confirmation.** Only on a clear confirm:

```bash
PYTHONPATH="$GOVERNOR_HOME/src" "$GOVERNOR_HOME/.venv/bin/python" \
  -m governor.gate submit --token <token> --json
```
Report placed vs `dry_run: true` (SAFE mode — logged, not sent; the shipped default). Never submit without an explicit confirm. Token is single-use, ~5-min TTL; if expired, re-run Step 1.

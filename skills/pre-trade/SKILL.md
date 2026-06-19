---
name: pre-trade
description: Pre-trade brake — analyze any deliberate stock or futures order against your rules, margin, concentration, and vault learnings before placing it. Routes to the equities or futures analyst by asset class. Use whenever the user wants to place a trade.
---

# Pre-Trade Gate (dispatcher)

The entry point for the pre-trade brake. Parse the intent, detect the asset class, route to the matching analyst. The brake's whole purpose: no deliberate trade reaches the exchange without being checked against the rules + the vault, and confirmed.

## Parse the intent
Extract: action, quantity, symbol, order type, any price.
- "buy 50 ORCL at 145" → buy, 50, ORCL, limit, 145
- "buy 200 NVDA" → buy, 200, NVDA, market
- "short 2 MNQ" → sell, 2, MNQ (futures)
- "sell 100 AAPL stop 180" → sell, 100, AAPL, stop, 180

"short" = sell. If the order type is ambiguous, ask; otherwise default to market.

## Detect asset class → route
- **Futures** if the symbol is a futures root: MNQ, MES, ES, NQ, M2K, RTY, YM, MYM, CL, MCL, GC, MGC, SI, NG, HG, ZN, ZB, ZF, ZT, 6E, 6J, 6B, … (CME / CBOT / NYMEX / COMEX roots). → use **`pre-trade-futures`**.
- **Equities** otherwise (an ordinary stock ticker). → use **`pre-trade-equities`**.

If you can't tell whether a symbol is a stock or a future, ASK before routing.

## Hand off
Invoke the chosen analyst skill. It runs the deterministic gate (`python -m governor.gate analyze … --json`), reads the vault, synthesizes a GO / CAUTION / BLOCK verdict, and owns the confirm-and-submit flow. Do NOT place any order yourself — the analyst handles confirmation and the staged-token submit.

# ib-governor

A local **behavioral circuit-breaker + pre-trade gate** for Interactive Brokers (IBKR). A governor caps an engine's max RPM; **ib-governor** caps overtrading — it mechanizes per-asset risk rules and a confirm-gated pre-trade gate, so a disciplined plan actually gets followed. It targets a common discipline failure: giving back gains by overtrading after a win. The rules are mechanized deterministically, and **nothing ever touches the account without an explicit confirm.**

Two machines, one goal — keep *deliberate* trades disciplined and *impulsive* churn braked:

1. **Pre-trade gate** (proactive). Before a deliberate order, analyzes the trade against rules, live IBKR margin/sizing/concentration, and (optionally) research-notes learnings, then returns **GO / CAUTION / BLOCK** — and places it only after you confirm.
2. **Circuit-breaker daemon** (reactive). An always-on process watches account state; when a self-imposed line is crossed, it alerts and *stages* a confirm-gated corrective action.

Both require an explicit human confirm. Neither acts on the account by itself.

## Quick Links

| | |
|---|---|
| [Operator's Handbook](HANDBOOK.md) | How to drive it day to day, with diagrams and worked examples |
| [Rule Catalog](RULES.md) | All 13 rules across futures/equities/portfolio |
| [Architecture & Lessons](FORCLAUDE.md) | Deep-dive into how and why it's built this way |
| [Security](SECURITY.md) | Threat model, audit findings, and arming checklist |
| [API Reference](reference/index.md) | Auto-generated from docstrings in `src/governor/` |

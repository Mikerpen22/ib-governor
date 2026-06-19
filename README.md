# ib-governor

A local **behavioral circuit-breaker + pre-trade gate** for Interactive Brokers (IBKR). A mechanical governor caps an engine's maximum RPM; **ib-governor caps overtrading** — it mechanizes per-asset risk rules and a confirm-gated pre-trade check so a disciplined plan actually gets followed. It targets a common, well-documented failure mode: the analysis is usually fine — the inability to *stop* trading after a win is what quietly gives the gains back.

**Nothing ever touches your account without an explicit confirm**, and it **ships in dry-run / read-only mode** — you arm it deliberately.

> ⚠️ **Disclaimer.** This software can connect to a live brokerage account and place real orders. It is provided "as is", without warranty of any kind, and is **not financial advice**. You are solely responsible for any trades, losses, or account actions. It ships in dry-run + read-only mode; **you arm it at your own risk.** Validate against an IBKR **paper** account (port 7497) first. Licensed under [Apache-2.0](LICENSE).

> 📘 **New here?** Read **[AGENTS.md](AGENTS.md)** to get running in ~60 seconds, or the **[Operator's Handbook](docs/HANDBOOK.md)** for day-to-day use with diagrams and worked examples.

## Documentation
- **[AGENTS.md](AGENTS.md)** — clone-and-go setup for humans and coding agents.
- **Local docs site:** `make docs` (or `pip install -e ".[docs]" && mkdocs serve`) → <http://localhost:8000>.
- **Published site:** <https://mikerpen22.github.io/ib-governor/> (live once GitHub Pages is enabled).
- **Guides:** [Operator's Handbook](docs/HANDBOOK.md) · [Architecture & Lessons](docs/FORCLAUDE.md) · [Security](docs/SECURITY.md) · [Rule Catalog](docs/RULES.md). The API reference is auto-generated from the docstrings in `src/governor/`.

## Quick start
```bash
git clone https://github.com/Mikerpen22/ib-governor && cd ib-governor
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # or: make setup
export GOVERNOR_HOME="$(pwd)"       # the skills + launchd template reference this

pytest -q                          # full suite — green WITHOUT TWS (live tests skip)

cp .env.example .env               # optional: add a Telegram bot token for alerts
python -m governor.live.daemon     # the circuit-breaker — ships dry-run/read-only
```

## What it does
Two machines, one goal — keep *deliberate* trades disciplined and *impulsive* churn braked:

1. **Pre-trade gate** (proactive). Before a deliberate order, it analyzes the trade against your rules, live IBKR margin/sizing/concentration, and (optionally) your research-vault notes, and returns **GO / CAUTION / BLOCK** — then places it only after you confirm.
2. **Circuit-breaker daemon** (reactive). An always-on process watches account state; when a self-imposed line is crossed (house-money, daily loss, overtrading, oversized overnight, concentration…), it alerts and *stages* a confirm-gated corrective action.

Both are grounded in your own rules (and, optionally, a research vault). Both require an explicit human confirm. Neither acts on the account by itself.

## Architecture
Four layers, each with one job (full deep-dive in [FORCLAUDE.md](docs/FORCLAUDE.md)):

- **Brain-stem** — the Python daemon (`src/governor/live`): persistent IBKR connection, deterministic rule engine, staged actions. **No LLM in the hot path** — the trip decision is dumb, fast, auditable Python.
- **Cortex** — the on-demand [Claude Code skills](#claude-code-skills) (`skills/`): LLM analysts that add judgment + optional vault grounding, both at the gate and at the close.
- **Senses** — IBKR via `ib_async` (live data) and `whatIfOrder` (margin preview).
- **Memory** — `config/rules.yaml` (thresholds) + (optionally) your research vault.

## The pre-trade gate
Use the pre-trade skills (they route by asset class), or call the CLI directly:

```bash
# analyze — READ-ONLY, places nothing; prints GO/CAUTION/BLOCK + a single-use token
python -m governor.gate analyze buy 50 ORCL --sec-type stk --type limit --limit 145 --json

# submit — the ONLY write path; needs the staged token AND your confirmation
python -m governor.gate submit --token <TOKEN>
```

The analysis covers: IBKR `whatIf` margin, per-trade size vs a 1.5%-NAV band, the concentration impact of *this* trade, every rule that would fire on the *post-trade* book, and any active lockout. Order types: `market` · `limit` · `stop` · `stop-limit`. The skills (`skills/pre-trade-equities`, `skills/pre-trade-futures`) layer optional vault learnings on top of these deterministic facts.

## The circuit-breaker daemon
`python -m governor.live.daemon` holds a persistent IBKR connection and re-evaluates the rule set the instant a fill arrives, plus scheduled daily briefings. On a trip it sends a loud alert (Telegram + desktop) and stages a confirm-gated action — **lockout**, **platform-off** (cancel orders), or **trim**. You reply `CONFIRM <token>` to execute; tokens are single-use and expire.

## Claude Code skills
ib-governor ships four [Claude Code](https://claude.com/claude-code) skills (in `skills/`) — the optional **LLM-analyst layer** that wraps the deterministic engine with judgment and (optionally) your own research notes. The Python CLI works without them; the skills make it conversational.

| Skill | What it does | Use when |
|-------|--------------|----------|
| **`/pre-trade`** | Router — analyzes any deliberate stock *or* futures order against your rules, margin, and concentration, then hands off to the right analyst below. | placing any trade |
| **`/pre-trade-equities`** | Stock pre-trade gate: size vs a 1.5%-NAV band, the concentration impact of *this* add, post-trade rule trips, IBKR margin, optional vault thesis → **GO / CAUTION / BLOCK**, confirm-gated. | buying/selling a stock |
| **`/pre-trade-futures`** | Futures pre-trade gate: notional/leverage, overnight risk, margin, plus a hedge-vs-bet and regime read → **GO / CAUTION / BLOCK**, confirm-gated. | trading MNQ/ES/NQ/CL/… |
| **`/daily-summary`** | End-of-day recap through three investor lenses — **Druckenmiller** (macro/risk), **Gerstner** (secular/AI), **Baker** (concentrated conviction) — sent to Telegram and, optionally, logged as a "Market Close" note to your vault. Read-only. | at the close |

Install them by symlinking into Claude Code's skills directory:
```bash
for s in pre-trade pre-trade-equities pre-trade-futures daily-summary; do
  ln -s "$GOVERNOR_HOME/skills/$s" "$HOME/.claude/skills/$s"
done
```
The three pre-trade skills never reach the exchange without your explicit confirm; `/daily-summary` only reads. All layer optional `$VAULT_DIR` research notes on top of the deterministic facts.

## Rules & configuration
- **Rule catalog:** [docs/RULES.md](docs/RULES.md) — all rules across futures/equities/portfolio, each with its `rules.yaml` config key, severity, and what trips it. Generated from `src/governor/rules/catalog.py` (regenerate: `python -m governor.rules.catalog`); a test fails if it drifts.
- **Thresholds:** `config/rules.yaml` — every number is yours, validated on load. No threshold is hardcoded in rule logic.
- **Secrets:** `.env` (Telegram bot token + chat id) — see `.env.example`. IBKR host/port/client_id live in `config/rules.yaml` (config, not secrets).

## Safety model
- **Nothing auto-fires.** Every account-affecting action is staged behind a single-use, expiring confirm token.
- **Two default-closed locks.** `live.dry_run: true` (logs intent, never executes) **and** `live.readonly: true` (the IBKR connection itself rejects writes). *Both* must be flipped to place a live order.
- **Single write chokepoint.** Exactly one module — `src/governor/actions/executor.py` — calls an IBKR write method, and every such call passes through the dry-run gate.
- **Fail loud, fail closed.** Lost/stale data → a "BRAKE BLIND" alert (never assume all-clear). A present-but-unreadable lockout file is treated as *locked*, never clear.

## Arming
ib-governor ships SAFE. The full pre-flight checklist is in [SECURITY.md](docs/SECURITY.md). The essentials before setting `live.dry_run: false`:

1. **Telegram** configured + validated in `.env` (watch the alert → `CONFIRM` loop end-to-end on a dry-run daemon first).
2. **Read-only off** — turn off TWS's "Read-Only API" setting *and* set `live.readonly: false` (both locks).
3. **Paper first** — validate against an IBKR paper account (port 7497) before pointing it at live (port 7496).

**Never commit `config/rules.yaml` in an armed state** (`dry_run`/`readonly: false`).

## Development
- **Stack:** Python 3.12, `ib_async`, `pydantic` v2, `httpx` (Telegram), `pytest`.
- **Tests:** `pytest -q` — green on a fresh clone; live tests skip without TWS. Real IBKR response shapes can be captured into fixtures via `scripts/capture_fixtures.py` (keep committed fixtures **synthetic** — see the script's header).
- **Layout:** `src/governor/{model,config,rules,live,actions,comms,state,gate}` · `skills/pre-trade*` · `tests/` · `docs/`.
- **Setup & contributing:** [CONTRIBUTING.md](CONTRIBUTING.md) — newcomer-friendly dev setup, commands, env vars, and conventions (plus [AGENTS.md](AGENTS.md) for a 60-second orientation). **Deep-dive:** [FORCLAUDE.md](docs/FORCLAUDE.md).
- **License:** [Apache-2.0](LICENSE).

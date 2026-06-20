# ib-governor

A local **behavioral circuit-breaker + pre-trade gate** for Interactive Brokers (IBKR). A mechanical governor caps an engine's maximum RPM; **ib-governor caps overtrading** — it mechanizes per-asset risk rules and a confirm-gated pre-trade check so a disciplined plan actually gets followed. It targets a common, well-documented failure mode: the analysis is usually fine — the inability to *stop* trading after a win is what quietly gives the gains back.

**Nothing ever touches your account without an explicit confirm**, and it **ships in dry-run / read-only mode** — you arm it deliberately.

> ⚠️ **Disclaimer.** This software can connect to a live brokerage account and place real orders. It is provided "as is", without warranty of any kind, and is **not financial advice**. You are solely responsible for any trades, losses, or account actions. It ships in dry-run + read-only mode; **you arm it at your own risk.** Validate against an IBKR **paper** account (port 7497) first. Licensed under [Apache-2.0](LICENSE).

> 📘 **New here?** Read **[AGENTS.md](AGENTS.md)** to get running in ~60 seconds, or the **[Operator's Handbook](docs/HANDBOOK.md)** for day-to-day use with diagrams and worked examples.

## Documentation map
This README is the comprehensive front door — what it is, how it flows, the options, the guardrails, the integrations. Each section links to the deep doc for detail:

- **[AGENTS.md](AGENTS.md)** — clone-and-go setup for humans and coding agents (the [setup required](#setup-required)).
- **[Operator's Handbook](docs/HANDBOOK.md)** — drive it day-to-day: reading a verdict, the [setup read](docs/HANDBOOK.md#6b-the-setup-read--setup-panel), arming, troubleshooting, command cheat-sheet.
- **[Architecture & Lessons](docs/FORCLAUDE.md)** — how and why it's built this way, phase by phase.
- **[Rule Catalog](docs/RULES.md)** — every rule, its `rules.yaml` key, severity, and trigger (generated from code, can't drift).
- **[Order Types](docs/ORDER_TYPES.md)** · **[Security](docs/SECURITY.md)** · **[Contributing](CONTRIBUTING.md)** · API reference: `make docs` → <http://localhost:8000>.

## Setup required
```bash
git clone https://github.com/Mikerpen22/ib-governor && cd ib-governor
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"             # or: make setup
export GOVERNOR_HOME="$(pwd)"        # the skills + launchd templates reference this

pytest -q                           # full suite — green WITHOUT TWS (live tests skip)

cp .env.example .env                # optional: Telegram bot token + chat id for alerts
python -m governor.live.daemon      # the circuit-breaker — ships dry-run/read-only
```
Prerequisites: **Python 3.12**, an **IBKR TWS or Gateway** running with the API enabled (port 7497 paper / 7496 live), and — to receive alerts — a **Telegram** bot. That's it to run in safe mode. Arming (placing real orders) is a separate, deliberate step — see [Arming](#arming). Full onboarding + project layout: **[AGENTS.md](AGENTS.md)**.

## What it does
Two machines, one goal — keep *deliberate* trades disciplined and *impulsive* churn braked:

1. **Pre-trade gate** (proactive). Before a deliberate order, it analyzes the trade against your rules, live IBKR margin/sizing/concentration, **a systematic setup-quality read of the chart**, and (optionally) your research-vault notes, and returns **GO / CAUTION / BLOCK** — then places it only after you confirm.
2. **Circuit-breaker daemon** (reactive). An always-on process watches account state; when a self-imposed line is crossed (house-money, daily loss, overtrading, oversized overnight, concentration…), it alerts and *stages* a confirm-gated corrective action.

Both are grounded in your own rules (and, optionally, a research vault). Both require an explicit human confirm. Neither acts on the account by itself.

## How it flows (control flow)
**The pre-trade gate** — one read-only analysis on a single TWS connection, then a confirm-gated write:
```
  /pre-trade buy 50 ORCL          (or: python -m governor.gate analyze …)
        │
        ▼
  analyze  ── READ-ONLY, one IBKR connection, places nothing ──────────────┐
   ├─ risk  : whatIf margin · size vs 1.5% NAV band · concentration before→after  │
   │          · every rule that would fire on the POST-TRADE book · active lockout │
   ├─ setup : 1Y daily bars → Stage 2 + VCP (stocks) | trend·vol·location·momentum │
   │          (futures).  A poor/extended setup escalates to CAUTION (never blocks)│
   └─ vault : your notes on this name / theme (optional)                           │
        │                                                                          │
        ▼                                                                          │
  decide → 🟢 GO / 🟡 CAUTION / 🔴 BLOCK   + a single-use, ~5-min token ◀──────────┘
        │
        ▼
  you confirm → submit --token   ── the ONLY write path; honors dry_run ──▶ order
```
**The circuit-breaker daemon** — always-on, edge-triggered, confirm-gated:
```
  fill arrives ─▶ rebuild account snapshot ─▶ deterministic rule engine
                                                   │  any trip?
                        no ◀──────────────────────┼──────────────────────▶ yes
                  (log; stay silent)                                        │
                                              loud alert (Telegram + desktop) + STAGE action
                                                                            │
                                       you reply  CONFIRM <token>  ─▶  execute
                                                                      (lockout · platform-off · trim)
```
No LLM sits in either hot path — the trip/verdict decision is deterministic, fast, auditable Python. The LLM skills are an optional layer *around* it.

## Architecture
Four layers, each with one job (full deep-dive in [FORCLAUDE.md](docs/FORCLAUDE.md)):

- **Brain-stem** — the Python daemon (`src/governor/live`): persistent IBKR connection, deterministic rule engine, staged actions. **No LLM in the hot path** — the trip decision is dumb, fast, auditable Python.
- **Cortex** — the on-demand [Claude Code skills](#claude-code-skills) (`skills/`): LLM analysts that add judgment + optional vault grounding, both at the gate and at the close.
- **Senses** — IBKR via `ib_async`: live account data, **historical bars (the setup read)**, and `whatIfOrder` margin preview.
- **Memory** — `config/rules.yaml` (thresholds) + (optionally) your research vault.

## The pre-trade gate
Use the pre-trade skills (they route by asset class), or call the CLI directly:

```bash
# analyze — READ-ONLY, places nothing; prints GO/CAUTION/BLOCK + a single-use token
python -m governor.gate analyze buy 50 ORCL --sec-type stk --type limit --limit 145 --json

# submit — the ONLY write path; needs the staged token AND your confirmation
python -m governor.gate submit --token <TOKEN>
```

Every analysis composes three things into one verdict:
- **Risk (the numbers):** IBKR `whatIf` margin, per-trade size vs a 1.5%-NAV band, the concentration impact of *this* trade (name weight before→after), every rule that would fire on the *post-trade* book, and any active lockout.
- **Setup (the chart):** the gate fetches the symbol's daily bars on its existing connection and runs a systematic read — see [The setup read](#the-setup-read) below. A poor or extended setup escalates the verdict to CAUTION; it never blocks on its own.
- **Vault (your judgment):** the skills optionally layer your own research notes (`$VAULT_DIR`) on top of the deterministic facts.

Order types: `market` · `limit` · `stop` · `stop-limit`, with optional `--adaptive`, protective `--stop-loss`/`--take-profit` brackets, and `--tif` — full catalog in [ORDER_TYPES.md](docs/ORDER_TYPES.md).

## The setup read
Beyond *can I afford this trade*, the gate now answers *is this a sound entry* — computed in pure Python from real IBKR bars, one connection, fail-soft (no bars → the panel is simply absent, risk verdict unaffected):

- **Equities — Minervini Stage 2 + VCP:** the 7-criterion Stage-2 trend test (price vs MA50/150/200, MA stack, MA200 slope, 52-week position, range expansion) plus VCP contraction detection (pivot, distance band, volume dry-up). Buying well past the pivot, or a name that isn't in a confirmed Stage 2, reads as a chase → CAUTION.
- **Futures — a four-factor structural read:** trend alignment (vs 20/50/200), volatility regime (ATR percentile), location/extension (distance from the 20-day high/low), and momentum (RSI). Counter-trend, chasing the range extreme, or elevated vol → CAUTION.

It renders as a 📈 SETUP panel beside the risk panel. Full panel guide + the `setup:` tuning knobs: **[Handbook §6b](docs/HANDBOOK.md#6b-the-setup-read--setup-panel)**.

## The circuit-breaker daemon
`python -m governor.live.daemon` holds a persistent IBKR connection and re-evaluates the rule set the instant a fill arrives, plus scheduled daily briefings. On a trip it sends a loud alert (Telegram + desktop) and stages a confirm-gated action — **lockout**, **platform-off** (cancel orders), or **trim**. You reply `CONFIRM <token>` to execute; tokens are single-use and expire. Soft warnings are edge-triggered (announced once, not re-spammed); a dropped or stale feed raises a **BRAKE BLIND** alert rather than assuming all-clear.

## Claude Code skills
ib-governor ships four [Claude Code](https://claude.com/claude-code) skills (in `skills/`) — the optional **LLM-analyst layer** that wraps the deterministic engine with judgment and (optionally) your own research notes. The Python CLI works without them; the skills make it conversational.

| Skill | What it does | Use when |
|-------|--------------|----------|
| **`/pre-trade`** | Router — detects asset class and hands off to the right analyst below. | placing any trade |
| **`/pre-trade-equities`** | Stock pre-trade gate: systematic **Minervini Stage 2 + VCP** read on real bars, size vs a 1.5%-NAV band, the concentration impact of *this* add, post-trade rule trips, IBKR margin, optional vault thesis → **GO / CAUTION / BLOCK**, confirm-gated. | buying/selling a stock |
| **`/pre-trade-futures`** | Futures pre-trade gate: a four-factor setup read (**trend · volatility regime · location · momentum**), notional/leverage, overnight risk, margin, optional vault → **GO / CAUTION / BLOCK**, confirm-gated. | trading MNQ/ES/NQ/CL/… |
| **`/daily-summary`** | End-of-day recap through three investor lenses — **Druckenmiller** (macro/risk), **Gerstner** (secular/AI), **Baker** (concentrated conviction) — sent to Telegram and, optionally, logged as a "Market Close" note to your vault. Read-only. | at the close |

Install them by symlinking into Claude Code's skills directory:
```bash
for s in pre-trade pre-trade-equities pre-trade-futures daily-summary; do
  ln -s "$GOVERNOR_HOME/skills/$s" "$HOME/.claude/skills/$s"
done
```
The three pre-trade skills never reach the exchange without your explicit confirm; `/daily-summary` only reads. All layer optional `$VAULT_DIR` research notes on top of the deterministic facts.

## Integrations
| Integration | Role | Configured in |
|-------------|------|---------------|
| **IBKR TWS / Gateway** (`ib_async`) | Live account state, historical bars (the setup read), `whatIf` margin preview, and order placement (the single write path). | `config/rules.yaml` → `live:` (host/port/client id) |
| **Telegram** (`httpx`) | Loud trip alerts + the `CONFIRM <token>` reply loop that arms each action. | `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) |
| **Obsidian / research vault** (optional) | The skills read your notes to ground verdicts; `/daily-summary` can log a Market Close note. | `$VAULT_DIR` env var |
| **macOS launchd** (optional) | Keep the daemon always-on (auto-restart) and schedule the daily summary. | templates in `launchd/`; wiring in [Handbook §5](docs/HANDBOOK.md) |

## Rules & configuration (all the options)
Every number is yours, validated on load — nothing is hardcoded in rule logic.

- **Risk rules:** [docs/RULES.md](docs/RULES.md) — all rules across futures/equities/portfolio, each with its `rules.yaml` config key, severity, and trigger. Generated from `src/governor/rules/catalog.py` (regenerate: `python -m governor.rules.catalog`; a test fails if it drifts).
- **Setup read:** the `setup:` block in `config/rules.yaml` tunes the Stage-2/VCP and futures thresholds — full table in [Handbook §6b](docs/HANDBOOK.md#6b-the-setup-read--setup-panel).
- **Order types:** [docs/ORDER_TYPES.md](docs/ORDER_TYPES.md) — the catalog, plus Adaptive, bracketing, and TIF.
- **Live wiring:** `config/rules.yaml` → `live:` — IBKR host/port/client id, session/briefing times, and the two safety locks (`dry_run`, `readonly`).
- **Secrets:** `.env` (Telegram only) — see `.env.example`. IBKR connection details are config, not secrets.

## Safety model & guardrails
- **Nothing auto-fires.** Every account-affecting action is staged behind a single-use, expiring confirm token.
- **Two default-closed locks.** `live.dry_run: true` (logs intent, never executes) **and** `live.readonly: true` (the IBKR connection itself rejects writes). *Both* must be flipped to place a live order.
- **Single write chokepoint.** Exactly one module — `src/governor/actions/executor.py` — calls an IBKR write method, and every such call passes through the dry-run gate. The setup read and all analysis are strictly read-only.
- **Setup escalates, never blocks.** A poor setup can only raise the verdict to CAUTION — it never produces a BLOCK and never downgrades a hard stop.
- **Fail loud, fail closed.** Lost/stale data → a "BRAKE BLIND" alert (never assume all-clear). A present-but-unreadable lockout file is treated as *locked*, never clear.

Full threat model, audit findings, and the trust boundary: [SECURITY.md](docs/SECURITY.md).

## Arming
ib-governor ships SAFE. The full pre-flight checklist is in [SECURITY.md](docs/SECURITY.md). The essentials before setting `live.dry_run: false`:

1. **Telegram** configured + validated in `.env` (watch the alert → `CONFIRM` loop end-to-end on a dry-run daemon first).
2. **Read-only off** — turn off TWS's "Read-Only API" setting *and* set `live.readonly: false` (both locks).
3. **Paper first** — validate against an IBKR paper account (port 7497) before pointing it at live (port 7496).

**Never commit `config/rules.yaml` in an armed state** (`dry_run`/`readonly: false`).

## Development
- **Stack:** Python 3.12, `ib_async`, `pydantic` v2, `httpx` (Telegram), `pytest`.
- **Tests:** `pytest -q` — green on a fresh clone; live tests skip without TWS. Real IBKR response shapes can be captured into fixtures via `scripts/capture_fixtures.py` (keep committed fixtures **synthetic** — see the script's header).
- **Layout:** `src/governor/{model,config,rules,live,actions,comms,state,gate,technicals}` · `skills/pre-trade*` · `tests/` · `docs/`.
- **Setup & contributing:** [CONTRIBUTING.md](CONTRIBUTING.md) — newcomer-friendly dev setup, commands, env vars, and conventions (plus [AGENTS.md](AGENTS.md) for a 60-second orientation). **Deep-dive:** [FORCLAUDE.md](docs/FORCLAUDE.md).
- **License:** [Apache-2.0](LICENSE).

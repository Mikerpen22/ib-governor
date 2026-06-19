# AGENTS.md — ib-governor

**What is this:** a local behavioral circuit-breaker + pre-trade gate for
Interactive Brokers (IBKR) trading discipline. A governor caps an engine's max
RPM; this caps overtrading — it mechanizes per-asset risk rules (futures /
equities / portfolio) and a confirm-gated pre-trade gate so a disciplined plan
actually gets followed. Python 3.12.

This file is the cross-tool onboarding doc: clone the repo and a coding agent
should be productive in about 60 seconds.

## Quickstart

```bash
git clone https://github.com/Mikerpen22/ib-governor && cd ib-governor
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export GOVERNOR_HOME="$(pwd)"
pytest -q                                   # full suite, green WITHOUT TWS
cp .env.example .env                        # optional: add Telegram creds for alerts
python -m governor.live.daemon              # the daemon — ships dry-run/read-only
```

## Project layout

The Python package is `governor`, under `src/`:

- `src/governor/config.py` — loads & validates `config/rules.yaml` (thresholds + live wiring).
- `src/governor/model.py` — core domain types (asset class, severity, action, intent…).
- `src/governor/live/` — the always-on circuit-breaker:
  - `daemon.py` — entrypoint; holds the connection, reacts to fills, runs daily briefings.
  - `connection.py` — persistent IBKR (`ib_async`) connection management.
  - `builder.py` — assembles the live account snapshot from IBKR data.
  - `snapshot.py` — snapshot model + helpers (contract symbol / sec-type checks).
  - `daily.py` — the scheduled daily summary (`python -m governor.live.daily`).
  - `sector.py` — sector lookup/cache for equities concentration checks.
- `src/governor/gate/` — the pre-trade gate:
  - `cli.py` — `analyze` / `submit` command surface (`python -m governor.gate`).
  - `runner.py` — orchestrates analysis and the confirmed submit.
  - `analysis.py` — margin (`whatIf`), sizing, concentration, post-trade rule checks.
  - `intent.py` — the proposed-order intent model.
- `src/governor/rules/` — the deterministic rule engine:
  - `engine.py` — evaluates the registered rules against a snapshot.
  - `futures.py` · `equities.py` · `portfolio.py` — the rule functions per asset class.
  - `catalog.py` — data-only catalog of every rule; source for `docs/RULES.md`.
- `src/governor/actions/` — staged corrective actions:
  - `executor.py` — the SINGLE write chokepoint (`_guarded` wrapper around IBKR writes).
  - `tokens.py` — single-use, expiring confirm tokens.
  - `lockout.py` — lockout flag model + store.
- `src/governor/comms/` — alerting:
  - `telegram.py` · `notify.py` · `send.py` — Telegram + local notifications (`python -m governor.comms.send`).
- `src/governor/state/` — local persistence:
  - `json_store.py` — atomic JSON file store.
  - `hwm.py` — high-water-mark tracking (drawdown).
  - `trade_log.py` — per-day trade log used by overtrading/churn rules.
- `config/rules.yaml` — tunable thresholds + live wiring (ships dry-run/read-only).
- `skills/` — Claude Code skills: `pre-trade-equities`, `pre-trade-futures`, `daily-summary`.
- `tests/` — the pytest suite. `docs/` — guide docs + auto-generated API reference.

## Running tests

```bash
pytest -q
```

About 375 tests; **green on a bare clone without TWS**. Live/integration tests
skip automatically when a TWS/Gateway connection or market data is absent (they
are marked `integration`; deselect explicitly with `-m 'not integration'`).
pytest config lives in `pyproject.toml`, which sets `pythonpath = ["src"]`, so
no manual `PYTHONPATH` is needed.

## Configuration

- **`.env`** — secrets for alerts: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  (both optional; only needed to run/arm the live daemon so it can push alerts
  and accept your `CONFIRM <token>` replies). Copy from `.env.example`. The test
  suite does not need them.
- **`config/rules.yaml`** — all tunable thresholds (per-asset risk lines) plus
  the live wiring (host/port/client id, `dry_run`, `readonly`). Validated on
  load; no threshold is hardcoded in rule logic.
- **`GOVERNOR_HOME`** — the repo checkout path (default `~/ib-governor`). Set it
  to `$(pwd)` after cloning.
- **`VAULT_DIR`** — optional path to a research-notes folder; the skills read it
  to layer your own learnings onto the gate's deterministic facts.

## Safety model

This project can move real money. The design is **default-closed** at every step:

- **Two default-closed locks.** `config/rules.yaml` ships with `dry_run: true`
  (confirmed actions are logged, never executed) AND `readonly: true` (the IBKR
  API socket flag rejects writes). *Both* must be flipped to `false` to place a
  live order — so nothing reaches the broker out of the box.
- **One write chokepoint.** Exactly one module —
  `src/governor/actions/executor.py` (the `_guarded` wrapper) — calls an IBKR
  write method, and every such call passes through the dry-run gate.
- **Confirm-gating.** Every account-affecting action is staged behind a
  single-use, expiring confirm token. Nothing auto-fires; a human confirms each
  action.
- **NEVER commit `config/rules.yaml` with `dry_run` or `readonly` set to
  `false`** (armed). Check the diff before committing.
- **Paper first.** Validate against an IBKR paper account (port 7497) before
  pointing it at live (port 7496).

## Common tasks

```bash
# Pre-trade gate — analyze is READ-ONLY (places nothing); prints GO/CAUTION/BLOCK + a token
python -m governor.gate analyze buy 50 ORCL --type limit --limit 145 --json
# submit the staged order — the only write path; needs the token AND your confirmation
python -m governor.gate submit --token <TOKEN>

# Run the circuit-breaker daemon (ships dry-run/read-only — safe)
python -m governor.live.daemon

# Regenerate the human-readable rule catalog (docs/RULES.md) from the code
python -m governor.rules.catalog

# Build & preview the documentation site locally
pip install -e ".[docs]" && mkdocs serve
```

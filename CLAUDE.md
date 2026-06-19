# CLAUDE.md — ib-governor

ib-governor is a local behavioral circuit-breaker + pre-trade gate for
Interactive Brokers (IBKR) trading discipline. A governor caps an engine's max
RPM; this caps overtrading. It mechanizes per-asset risk rules (futures /
equities / portfolio) and a confirm-gated pre-trade gate. Python 3.12.

**Read [AGENTS.md](AGENTS.md) for setup** (clone, venv, install, tests) — it is
the canonical onboarding doc.

## CRITICAL safety rule

This project can move real money, so treat the safety model as non-negotiable:

- **Ships SAFE.** `config/rules.yaml` defaults to `dry_run: true` AND
  `readonly: true` — two default-closed locks. The pre-trade gate and the
  circuit-breaker daemon **never place orders** until the operator deliberately
  arms it (both locks flipped to `false`).
- **Every account action is confirm-gated** behind a single-use, expiring token.
  Nothing auto-fires.
- **NEVER commit `config/rules.yaml` with `dry_run` or `readonly` set to
  `false`** (armed). Verify before any commit.
- **Paper first.** Test against an IBKR paper account (port 7497) before live
  (port 7496).
- There is exactly **one write chokepoint** —
  `src/governor/actions/executor.py` (the `_guarded` wrapper). All IBKR write
  calls go through it. Do not add write paths anywhere else.

## Orientation

- The Python package is `governor` (under `src/`). CLI entrypoints are
  `python -m governor.*`:
  - `python -m governor.live.daemon` — the circuit-breaker daemon
  - `python -m governor.gate analyze|submit ...` — the pre-trade gate
  - `python -m governor.live.daily` — the daily summary
  - `python -m governor.comms.send` — send a Telegram message
- **Claude Code skills** live in `skills/`: `pre-trade-equities`,
  `pre-trade-futures`, `daily-summary` (each has a `SKILL.md`). They layer
  vault-grounded reasoning on top of the deterministic gate facts.
- **Tests:** `pytest -q` (run from the repo root; `pyproject.toml` sets
  `pythonpath = ["src"]`). The suite is green on a bare clone — live/integration
  tests skip when TWS/market-data is absent.

## Deep dives

- **Architecture explainer:** [docs/FORCLAUDE.md](docs/FORCLAUDE.md)
- **Operations / day-to-day:** [docs/HANDBOOK.md](docs/HANDBOOK.md)

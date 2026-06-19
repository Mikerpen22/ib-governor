# Contributing to ib-governor

Thanks for your interest! ib-governor is a small, focused Python project — a local risk "governor" for Interactive Brokers (IBKR) trading. This guide takes you from a fresh clone to running the tests and proposing a change, **even if you've never touched a trading API.** You do **not** need a brokerage account to develop or test.

> ### ⚠️ Read this first — the safety rule
> This software can place **real** brokerage orders. It ships in **dry-run + read-only** mode and never acts without an explicit human confirm. **Never commit `config/rules.yaml` with `dry_run` or `readonly` set to `false`** — that "armed" state belongs only in your private local copy. Details in [docs/SECURITY.md](docs/SECURITY.md).

## Prerequisites
- **Python 3.12** — the only hard requirement to build and test (`python3.12 --version`).
- **git** + a GitHub account (to fork and open a pull request).
- **Optional, only for running it live against your own account:** Interactive Brokers' **TWS** or **IB Gateway** desktop app with the API enabled. Start with a **paper (simulated) account** — it's free and risk-free. *None of this is needed to develop or run the tests* — the live tests skip automatically when TWS isn't present.

New to virtual environments or IBKR? That's fine — the commands below are copy-pasteable, and the test suite runs fully offline.

## Get set up
```bash
git clone https://github.com/Mikerpen22/ib-governor && cd ib-governor
make setup                      # creates .venv (python3.12) + installs with dev extras
export GOVERNOR_HOME="$(pwd)"   # the skills + launchd template reference this
make test                       # the full suite — should pass WITHOUT TWS
```
No `make`? The manual equivalent:
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Commands — the `Makefile` is the front door
| Command | What it does |
|---------|--------------|
| `make setup` | Create `.venv` (Python 3.12) and install the package with dev dependencies |
| `make test` | Run the full test suite (`pytest -q`) — green without TWS |
| `make daemon` | Run the circuit-breaker daemon (ships dry-run/read-only) |
| `make gate` | Print example pre-trade-gate usage |
| `make docs` | Install docs extras and serve the docs site at <http://localhost:8000> |
| `make clean` | Remove the venv, caches, and build artifacts |

Each wraps a `python -m governor.*` entry point you can also call directly:
| Module | Purpose |
|--------|---------|
| `python -m governor.gate analyze …` | Read-only pre-trade analysis → GO/CAUTION/BLOCK + a single-use token |
| `python -m governor.gate submit --token …` | The **only** write path — places a staged order after confirmation |
| `python -m governor.live.daemon` | The always-on circuit-breaker daemon |
| `python -m governor.live.daily` | End-of-day data for the daily summary |
| `python -m governor.rules.catalog` | Regenerate `docs/RULES.md` from the rule registry |

## Environment variables
Two secrets are read from a local `.env` (copy `.env.example` → `.env`). Both are **optional** — they only enable live alerts. The `.env.example` file has step-by-step setup instructions.

| Variable | Purpose | Format / example |
|----------|---------|------------------|
| `TELEGRAM_BOT_TOKEN` | Token for your Telegram alert bot (from @BotFather) | `123456789:AAH9x…Qw` |
| `TELEGRAM_CHAT_ID` | The chat the daemon messages (you) | numeric, e.g. `12345678` |

Two **shell** variables (set in your shell profile, *not* `.env`) configure paths:
| Variable | Purpose | Default |
|----------|---------|---------|
| `GOVERNOR_HOME` | Absolute path to your checkout (skills + the launchd template use it) | `~/ib-governor` |
| `VAULT_DIR` | *Optional* research-notes folder the skills can read for extra context | unset → vault features off |

IBKR connection settings (host / port / client_id) are **configuration, not secrets** — they live in `config/rules.yaml`, never in `.env`.

## Running tests
```bash
make test                         # or: pytest -q
pytest --cov=governor -q          # with a coverage report
```
- The suite is **green on a bare clone** — tests needing a live IBKR connection or a market-data subscription **skip** themselves when those aren't available.
- Fixtures in `tests/fixtures/` are **fully synthetic** (fictional tickers, round numbers). If you ever regenerate them with `scripts/capture_fixtures.py` against your own account, keep the committed copies synthetic — see the script's header for why.
- New behavior should come with a test; we follow a test-first approach where practical.

## Project layout
```
src/governor/
  live/      persistent IBKR connection, snapshot builder, the daemon, daily summary
  gate/      the pre-trade gate (intent → analysis → confirm-gated submit)
  rules/     the deterministic rule engine + catalog (futures / equities / portfolio)
  actions/   the SINGLE guarded write chokepoint + confirm tokens + lockout store
  comms/     Telegram + desktop notifications
  state/     small JSON-backed stores (lockout, high-water-mark, trade log)
  config.py  typed config (pydantic);  model.py  the StateSnapshot domain type
skills/      Claude Code skills — the optional LLM-analyst layer
config/      rules.yaml — your tunable thresholds (ships SAFE)
docs/        guides + an auto-generated API reference
tests/       unit + live (skipped without TWS) + a replay acceptance test
```

## Code conventions
- **Deterministic core, no LLM in the hot path.** The rule engine and gate are pure, fast, auditable Python. LLM reasoning lives only in the on-demand skills.
- **Pure / IO split.** Keep transforms pure (easy to unit-test); isolate IBKR calls at the edges. Type-hint public functions; validate config with pydantic.
- **One write chokepoint.** Every IBKR order write goes through `governor/actions/executor.py` (the `_guarded` wrapper). **Do not add a write path anywhere else** — it is the heart of the safety model.
- Prefer small, focused modules over large ones, and match the style of the file you're editing.

## Submitting a change
1. Fork, then branch from `main`: `git checkout -b fix/short-description`.
2. Make the change and add/adjust tests. If you touch the rules, run `python -m governor.rules.catalog` to refresh `docs/RULES.md` (a test fails if it drifts).
3. `make test` must pass.
4. Commit using **conventional-commit** style: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
5. Double-check `config/rules.yaml` is **not** armed and that no secrets or personal data are staged.
6. Open a pull request describing the change and how you tested it.

**Found a security issue?** Please **don't** open a public issue — report it privately via GitHub's "Report a vulnerability" (see [docs/SECURITY.md](docs/SECURITY.md)).

## Where to look next
- **[AGENTS.md](AGENTS.md)** — a 60-second orientation (also tuned for coding agents).
- **[docs/HANDBOOK.md](docs/HANDBOOK.md)** — operating it day to day: diagrams, arming, troubleshooting.
- **[docs/FORCLAUDE.md](docs/FORCLAUDE.md)** — the architecture & lessons deep-dive.
- **[docs/RULES.md](docs/RULES.md)** — every rule, its config key, and what trips it.

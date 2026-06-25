# ib-governor — common developer tasks.
#
# A local behavioral circuit-breaker + pre-trade gate for IBKR trading
# discipline. These targets call .venv/bin/python directly, so they work
# whether or not you have the venv activated.
#
# Quick start:
#   make setup     # create .venv (python3.12) + install with dev extras
#   make test      # run the full test suite (green WITHOUT TWS)
#   make daemon    # run the circuit-breaker daemon (ships dry-run/read-only)

PYTHON ?= python3.12
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: help setup test daemon restart-daemon gate docs clean

help:
	@echo "ib-governor — available targets:"
	@echo "  make setup    Create $(VENV) (with $(PYTHON)) and install -e \".[dev]\""
	@echo "  make test     Run the test suite (pytest -q) — green without TWS"
	@echo "  make daemon   Run the circuit-breaker daemon (ships dry-run/read-only)"
	@echo "  make restart-daemon  Pull main (if clean) + bounce the launchd daemon"
	@echo "  make gate     Show example pre-trade gate (read-only analyze) usage"
	@echo "  make docs     Install docs extras and serve the MkDocs site locally"
	@echo "  make clean    Remove $(VENV), caches, and build artifacts"

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"
	@echo "Done. Activate with: source $(BIN)/activate"

test:
	$(BIN)/python -m pytest -q

daemon:
	$(BIN)/python -m governor.live.daemon

# Bounce the always-on launchd daemon after pulling new code or editing config.
# Pulls main (when the tree is clean), restarts com.ib-governor.daemon, and
# prints the SAFE/ARMED mode it came back in. Pass --no-pull / --logs via the
# script directly. Restart only — never arms the brake.
restart-daemon:
	./scripts/restart-daemon.sh

gate:
	@echo "Pre-trade gate — analyze is READ-ONLY (places nothing); prints GO/CAUTION/BLOCK + a single-use token:"
	@echo ""
	@echo "  $(BIN)/python -m governor.gate analyze buy 50 ORCL --type limit --limit 145 --json"
	@echo ""
	@echo "Then submit the staged order (the only write path; needs the token AND confirmation):"
	@echo ""
	@echo "  $(BIN)/python -m governor.gate submit --token <TOKEN>"

docs:
	$(BIN)/python -m pip install -e ".[docs]"
	$(BIN)/mkdocs serve

clean:
	rm -rf $(VENV)
	rm -rf .pytest_cache .mypy_cache
	rm -rf build dist *.egg-info src/*.egg-info
	rm -rf site htmlcov .coverage .coverage.*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

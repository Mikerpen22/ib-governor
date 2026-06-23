"""Read-only technical setup CLI: `python -m governor.technicals <SYMBOL>`.

Renders the SAME Stage-2/VCP (equity) or trend/vol/location/momentum (futures)
read the pre-trade gate shows — but as a pure QUESTION. It qualifies the
contract, fetches daily bars, and assesses; it PLACES nothing and (unlike
`gate analyze`) STAGES nothing — no confirm token is written. This lets the
natural-language ask lane answer "how does NVDA look?" without a side effect.

assess_symbol(ib, ...) is pure given a duck-typed ib (qualifyContracts +
reqHistoricalData), so it's unit-testable with no real connection. main() wires
a read-only connection on the technicals client id.
"""
from __future__ import annotations

import argparse
import json

from ..config import RulesConfig, load_config, load_env_file
from ..gate.intent import Action, OrderIntent, OrderType, SecType
from ..gate.runner import qualify
from ..live.history import fetch_daily_bars
from .assess import assess_setup, setup_to_dict


def assess_symbol(ib, symbol: str, sec_type: str, config: RulesConfig) -> dict:
    """Qualify → fetch daily bars → assess the candidate setup. Read-only; the
    action is fixed to BUY (the lens the setup reasons are written for). Stages
    nothing. Returns a JSON-serializable dict {symbol, sec_type, setup}."""
    st = SecType.FUT if str(sec_type).lower() == "fut" else SecType.STK
    intent = OrderIntent(action=Action.BUY, symbol=symbol.upper(), quantity=1,
                         sec_type=st, order_type=OrderType.MARKET)
    contract = qualify(ib, intent)
    bars = fetch_daily_bars(ib, contract, config.setup.history_duration)
    setup = assess_setup(st, Action.BUY, bars, config.setup)
    return {"symbol": symbol.upper(), "sec_type": st.value, "setup": setup_to_dict(setup)}


def render_text(result: dict) -> str:
    """A compact human summary (the --json form is what the ask agent consumes)."""
    setup = result.get("setup", {})
    head = f"{result.get('symbol', '?')} ({result.get('sec_type', '?')})"
    if not setup.get("available"):
        return f"{head}: insufficient history for a setup read."
    reasons = setup.get("caution_reasons") or []
    body = "\n".join(f"  - {r}" for r in reasons) if reasons else "  - no setup cautions"
    verdict = "POOR setup" if setup.get("poor") else "setup OK"
    return f"{head} — {verdict}:\n{body}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m governor.technicals",
        description="Read-only technical setup read for a symbol (places/stages nothing).",
    )
    parser.add_argument("symbol", help="ticker or futures root, e.g. NVDA or MNQ")
    parser.add_argument("--sec-type", choices=["stk", "fut"], default="stk")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="emit the setup as JSON (for the ask agent)")
    parser.add_argument("--config", default="config/rules.yaml")
    args = parser.parse_args(argv)

    from ..live.connection import BrakeConnection

    load_env_file()
    config = load_config(args.config)
    # Own client id (distinct from daemon=4 / gate=5 / daily=6) so we can read
    # while the always-on daemon holds 4. Read-only by construction.
    tech_live = config.live.model_copy(update={"client_id": config.live.technicals_client_id})
    conn = BrakeConnection(tech_live)
    conn.connect()
    try:
        result = assess_symbol(conn.ib, args.symbol, args.sec_type, config)
    finally:
        conn.disconnect()
    print(json.dumps(result, indent=2) if args.as_json else render_text(result))


if __name__ == "__main__":  # pragma: no cover
    main()

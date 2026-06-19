"""Capture REAL IBKR API response shapes into tests/fixtures/ for replayable
contract tests. READ-ONLY — places NO orders. Run with TWS/Gateway up:

    PYTHONPATH=src .venv/bin/python scripts/capture_fixtures.py            # raw values
    PYTHONPATH=src .venv/bin/python scripts/capture_fixtures.py --scale 10 # scaled dollars

Writes account_values.json, portfolio.json, fills.json — exactly the fields the
snapshot builder consumes. Account identifiers are scrubbed to 'TEST_ACCT' (both the
`account` field AND the `value` of account-id tags like AccountCode/AccountOrGroup).

WARNING: --scale only OBSCURES dollar magnitudes; it does NOT anonymize. Scaling
preserves every RATIO (NAV, position weights, leverage, sector concentration) and is
trivially reversible, so a *captured* fixture still exposes your real book. Captures are
for LOCAL, PRIVATE drift-checking ONLY — do NOT commit them to a public repo. The fixtures
shipped in this repo are fully SYNTHETIC (fictional tickers / round numbers); keep them
that way rather than committing a real capture.

Once tests/fixtures/*.json are present, tests/live/test_real_fixtures.py stops skipping
and runs the builder against these shapes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from governor.config import load_config
from governor.live.connection import BrakeConnection

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
SCRUB = "TEST_ACCT"


def _g(obj, attr, default=None):
    return getattr(obj, attr, default)


def _contract(c) -> dict:
    return {
        "secType": _g(c, "secType"),
        "symbol": _g(c, "symbol"),
        "localSymbol": _g(c, "localSymbol"),
        "multiplier": _g(c, "multiplier"),
        "conId": _g(c, "conId"),
        "exchange": _g(c, "exchange"),
        "currency": _g(c, "currency"),
    }


# Tags whose `value` field IS the account identifier (not a dollar amount), so the
# account number must be scrubbed from `value` too — not only the `account` field.
_ACCT_ID_TAGS = {"AccountCode", "AccountOrGroup", "AccountId", "AccountName"}


def _account_value(av) -> dict:
    tag = _g(av, "tag")
    value = SCRUB if tag in _ACCT_ID_TAGS else _g(av, "value")
    return {"tag": tag, "value": value,
            "currency": _g(av, "currency"), "account": SCRUB}


def _portfolio_item(it) -> dict:
    return {
        "contract": _contract(_g(it, "contract")),
        "position": _g(it, "position"),
        "marketPrice": _g(it, "marketPrice"),
        "marketValue": _g(it, "marketValue"),
        "averageCost": _g(it, "averageCost"),
        "unrealizedPNL": _g(it, "unrealizedPNL"),
        "account": SCRUB,
    }


def _fill(f) -> dict:
    ex, cr = _g(f, "execution"), _g(f, "commissionReport")
    return {
        "contract": _contract(_g(f, "contract")),
        "execution": {
            "orderId": _g(ex, "orderId"),
            "side": _g(ex, "side"),
            "shares": _g(ex, "shares"),
            "cumQty": _g(ex, "cumQty"),
            "time": str(_g(ex, "time")),
            "acctNumber": SCRUB,
        },
        "commissionReport": {
            "realizedPNL": _g(cr, "realizedPNL"),
            "commission": _g(cr, "commission"),
        },
    }


def _scaled(v, factor: float):
    """Scale a numeric value by factor, PRESERVING type (str stays str, number stays
    number). Non-numeric values (currency codes, symbols) pass through unchanged."""
    if factor == 1.0 or isinstance(v, bool):  # bool is an int subclass — never scale flags
        return v
    if isinstance(v, (int, float)):
        return v * factor
    if isinstance(v, str):
        try:
            return str(float(v) * factor)
        except ValueError:
            return v
    return v


def _anonymize(account_rows, portfolio_rows, fill_rows, factor: float):
    """Scale every dollar field by factor (ratios preserved; share counts untouched)."""
    if factor == 1.0:
        return account_rows, portfolio_rows, fill_rows
    acct = [{**r, "value": _scaled(r["value"], factor)} for r in account_rows]
    money = ("marketPrice", "marketValue", "averageCost", "unrealizedPNL")
    port = [{**r, **{k: _scaled(r.get(k), factor) for k in money}} for r in portfolio_rows]
    fills = [
        {**r, "commissionReport": {
            "realizedPNL": _scaled(r["commissionReport"].get("realizedPNL"), factor),
            "commission": _scaled(r["commissionReport"].get("commission"), factor),
        }}
        for r in fill_rows
    ]
    return acct, port, fills


def _write(name: str, rows: list[dict]) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(json.dumps(rows, indent=2, default=str))
    print(f"  wrote tests/fixtures/{name}.json ({len(rows)} rows)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture IBKR fixtures (read-only).")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="Scale dollar fields by this factor to anonymize real amounts "
                             "(e.g. 10 = values 10x bigger; ratios preserved). Default 1.0 (raw).")
    args = parser.parse_args()

    cfg = load_config("config/rules.yaml")
    conn = BrakeConnection(cfg.live)
    try:
        conn.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not connect to TWS ({exc}). Is TWS up with the API enabled "
              f"on {cfg.live.host}:{cfg.live.port}?")
        return 2
    ib = conn.ib
    note = f"; dollar fields x{args.scale:g} (anonymized)" if args.scale != 1.0 else ""
    print(f"capturing (read-only; account ids -> TEST_ACCT{note}):")
    acct = [_account_value(av) for av in ib.accountValues()]
    port = [_portfolio_item(it) for it in ib.portfolio()]
    fills = [_fill(f) for f in ib.fills()]
    acct, port, fills = _anonymize(acct, port, fills, args.scale)
    _write("account_values", acct)
    _write("portfolio", port)
    _write("fills", fills)
    conn.disconnect()
    print("done. Review tests/fixtures/*.json, then commit — "
          "tests/live/test_real_fixtures.py will run against them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

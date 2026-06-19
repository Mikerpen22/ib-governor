"""CLI entry-point for the pre-trade gate.

Subcommands
-----------
    analyze <action> <quantity> <symbol> [options]
        Runs the full gate analysis read-only, stages the order, and prints
        either JSON (--json) or a human-readable summary.  Exit code:
            0  — GO or CAUTION (or BLOCK with --override)
            2  — BLOCK (without --override)

    submit --token TOKEN [--json]
        Consumes a previously staged token and places the order via
        ActionExecutor.  Exits nonzero if the token is invalid/expired.

Monkeypatchable seams (module-level callables, replaced by tests):
    _make_connection(config) -> conn
    build_current_snapshot(ib, config) -> StateSnapshot
    analyze_intent(ib, intent, current, config, lockout_store, *, now, sector)
    submit_intent(ib, executor, intent) -> bool
    load_config(path) -> RulesConfig
    _staged_path(config) -> Path
    _get_now() -> dt.datetime  (returns current ET-aware time; patchable for tests)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from governor.actions.executor import ActionExecutor
from governor.actions.lockout import LockoutStore
from governor.config import RulesConfig, load_config
from governor.gate.analysis import Verdict
from governor.gate.intent import Action, OrderIntent, OrderType, SecType
from governor.gate.runner import analyze_intent, submit_intent
from governor.gate.staged import StagedOrderStore
from governor.live.builder import build_live_snapshot
from governor.live.connection import BrakeConnection
from governor.live.sector import SectorResolver
from governor.state.hwm import HwmStore
from governor.state.trade_log import WeeklyTradeLog

ET = ZoneInfo("America/New_York")

# Repo-relative path resolution — keeps all config paths stable regardless of
# the working directory from which the CLI is invoked (e.g. when called from a
# skill that shells out from a different cwd).
_REPO_ROOT = Path(__file__).resolve().parents[3]  # src/governor/gate/cli.py → repo root

_CONFIG_PATH = _REPO_ROOT / "config" / "rules.yaml"
_LOCKOUT_PATH = _REPO_ROOT / "config" / "lockout.json"
_HWM_PATH = _REPO_ROOT / "config" / "hwm.json"
_TRADE_LOG_PATH = _REPO_ROOT / "config" / "trade_log.json"
_SECTOR_CACHE_PATH = _REPO_ROOT / "config" / "sector_cache.json"


# ---------------------------------------------------------------------------
# Seam functions — monkeypatched in tests
# ---------------------------------------------------------------------------


def _get_now() -> dt.datetime:
    """Return current time as an ET-aware datetime. Monkeypatched in tests."""
    return dt.datetime.now(tz=ET)


def _gate_connection_config(config: RulesConfig):
    """Return a LiveConfig copy that uses the gate's own client_id (so the gate
    doesn't collide with a running daemon on client_id 4)."""
    return config.live.model_copy(update={"client_id": config.live.gate_client_id})


def _make_connection(config: RulesConfig) -> BrakeConnection:
    """Create and return a BrakeConnection (does not call .connect())."""
    return BrakeConnection(_gate_connection_config(config))


def _staged_path(config: RulesConfig) -> Path:
    """Return the Path for the staged-orders file."""
    return _REPO_ROOT / "config" / "staged_orders.json"


def build_current_snapshot(ib, config: RulesConfig):
    """Build a read-only StateSnapshot from live IBKR data.

    Delegates to ``build_live_snapshot`` with ``mutate_hwm=False`` so the HWM peak
    file is never written during a gate analysis.  This function is kept as a
    module-level seam so tests can monkeypatch it.
    """
    return build_live_snapshot(
        ib,
        config,
        sector_resolver=SectorResolver(ib, cache_path=_SECTOR_CACHE_PATH),
        trade_log=WeeklyTradeLog(_TRADE_LOG_PATH),
        hwm=HwmStore(_HWM_PATH),
        now=dt.datetime.now(tz=ET),
        mutate_hwm=False,
    )


def _resolve_sector(ib, symbol: str) -> str | None:
    """Resolve the sector for a symbol. Module-level seam so tests can monkeypatch it."""
    return SectorResolver(ib, cache_path=_SECTOR_CACHE_PATH).resolve(symbol)


# ---------------------------------------------------------------------------
# Arg → enum mapping helpers
# ---------------------------------------------------------------------------

_ACTION_MAP: dict[str, Action] = {
    "buy": Action.BUY,
    "sell": Action.SELL,
}

_SEC_TYPE_MAP: dict[str, SecType] = {
    "stk": SecType.STK,
    "fut": SecType.FUT,
}

_ORDER_TYPE_MAP: dict[str, OrderType] = {
    "market": OrderType.MARKET,
    "limit": OrderType.LIMIT,
    "stop": OrderType.STOP,
    "stop-limit": OrderType.STOP_LIMIT,
}


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m governor.gate",
        description="Pre-trade gate: analyze or submit an order.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- analyze subcommand ---
    ana = sub.add_parser("analyze", help="Analyze a proposed order against all gate rules.")
    ana.add_argument("action", choices=["buy", "sell"], help="Trade direction")
    ana.add_argument("quantity", type=float, help="Number of shares / contracts")
    ana.add_argument("symbol", type=str, help="Ticker symbol (e.g. ORCL, MNQ)")
    ana.add_argument(
        "--sec-type",
        dest="sec_type",
        default="stk",
        choices=["stk", "fut"],
        help="Security type: stk (default) or fut",
    )
    ana.add_argument(
        "--type",
        dest="order_type",
        default="market",
        choices=["market", "limit", "stop", "stop-limit"],
        help="Order type (default: market)",
    )
    ana.add_argument("--limit", dest="limit_price", type=float, default=None,
                     help="Limit price (required for limit / stop-limit orders)")
    ana.add_argument("--stop", dest="stop_price", type=float, default=None,
                     help="Stop price (required for stop / stop-limit orders)")
    ana.add_argument("--stop-loss", dest="stop_loss", type=float, default=None,
                     help="Protective stop-loss price (attaches a bracket child order)")
    ana.add_argument("--take-profit", dest="take_profit", type=float, default=None,
                     help="Protective take-profit price (attaches a bracket child order)")
    ana.add_argument("--json", dest="json_output", action="store_true",
                     help="Print JSON output instead of human-readable")
    ana.add_argument("--override", action="store_true",
                     help="Exit 0 even on BLOCK verdict (allows override submit)")

    # --- submit subcommand ---
    sub_cmd = sub.add_parser("submit", help="Submit a previously staged order by token.")
    sub_cmd.add_argument("--token", required=True, help="Staging token from analyze")
    sub_cmd.add_argument("--json", dest="json_output", action="store_true",
                         help="Print JSON output")

    return parser


# ---------------------------------------------------------------------------
# analyze handler
# ---------------------------------------------------------------------------


def _handle_analyze(args, config: RulesConfig) -> int:
    """Run gate analysis. Returns exit code (0 = GO/CAUTION, 2 = BLOCK)."""
    # 1. Build and validate the OrderIntent (fail-fast before any I/O)
    action = _ACTION_MAP[args.action]
    sec_type = _SEC_TYPE_MAP[args.sec_type]
    order_type = _ORDER_TYPE_MAP[args.order_type]

    try:
        intent = OrderIntent(
            action=action,
            symbol=args.symbol.upper(),
            quantity=args.quantity,
            sec_type=sec_type,
            order_type=order_type,
            limit_price=args.limit_price,
            stop_price=args.stop_price,
            stop_loss=getattr(args, "stop_loss", None),
            take_profit=getattr(args, "take_profit", None),
        )
    except (ValidationError, ValueError) as exc:
        print(f"ERROR: invalid order — {exc}", file=sys.stderr)
        return 1

    # 2. Config already loaded by caller.

    # 3. Connect (read-only)
    conn = _make_connection(config)
    conn.connect()
    ib = conn.ib

    try:
        # 4. Build current snapshot (read-only)
        current = build_current_snapshot(ib, config)

        # 5. Resolve sector for the target symbol (best-effort)
        sector: str | None = None
        if sec_type is SecType.STK:
            try:
                sector = _resolve_sector(ib, intent.symbol)
            except Exception:  # noqa: BLE001 — sector is advisory
                sector = None

        # 6. Gate analysis
        lockout_store = LockoutStore(_LOCKOUT_PATH)
        now = _get_now()
        verdict, preview = analyze_intent(
            ib, intent, current, config, lockout_store,
            now=now, sector=sector,
        )

        # 7. Stage the order (even on BLOCK — override submit requires the token)
        store = StagedOrderStore(
            _staged_path(config),
            ttl_seconds=config.live.confirm_ttl_seconds,
        )
        token = store.stage(intent.model_dump(), now)

    finally:
        conn.disconnect()

    # 8. Output
    if args.json_output:
        print(json.dumps({**preview, "token": token}))
    else:
        _print_analyze_summary(intent, verdict, preview, token)

    # 9. Exit code
    if verdict.level is Verdict.BLOCK and not args.override:
        return 2
    return 0


def _print_analyze_summary(
    intent: OrderIntent,
    verdict,
    preview: dict,
    token: str,
) -> None:
    """Print a concise, human-readable gate analysis summary."""
    label = f"{intent.action.value} {int(intent.quantity)} {intent.symbol} ({intent.sec_type.value})"
    print(f"\n=== GATE ANALYSIS: {label} ===")
    print(f"  Order type  : {intent.order_type.value}"
          + (f" @ {intent.limit_price}" if intent.limit_price else "")
          + (f" stop {intent.stop_price}" if intent.stop_price else ""))
    print(f"  Notional    : ${preview.get('order_notional', 0.0):,.0f}  "
          f"({preview.get('pct_nav', 0.0):.1%} of NAV)")
    print(f"  Init margin : ${preview.get('init_margin', 0.0):,.0f}")

    trips = preview.get("trips", [])
    if trips:
        print(f"  Rule trips  : {len(trips)}")
        for t in trips:
            print(f"    [{t['severity'].upper()}] {t['rule_id']}: {t['message']}")
    else:
        print("  Rule trips  : none")

    print(f"  Lockout     : {'YES' if preview.get('lockout_active') else 'no'}")
    print()

    level = verdict.level.value
    reasons = verdict.reasons
    print(f"  VERDICT: {level}")
    if reasons:
        for r in reasons:
            print(f"    - {r}")

    print()
    print(f"  Token  : {token}")
    print(f"  Submit : python -m governor.gate submit --token {token}")
    print()


# ---------------------------------------------------------------------------
# submit handler
# ---------------------------------------------------------------------------


def _handle_submit(args, config: RulesConfig) -> int:
    """Consume a staged token and place the order. Returns exit code."""
    now = _get_now()

    store = StagedOrderStore(
        _staged_path(config),
        ttl_seconds=config.live.confirm_ttl_seconds,
    )
    intent_dict = store.consume(args.token, now)
    if intent_dict is None:
        print(
            f"ERROR: token {args.token!r} is invalid, already used, or expired — "
            "re-run 'analyze' to get a fresh token.",
            file=sys.stderr,
        )
        return 1

    try:
        intent = OrderIntent(**intent_dict)
    except (ValidationError, ValueError) as exc:
        print(f"ERROR: staged intent is invalid — {exc}", file=sys.stderr)
        return 1

    # Warn on armed-but-readonly misconfiguration: dry_run=False with readonly=True
    # means TWS will silently reject the order even though the CLI would report success.
    if not config.live.dry_run and config.live.readonly:
        print(
            "⚠️  live.dry_run is False but live.readonly is True — the IB API "
            "connection is read-only, so TWS will REJECT this order. Set "
            "readonly: false in config/rules.yaml to actually place orders.",
            file=sys.stderr,
        )

    # Connect
    conn = _make_connection(config)
    conn.connect()
    ib = conn.ib

    try:
        lockout_store = LockoutStore(_LOCKOUT_PATH)
        executor = ActionExecutor(
            ib,
            dry_run=config.live.dry_run,
            lockout_store=lockout_store,
        )
        placed = submit_intent(ib, executor, intent)
    finally:
        conn.disconnect()

    # Report
    mode_label = "DRY-RUN — not sent" if config.live.dry_run else "ARMED — order sent"
    label = f"{intent.action.value} {int(intent.quantity)} {intent.symbol}"

    if args.json_output:
        print(json.dumps({
            "action": intent.action.value,
            "symbol": intent.symbol,
            "quantity": intent.quantity,
            "placed": placed,
            "dry_run": config.live.dry_run,
        }))
    else:
        status = "PLACED" if placed else mode_label
        print(f"submit: {label} — {status}")

    return 0


# ---------------------------------------------------------------------------
# main entry-point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Parse args, dispatch to handler, and sys.exit with the returned code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(_CONFIG_PATH)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: could not load config — {exc}", file=sys.stderr)
        sys.exit(1)

    if args.command == "analyze":
        code = _handle_analyze(args, config)
    else:
        code = _handle_submit(args, config)

    if code != 0:
        sys.exit(code)

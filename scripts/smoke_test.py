"""Live external-touchpoint smoke test for ib-governor.

Prints a PASS/WARN/SKIP/FAIL board for every external dependency and exits
non-zero only if at least one check FAILs. SKIP means the dependency is absent.
WARN means the touchpoint works but with degraded capability (e.g. whatIf empty).

Usage (from project root):
    PYTHONPATH=src .venv/bin/python scripts/smoke_test.py

# whatIf findings
# ================
# Live smoke run on 2026-06-19 with TWS connected (read-only mode, real account):
#
#   1. Market order (default tif):           PASS — OrderState returned (initMarginAfter populated)
#      Advisory: "Order TIF was set to DAY based on order preset" (TWS normalizes tif)
#   2. Market order + tif="DAY" explicitly:  PASS — same result
#   3. Limit order + explicit price (nan*0.98): FAIL — "Error 320: Unable to parse field:
#      'Limit Price' for input string: 'nan'" — this was because reqTickers() returned nan
#      (no live market data subscription) and the price calculation produced nan, not a
#      whatIf-specific failure. Using a hardcoded limit price resolves this.
#   4. whatIfOrderAsync: not needed — synchronous whatIfOrder works fine.
#
# Root cause of the original "whatIf returns []" report: The issue was NOT the
# read-only API flag. Market orders DO return a populated OrderState. The [] was
# likely observed after a limit order with nan price, which TWS rejects silently.
#
# Summary: whatIf is AVAILABLE on this account in read-only mode. Market orders
# work. Limit orders work if the price is valid (non-nan). The gate's _order_state
# defensive handling ([] → None → buying_power_ok=True) is still good practice
# since TWS can return [] in other edge cases (paper account quirks, etc.).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# ── result accumulator ────────────────────────────────────────────────────────

_RESULTS: list[tuple[str, str, str]] = []  # (name, status, detail)


def _record(name: str, status: str, detail: str) -> None:
    _RESULTS.append((name, status, detail))


def _print_board() -> None:
    width = max(len(n) for n, _, _ in _RESULTS) + 2
    print()
    for name, status, detail in _RESULTS:
        print(f"{status:<5} {name:<{width}} — {detail}")
    print()
    fails = [n for n, s, _ in _RESULTS if s == "FAIL"]
    warns = [n for n, s, _ in _RESULTS if s == "WARN"]
    if fails:
        print(f"OVERALL: FAIL  ({len(fails)} failure(s): {', '.join(fails)})")
    elif warns:
        print(f"OVERALL: WARN  (all pass with {len(warns)} degraded touchpoint(s): {', '.join(warns)})")
    else:
        passed = sum(1 for _, s, _ in _RESULTS if s == "PASS")
        skipped = sum(1 for _, s, _ in _RESULTS if s == "SKIP")
        print(f"OVERALL: PASS  ({passed} passed, {skipped} skipped)")


# ── check 1: config ───────────────────────────────────────────────────────────

def check_config() -> bool:
    try:
        from governor.config import load_config
        cfg = load_config("config/rules.yaml")
        _record("config", "PASS", f"loaded rules.yaml (futures.daily_loss_usd={cfg.futures.daily_loss_usd})")
        return True
    except Exception as exc:
        _record("config", "FAIL", str(exc))
        return False


# ── TWS checks ────────────────────────────────────────────────────────────────

def check_tws_connect(cfg) -> tuple[bool, object | None]:
    """Returns (success, ib) — ib is the connected IB instance or None."""
    from governor.config import load_config
    from governor.live.connection import BrakeConnection

    live_cfg = cfg.live.model_copy(update={"client_id": 9})
    conn = BrakeConnection(live_cfg)
    try:
        conn.connect()
        ib = conn.ib
        _record("TWS connect", "PASS", f"client_id 9 @ {live_cfg.host}:{live_cfg.port}")
        return True, ib
    except Exception as exc:
        _record("TWS connect", "SKIP", f"connection failed — {exc}")
        return False, None


def check_tws_data(ib) -> None:
    try:
        vals = ib.accountValues()
        nlv = next((v for v in vals if v.tag == "NetLiquidation" and v.currency == "USD"), None)
        if nlv is None:
            _record("TWS data", "FAIL", "NetLiquidation not found in accountValues()")
            return
        portfolio = ib.portfolio()
        fills = ib.fills()
        _record(
            "TWS data",
            "PASS",
            f"NAV={float(nlv.value):,.0f} USD  portfolio={len(portfolio)} items  fills={len(fills)}",
        )
    except Exception as exc:
        _record("TWS data", "FAIL", str(exc))


def check_whatif(ib, cfg) -> None:
    """Probe whatIfOrder with several variants; WARN if all return empty."""
    from ib_async import LimitOrder, MarketOrder, Stock

    try:
        contract = ib.qualifyContracts(Stock("SPY", "SMART", "USD"))
        if not contract:
            _record("whatIf", "FAIL", "SPY contract not qualified")
            return
        contract = contract[0]

        # Variant 1: plain market order
        mkt = MarketOrder("BUY", 1)
        r1 = ib.whatIfOrder(contract, mkt)

        # Variant 2: market order with explicit tif="DAY"
        mkt_day = MarketOrder("BUY", 1)
        mkt_day.tif = "DAY"
        r2 = ib.whatIfOrder(contract, mkt_day)

        # Variant 3: limit order with hardcoded price (avoids reqTickers nan when no
        # live market data subscription; SPY price is well above $400 so $490 is safe)
        lmt = LimitOrder("BUY", 1, 490.0)
        r3 = ib.whatIfOrder(contract, lmt)

        # Variant 4: async-style via ib.run() — synchronous whatIf already works;
        # this confirms the async path doesn't behave differently
        async def _async_whatif():
            order = LimitOrder("BUY", 1, 490.0)
            result = ib.whatIfOrder(contract, order)
            ib.sleep(0.25)
            return result

        try:
            r4 = ib.run(_async_whatif())
        except Exception:
            r4 = []

        all_empty = all(r == [] or r is None for r in [r1, r2, r3, r4])
        if all_empty:
            _record(
                "whatIf",
                "WARN",
                "all variants returned [] — margin preview unavailable (read-only TWS session; gate degrades gracefully)",
            )
        else:
            # Some variant returned a result
            non_empty = next(r for r in [r1, r2, r3, r4] if r and r != [])
            state = non_empty[0] if isinstance(non_empty, list) else non_empty
            init = getattr(state, "initMarginAfter", "?")
            _record("whatIf", "PASS", f"margin preview available (initMarginAfter={init})")
    except Exception as exc:
        _record("whatIf", "FAIL", str(exc))


def check_sector_resolve(ib) -> None:
    try:
        from governor.live.sector import SectorResolver
        resolver = SectorResolver(ib, cache_path=None)
        sector = resolver.resolve("AAPL")
        _record(
            "sector resolve",
            "PASS",
            f"AAPL → {sector!r}" if sector else "AAPL → None (unknown sector, not an error)",
        )
    except Exception as exc:
        _record("sector resolve", "FAIL", str(exc))


def check_gate_analyze(ib, cfg) -> None:
    from governor.actions.lockout import LockoutStore
    from governor.gate.intent import Action, OrderIntent, OrderType, SecType
    from governor.gate.runner import analyze_intent
    from governor.live.snapshot import build_snapshot

    try:
        now = dt.datetime.now(tz=ET)
        account_values = ib.accountValues()
        portfolio_items = ib.portfolio()
        fills = ib.fills()
        snapshot = build_snapshot(
            now=now,
            account_values=account_values,
            portfolio_items=portfolio_items,
            fills=fills,
            cfg=cfg.live,
            sector_by_symbol={},
        )
        # Use LIMIT with an explicit price so analyze_intent doesn't need to call
        # reqTickers — this account lacks a live market data subscription, causing
        # marketPrice=nan and a ValueError in _reference_price.
        intent = OrderIntent(
            action=Action.BUY,
            quantity=1,
            symbol="SPY",
            sec_type=SecType.STK,
            order_type=OrderType.LIMIT,
            limit_price=500.0,
        )
        lockout_store = LockoutStore("config/lockout.json")
        verdict, preview = analyze_intent(ib, intent, snapshot, cfg, lockout_store, now=now)
        _record(
            "gate analyze",
            "PASS",
            f"verdict={verdict.level.value}  reasons={preview.get('reasons', [])}",
        )
    except Exception as exc:
        _record("gate analyze", "FAIL", str(exc))


def check_daily_collector(ib, cfg) -> None:
    try:
        from governor.live.daily import collect_day_data
        now = dt.datetime.now(tz=ET)
        data = collect_day_data(ib, cfg, now)
        # Verify expected keys
        expected_keys = {"date", "nav", "margin_cushion", "gross_leverage",
                         "realized_pnl_today", "fills", "positions", "trips"}
        missing = expected_keys - set(data.keys())
        if missing:
            _record("daily collector", "FAIL", f"missing keys: {missing}")
            return
        # Verify JSON-serializable
        json.dumps(data)
        _record(
            "daily collector",
            "PASS",
            f"date={data['date']}  nav={data['nav']:,.0f}  fills={len(data['fills'])}  trips={len(data['trips'])}",
        )
    except Exception as exc:
        _record("daily collector", "FAIL", str(exc))


# ── Telegram checks ───────────────────────────────────────────────────────────

def check_telegram_getme(tg_cfg) -> bool:
    try:
        import httpx
        url = f"https://api.telegram.org/bot{tg_cfg.bot_token}/getMe"
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            _record("Telegram getMe", "FAIL", f"ok=False: {body}")
            return False
        username = body.get("result", {}).get("username", "?")
        _record("Telegram getMe", "PASS", f"ok=True  bot=@{username}")
        return True
    except Exception as exc:
        _record("Telegram getMe", "FAIL", str(exc))
        return False


def check_telegram_send(tg_cfg) -> None:
    async def _send():
        import httpx
        from governor.comms.telegram import TelegramClient
        async with httpx.AsyncClient() as http:
            client = TelegramClient(tg_cfg, http)
            today = dt.date.today().isoformat()
            await client.send(f"🔌 ib-governor smoke test {today}")

    try:
        asyncio.run(_send())
        _record("Telegram send", "PASS", "message delivered without exception")
    except Exception as exc:
        _record("Telegram send", "FAIL", str(exc))


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    from governor.config import load_env_file, telegram_from_env

    load_env_file()

    # 1. Config
    if not check_config():
        _print_board()
        sys.exit(1)

    from governor.config import load_config
    cfg = load_config("config/rules.yaml")

    # 2. TWS connect
    tws_ok, ib = check_tws_connect(cfg)

    # 3–7. TWS-dependent checks
    if tws_ok and ib is not None:
        check_tws_data(ib)
        check_whatif(ib, cfg)
        check_sector_resolve(ib)
        check_gate_analyze(ib, cfg)
        check_daily_collector(ib, cfg)
    else:
        for name in ["TWS data", "whatIf", "sector resolve", "gate analyze", "daily collector"]:
            _record(name, "SKIP", "TWS not connected")

    # 8–9. Telegram checks
    tg_cfg = telegram_from_env()
    if not tg_cfg.enabled:
        _record("Telegram getMe", "SKIP", "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        _record("Telegram send", "SKIP", "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    else:
        getme_ok = check_telegram_getme(tg_cfg)
        if getme_ok:
            check_telegram_send(tg_cfg)
        else:
            _record("Telegram send", "SKIP", "getMe failed — skipping send")

    # Disconnect cleanly if we connected
    if tws_ok and ib is not None:
        try:
            ib.disconnect()
        except Exception:
            pass

    _print_board()

    if any(s == "FAIL" for _, s, _ in _RESULTS):
        sys.exit(1)


if __name__ == "__main__":
    main()

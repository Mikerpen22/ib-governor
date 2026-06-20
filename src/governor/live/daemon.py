"""The live daemon: persistent connection + event-driven recompute + briefing loop.

Pure helpers (next_briefing_dt, is_stale) are unit-tested.
The wiring (events, run loop, Telegram tasks) is integration territory.
Plan 3 replaces handle()'s logging with Telegram alerts + confirm-gated actions.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import secrets
from zoneinfo import ZoneInfo

import httpx

from ..actions.executor import ActionExecutor
from ..actions.lockout import LockoutStore
from ..actions.tokens import ConfirmTokenGate
from ..comms.notify import notify as macos_notify
from ..comms.telegram import TelegramClient
from ..config import RulesConfig, load_config, load_env_file, telegram_from_env
from ..model import ActionType, Severity, StateSnapshot, Trip
from ..rules.engine import evaluate
from ..state.hwm import HwmStore
from ..state.json_store import StateFileError
from ..state.trade_log import WeeklyTradeLog
from .builder import build_live_snapshot
from .connection import BrakeConnection
from .sector import SectorResolver
from .snapshot import contract_symbol, is_sec_type

ET = ZoneInfo("America/New_York")
log = logging.getLogger("governor.daemon")

# IB status codes that are informational, not errors (data farm connect/disconnect).
_BENIGN_IB_CODES = {2104, 2106, 2107, 2108, 2119, 2158}


def next_briefing_dt(now: dt.datetime, briefing_times_et: list[str]) -> dt.datetime:
    """Soonest future ET datetime among the configured HH:MM times (rolls to tomorrow)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    now = now.astimezone(ET)
    candidates: list[dt.datetime] = []
    for hhmm in briefing_times_et:
        h, m = (int(x) for x in hhmm.split(":"))
        today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        candidates.append(today if today > now else today + dt.timedelta(days=1))
    return min(candidates)


_ACTIONABLE = {ActionType.LOCKOUT_FUTURES_48H, ActionType.PLATFORM_OFF_TODAY, ActionType.TRIM_FUTURES}


def is_stale(last, now, max_age: float) -> bool:
    """True if the last successful snapshot is older than max_age. None last = not stale
    (the daemon just hasn't built yet)."""
    if last is None:
        return False
    return (now - last).total_seconds() > max_age


class BrakeDaemon:
    def __init__(self, config: RulesConfig) -> None:
        self.config = config
        self.conn = BrakeConnection(config.live)
        # Plan 3: dry_run=False is now valid (armed mode). Plan-2 NotImplementedError guard removed.
        self._telegram_cfg = telegram_from_env()
        self._http = httpx.AsyncClient(timeout=self.config.live.confirm_ttl_seconds + 35)
        self.telegram = TelegramClient(self._telegram_cfg, self._http)
        self.lockout_store = LockoutStore("config/lockout.json")
        self.executor = ActionExecutor(self.ib, dry_run=self.config.live.dry_run,
                                       lockout_store=self.lockout_store)
        self.tokens = ConfirmTokenGate(self.config.live.confirm_ttl_seconds,
                                       token_factory=lambda: secrets.token_hex(4).upper())
        self.sector = SectorResolver(self.ib)
        self.hwm = HwmStore("config/hwm.json")
        self.trade_log = WeeklyTradeLog("config/trade_log.json")
        self._last_built = None
        self._last_executed: dict[str, dt.datetime] = {}  # action.value -> last successful execute (cooldown)
        self._active_soft_keys: set[str] = set()  # rule_ids of standing WARN/INFO trips already announced (edge-triggered alerts)
        self._tg_offset = 0

    @property
    def ib(self):
        return self.conn.ib

    def _now(self) -> dt.datetime:
        return dt.datetime.now(tz=ET)

    def build(self) -> StateSnapshot:
        return build_live_snapshot(
            self.ib,
            self.config,
            sector_resolver=self.sector,
            trade_log=self.trade_log,
            hwm=self.hwm,
            now=self._now(),
            mutate_hwm=True,
        )

    def evaluate_and_handle(self, reason: str) -> list[Trip]:
        snap = self.build()
        trips = evaluate(snap, self.config)
        self.handle(trips, snap, reason)
        return trips

    def alert(self, text: str) -> None:
        log.warning(text)
        macos_notify("Brake", text)
        if self._telegram_cfg.enabled:
            asyncio.ensure_future(self.telegram.send(text))

    def handle(self, trips, snap, reason) -> None:
        self._last_built = self._now()
        # lockout-violation witness: only fills can violate a lockout, so only
        # read the file on fills (saves a disk round-trip on every briefing/reconnect).
        if reason == "fill":
            try:
                lk = self.lockout_store.active(self._now())
            except StateFileError as exc:
                # Present-but-unreadable lockout state: fail CLOSED — assume locked + scream.
                self.alert(f"\U0001f6d1 BRAKE BLIND: lockout state unreadable ({exc}). "
                           f"Assume you ARE locked out — inspect/clear config/lockout.json.")
            else:
                if lk:
                    self.alert(f"⚠️ LOCKOUT VIOLATION: you traded futures while a {lk.kind} "
                               f"lockout is active (until {lk.until:%H:%M}, reason: {lk.reason}).")
        # Edge-triggered soft alerts: a standing WARN/INFO (e.g. sector concentration)
        # is announced ONCE when it appears and stays quiet while it persists — so the
        # 3x/day briefings don't re-spam it. HARD trips always alert (they stage actions
        # and matter every time). A soft trip that later clears gets a one-line "cleared".
        current_rule_ids = {t.rule_id for t in trips}
        new_soft_keys = {t.rule_id for t in trips if t.severity is not Severity.HARD}
        for t in trips:
            if t.severity is not Severity.HARD and t.rule_id in self._active_soft_keys:
                continue  # standing WARN/INFO already announced — don't repeat it
            self.alert(f"\U0001f6d1 {t.rule_id} [{t.severity.value}] — {t.message}")
            if t.action not in _ACTIONABLE:
                continue
            last = self._last_executed.get(t.action.value)
            if last is not None and \
                    (self._now() - last).total_seconds() < self.config.live.action_cooldown_seconds:
                # post-execute cooldown: don't re-stage the same action while it settles
                self.alert(f"(cooldown) {t.action.value} executed recently — not re-staging "
                           f"for ~{int(self.config.live.action_cooldown_seconds)}s.")
                continue
            token = self.tokens.issue(payload=t, now=self._now(), dedup_key=t.action.value)
            mode = "DRY-RUN" if self.config.live.dry_run else "ARMED"
            self.alert(f"Staged action ({mode}): {t.action.value}. Reply `CONFIRM {token}` "
                       f"within {int(self.config.live.confirm_ttl_seconds)}s to proceed.")

        cleared = self._active_soft_keys - current_rule_ids
        if cleared:
            self.alert(f"✅ cleared: {', '.join(sorted(cleared))}")
        self._active_soft_keys = new_soft_keys

        if not trips:
            log.info("[%s] OK nav=%.0f fut_pnl=%.0f trades=%d", reason, snap.nav,
                     snap.futures_realized_pnl_today, snap.futures_trade_count_today)

    def on_confirm(self, reply_text: str) -> None:
        pending = self.tokens.verify(reply_text, self._now())
        if pending is None:
            return
        trip = pending.payload
        self.alert(f"✅ confirmed: {trip.action.value} — executing.")
        self._execute(trip.action)

    def _execute(self, action: ActionType) -> None:
        now = self._now()
        try:
            if action == ActionType.LOCKOUT_FUTURES_48H:
                self.executor.lockout("futures_48h", now + dt.timedelta(hours=48),
                                      "house-money / loss rule", now)
            elif action == ActionType.PLATFORM_OFF_TODAY:
                eod = now.replace(hour=23, minute=59, second=0, microsecond=0)
                self.executor.lockout("platform_off_today", eod, "daily loss / overtrading stop", now)
            elif action == ActionType.TRIM_FUTURES:
                self.executor.trim_futures(target_contracts=self.config.futures.max_overnight_contracts)
            self._last_executed[action.value] = now   # arm the cooldown only on success
        except Exception as exc:  # noqa: BLE001 — a failed action must NOT look like success
            # e.g. a lockout cancelled orders but couldn't persist its flag: say so loudly
            # instead of letting it vanish into the telegram-loop's generic catch.
            self.alert(f"\U0001f6d1 ACTION FAILED: {action.value} did not complete ({exc}). "
                       f"The brake may NOT be armed — verify manually.")

    # --- event handlers ---
    def _on_commission(self, trade, fill, report) -> None:
        # realizedPNL is populated HERE (not on execDetailsEvent).
        # Record STK fills to the rolling weekly trade log (de-duped by orderId).
        if is_sec_type(fill, "STK"):
            sym = contract_symbol(fill.contract)
            if sym:
                self.trade_log.record(sym, fill.execution.orderId, self._now())
        # Re-evaluate immediately on ANY fill (futures or equity) so all
        # rules see the updated state without waiting for the next briefing.
        self.evaluate_and_handle("fill")

    def _on_error(self, reqId, code, errorString, contract) -> None:
        if code not in _BENIGN_IB_CODES:
            log.error("IB error %s: %s", code, errorString)

    def _on_disconnect(self) -> None:
        log.error("BRAKE BLIND: disconnected from TWS — reconnecting")
        asyncio.ensure_future(self._reconnect())

    async def _reconnect(self) -> None:
        for delay in (5, 10, 20, 40, 60):
            await asyncio.sleep(delay)
            try:
                await self.conn.connect_async()
                log.info("reconnected to TWS")
                self.evaluate_and_handle("reconnect")
                return
            except Exception as exc:  # noqa: BLE001 - want to retry on any failure
                log.error("reconnect attempt failed (waited %ss): %s", delay, exc)
        log.critical("BRAKE BLIND: reconnect gave up — manual intervention required")

    async def _briefing_loop(self) -> None:
        while True:
            now = self._now()
            nxt = next_briefing_dt(now, self.config.live.briefing_times_et)
            await asyncio.sleep(max(0.0, (nxt - now).total_seconds()))
            try:
                self.evaluate_and_handle("briefing")
            except Exception as exc:  # noqa: BLE001
                log.error("briefing failed: %s", exc)

    async def _telegram_loop(self) -> None:
        if not self._telegram_cfg.enabled:
            log.warning("Telegram not configured (set TELEGRAM_BOT_TOKEN/CHAT_ID) — "
                        "alerts go to logs + macOS only; confirmations unavailable.")
            return
        while True:
            try:
                texts, self._tg_offset = await self.telegram.poll(self._tg_offset)
                for text in texts:
                    self.on_confirm(text)
            except Exception as exc:  # noqa: BLE001
                log.error("telegram poll error: %s", exc)
                await asyncio.sleep(5)

    def _refresh_if_stale(self) -> None:
        """Staleness-watchdog tick. A quiet market (no fills, between briefings) ages the
        snapshot — that's NORMAL, not a fault — so refresh SILENTLY to keep state warm and
        prove data still flows. Only scream BRAKE BLIND if the refresh itself FAILS (a
        genuine stall: socket up but data dead). A dropped link is handled by the
        disconnect path, not here."""
        if not self.ib.isConnected():
            return  # disconnect path already screams BRAKE BLIND
        if not is_stale(self._last_built, self._now(), self.config.live.staleness_seconds * 3):
            return
        log.info("staleness watchdog: snapshot aged — refreshing quietly")
        try:
            self.evaluate_and_handle("staleness")
        except Exception as exc:  # noqa: BLE001 — a failed refresh IS the blind condition
            self.alert(f"\U0001f6d1 BRAKE BLIND: snapshot refresh failed ({exc}) — "
                       f"data may be stale; check TWS.")

    async def _staleness_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.live.staleness_seconds)
            self._refresh_if_stale()

    def run(self) -> None:
        self.conn.connect()
        self.ib.commissionReportEvent += self._on_commission
        self.ib.errorEvent += self._on_error
        self.ib.disconnectedEvent += self._on_disconnect
        mode = "ARMED — confirmed actions WILL execute" if not self.config.live.dry_run \
               else "DRY-RUN — confirmed actions are logged only"
        log.warning("brake daemon up: %s", mode)
        self.evaluate_and_handle("startup")
        asyncio.ensure_future(self._briefing_loop())
        asyncio.ensure_future(self._telegram_loop())
        asyncio.ensure_future(self._staleness_loop())
        self.ib.run()  # loop.run_forever()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_env_file()  # populate Telegram creds from .env before telegram_from_env() reads them
    config = load_config("config/rules.yaml")
    BrakeDaemon(config).run()


if __name__ == "__main__":
    main()

"""The live daemon: persistent connection + event-driven recompute + briefing loop.

Pure helpers (next_briefing_dt, is_stale) are unit-tested.
The wiring (events, run loop, Telegram tasks) is integration territory.
Plan 3 replaces handle()'s logging with Telegram alerts + confirm-gated actions.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import secrets
import sys
from zoneinfo import ZoneInfo

import httpx

from ..actions.executor import ActionExecutor
from ..actions.lockout import LockoutStore
from ..actions.tokens import ConfirmTokenGate
from ..comms.agent_runner import run_agent, run_ask_agent
from ..comms.ask import Intent, classify_message, quick_answer
from ..comms.format import b, code, esc, header, i, joinsections, section, strip_tags
from ..comms.notify import notify as macos_notify
from ..comms.proc import run_capture
from ..comms.telegram import TelegramClient
from ..config import RulesConfig, load_config, load_env_file, telegram_from_env
from ..gate.staged import StagedOrderStore, resolve_staged_path
from ..model import ActionType, StateSnapshot, Trip
from ..rules.engine import evaluate
from ..state.hwm import HwmStore
from ..state.json_store import StateFileError
from ..state.trade_log import WeeklyTradeLog
from .builder import build_live_snapshot
from .connection import BrakeConnection
from .daily import _account_id, collect_account_view
from .sector import SectorResolver
from .snapshot import contract_symbol, is_sec_type

ET = ZoneInfo("America/New_York")
log = logging.getLogger("governor.daemon")


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    h, m = (int(x) for x in hhmm.split(":"))
    return h, m


def is_expected_restart(now: dt.datetime, restart_et: str, window_min: float) -> bool:
    """True if `now` is within +/- window_min of the daily Gateway/IBC restart
    time (ET) on the prior, current, or next day — so a 23:59 restart's window
    correctly straddles midnight. A disconnect here is routine: stay quiet."""
    now = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    h, m = _parse_hhmm(restart_et)
    window = window_min * 60.0
    for day_off in (-1, 0, 1):
        restart = (now + dt.timedelta(days=day_off)).replace(
            hour=h, minute=m, second=0, microsecond=0)
        if abs((now - restart).total_seconds()) <= window:
            return True
    return False


def is_weekly_relogin_window(now: dt.datetime, reset_et: str, probe_et: str) -> bool:
    """True if `now` is Sunday (ET) between the IBKR weekly token reset and the
    morning probe — being logged out here is expected (market closed), so the
    reconnect loop stays quiet and the Sunday probe issues the actionable nudge."""
    now = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    if now.weekday() != 6:
        return False
    rh, rm = _parse_hhmm(reset_et)
    ph, pm = _parse_hhmm(probe_et)
    reset = now.replace(hour=rh, minute=rm, second=0, microsecond=0)
    probe = now.replace(hour=ph, minute=pm, second=0, microsecond=0)
    return reset <= now < probe


def next_weekly_probe_dt(now: dt.datetime, probe_et: str) -> dt.datetime:
    """Soonest future Sunday at probe_et (ET)."""
    now = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    h, m = _parse_hhmm(probe_et)
    days_ahead = (6 - now.weekday()) % 7
    candidate = (now + dt.timedelta(days=days_ahead)).replace(
        hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=7)
    return candidate


def should_alert_blind(elapsed_seconds: float, expected: bool,
                       alert_after_seconds: float, restart_window_min: float) -> bool:
    """Whether a still-down link warrants the (edge-triggered) BRAKE-BLIND alert.
    During an expected restart/weekly window, tolerate the full window before
    crying wolf; otherwise alert after the short grace."""
    threshold = restart_window_min * 60.0 if expected else alert_after_seconds
    return elapsed_seconds >= threshold

# IB status codes that are informational, not errors (data farm connect/disconnect).
_BENIGN_IB_CODES = {2104, 2106, 2107, 2108, 2119, 2158}

# A staged-order / action confirm token: hex from secrets.token_hex(...).upper()
# (8 chars for actions, 16 for orders). Hex-only + min-8 avoids matching ordinary
# English words after the CONFIRM keyword (8-hex chatter like "DEADBEEF" is
# tolerated — it just routes to submit and gets an "expired/invalid" reply).
_TOKEN_RE = re.compile(r"^[0-9A-F]{8,}$")
_TOKEN_STRIP = "`*_'\".,!?:;()[]"
_COMMANDS = ("/start", "/help", "help")

# Slash shortcuts (the Telegram command menu) → the canonical question each maps
# to, answered by the deterministic read-only fast-path.
_QUICK_COMMANDS = {
    "/leverage": "leverage",
    "/pnl": "how am I doing",
    "/positions": "positions",
    "/book": "book",
    "/today": "what did I trade today",
    "/cushion": "margin cushion",
}

# Registered with Telegram (setMyCommands) so they appear as tap shortcuts.
_BOT_COMMANDS = [
    {"command": "leverage", "description": "Current gross leverage"},
    {"command": "pnl", "description": "Today's P&L"},
    {"command": "positions", "description": "Open positions"},
    {"command": "today", "description": "Today's trades"},
    {"command": "cushion", "description": "Margin cushion"},
    {"command": "help", "description": "What I can do"},
]


def _normalize_token(word: str) -> str | None:
    """Strip markdown/punctuation, upper-case, and return the word iff it is a
    token. One predicate shared by the typed-CONFIRM and button-callback paths so
    they can't drift on what wrapping a user is allowed to put around a token."""
    candidate = word.strip(_TOKEN_STRIP).upper()
    return candidate if _TOKEN_RE.match(candidate) else None


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


def _confirm_token(text: str) -> str | None:
    """Extract an order confirm token from a message containing a CONFIRM keyword
    followed by a token-shaped word.

    Tolerant of how a phone user actually sends it — case, surrounding markdown
    backticks/punctuation, and leading words ("Reply CONFIRM x", "please confirm
    x") — mirroring the all-words tolerance of ConfirmTokenGate.verify. A natural-
    language message that merely mentions "confirm" without a token-shaped word
    (e.g. "confirm that ORCL is a buy") returns None and is routed to the agent.
    """
    words = text.split()
    for i, word in enumerate(words):
        if word.strip(_TOKEN_STRIP).upper() == "CONFIRM":
            for nxt in words[i + 1:]:              # first token-shaped word after CONFIRM
                tok = _normalize_token(nxt)
                if tok is not None:
                    return tok
            return None
    return None


async def _gate_submit(token: str, timeout: float) -> tuple[int, str, str]:
    """Run `python -m governor.gate submit --token <token> --json` (never
    --override) as a subprocess. This is the order-write path: the gate enforces
    both locks, the _guarded chokepoint, and the BLOCK refusal. Module-level seam
    for tests."""
    argv = [sys.executable, "-m", "governor.gate", "submit", "--token", token, "--json"]
    return await run_capture(argv, timeout)


# Submit can be slow on a cold TWS; give it its own (longer) budget rather than
# reusing the agent's timeout, and treat a timeout as "uncertain" (the order may
# already be live) rather than "failed".
_SUBMIT_TIMEOUT_SECONDS = 60.0

def help_message() -> str:
    """Onboarding text in the Telegram-HTML house style (bold lead, italic
    examples, <code> for the literal confirm grammar)."""
    return joinsections(
        "👋 " + b("I'm your trading brake.") + " Text me an order in plain English "
        "and I'll check it against your rules before anything is placed.",
        section("Place an order:", [
            "• " + i("buy 10 oracle"),
            "• " + i("grab 2 micro nasdaq at 21000, stop 20900"),
            "• " + i("sell 50 SNAP at market"),
        ]),
        "I'll reply with a risk read and a confirm token. Nothing is placed until "
        "you tap " + b("✅ Place order") + " or reply " + code("CONFIRM <token>") +
        ". Orders expire after ~5 minutes for safety.",
        section("Ask me anything (read-only):", [
            "• " + i("what's my leverage?") + " · " + i("how am I doing?"),
            "• " + i("show my positions") + " · " + i("what did I trade today?"),
            "• " + i("how does NVDA look?") + " · " + i("any news on oracle?"),
        ]),
        "Shortcuts: " + code("/leverage") + " " + code("/pnl") + " " +
        code("/positions") + " " + code("/today") + " " + code("/cushion") + ".",
    )


def _is_fast_message(text: str) -> bool:
    """Cheap messages (a command or a confirm) handled inline, ahead of slow agent
    runs — so a CONFIRM is never queued behind a ~70s analysis and can't expire
    while it waits."""
    if text.strip().lower() in _COMMANDS:
        return True
    return _confirm_token(text) is not None


_REASON_REPLIES = {
    "BLOCKED": f"🛑 {b('BLOCKED')} — I did NOT place this order. Nothing happened to your account.",
    "EXPIRED": ("⏳ That confirmation expired or was already used (orders time out after "
                "~5 min). Text me the order again to get a fresh one."),
    "READONLY": f"⚠️ Can't place — the connection is in {b('read-only / safe mode')}. Nothing happened.",
    "INVALID_INTENT": "⚠️ Couldn't place that order — the staged order was invalid. Please re-send it.",
}


def _friendly_submit_reply(rc: int, out: str, err: str) -> str:
    """Map gate-submit `--json` output to a normie-readable line that answers
    'did money move?' first. The gate emits a structured object on stdout for both
    success and failure (a `reason` code on error), so we switch on fields, not
    fragile stderr prose."""
    try:
        d = json.loads(out.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001 — no structured output: never assert success
        return "⚠️ Order status is uncertain — please check TWS before re-sending."
    reason = d.get("reason")
    if reason:
        return _REASON_REPLIES.get(
            reason, "⚠️ Couldn't place that order. Nothing happened to your account.")
    label = f"{d.get('action', '?')} {int(d.get('quantity', 0) or 0)} {d.get('symbol', '?')}"
    if d.get("placed"):
        return f"✅ {b('ORDER PLACED')} — {code(label)} is live at IBKR now."
    if d.get("dry_run"):
        return (f"🧪 {b('PRACTICE MODE')} — {code(label)} was NOT placed; your account is "
                f"untouched (the bot is in safe / dry-run mode).")
    return f"⚠️ {code(label)} — submitted, but status is uncertain. Check TWS."


def rule_alert(trip) -> str:
    """A circuit-breaker trip line in the house style: bold rule id, the severity
    tag left bare so `[hard]` stays a literal substring, escaped message."""
    return f"🛑 {b(trip.rule_id)} [{trip.severity.value}] — {esc(trip.message)}"


def staged_action_message(action_value: str, mode: str, ttl_seconds: float, token: str) -> str:
    """The confirm-gated staged-action announcement. `token` rides in <code> so
    it stays copy-pasteable (and a literal substring) regardless of formatting."""
    return joinsections(
        f"{header('🟡', f'Staged action ({mode})')}: {code(action_value)}.",
        f"Tap below, or reply {code('CONFIRM ' + token)}, within {int(ttl_seconds)}s to proceed.",
    )


def _confirm_keyboard(token: str, kind: str = "order") -> dict:
    """Inline keyboard so the user taps instead of typing a token. The token rides
    in callback_data, so the safety model is unchanged — taps are just a nicer
    transport. `kind` namespaces the tap so it routes to the right path:
      - "order"  → confirm:<token>  → gate submit (the order write chokepoint)
      - "action" → action:<token>   → in-memory circuit-breaker execute
    Cancel is shared (cancel:<token>)."""
    if kind == "action":
        go_text, go_data = "✅ Confirm", f"action:{token}"
    else:
        go_text, go_data = "✅ Place order", f"confirm:{token}"
    return {"inline_keyboard": [[
        {"text": go_text, "callback_data": go_data},
        {"text": "✖️ Cancel", "callback_data": f"cancel:{token}"},
    ]]}


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
        self._announced_keys: set[str] = set()  # rule_ids of standing trips already announced (edge-triggered alerts; any severity)
        self._reconnecting = False        # guard: at most one reconnect loop at a time
        self._blind_alerted = False       # edge-trigger: BRAKE BLIND announced once per blind episode
        self._tg_offset = 0
        # Serialize order placement/cancel so two near-simultaneous taps/types of
        # the same token can't both consume it and double-submit (the staged-file
        # read-modify-write isn't atomic across the spawned gate subprocesses).
        self._place_lock = asyncio.Lock()
        # Bound + track spawned agent runs so a burst of orders can't fork
        # unbounded `claude` subprocesses (starving the brake loop) or be GC'd
        # mid-flight (asyncio keeps only a weak ref to bare-future tasks).
        self._agent_sema = asyncio.Semaphore(2)
        self._agent_tasks: set[asyncio.Task] = set()

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

    def _account_view(self) -> dict | None:
        """The connection-cheap account view (no market backdrop) for the
        quick-answer fast-path. None when we can't read it (disconnected / error)
        so the caller falls through to the slower ask agent — never raises."""
        if not self.ib.isConnected():
            return None
        try:
            return collect_account_view(self.ib, self.config, self._now())
        except Exception as exc:  # noqa: BLE001 — a read failure must not crash the loop
            log.error("account view failed: %s", exc)
            return None

    def _subscribe_pnl(self) -> None:
        """Open the account-level reqPnL subscription once so the quick-answer
        /pnl reads are warm (sub-second). Best-effort — a failure must not block
        startup; fetch_account_pnl degrades to 'n/a' if the stream never arrives."""
        try:
            self.ib.reqPnL(_account_id(self.ib, self.config))
        except Exception as exc:  # noqa: BLE001
            log.warning("reqPnL subscribe failed (P&L will read n/a): %s", exc)

    def alert(self, text: str, *, action_token: str | None = None) -> None:
        """Loud brake notification: telegram (HTML) + macOS banner + WARNING log.
        `text` is HTML; the log + banner get the tags stripped so they stay
        readable, telegram gets the formatted version (with a plain-text fallback
        inside send() if the markup is ever rejected). When *action_token* is
        given, attach ✅/✖️ buttons so a staged circuit-breaker action can be
        confirmed with a tap (routes to action:<token>)."""
        plain = strip_tags(text)
        log.warning(plain)
        macos_notify("Brake", plain)
        if self._telegram_cfg.enabled:
            markup = _confirm_keyboard(action_token, kind="action") if action_token else None
            asyncio.ensure_future(self.telegram.send(text, parse_mode="HTML", reply_markup=markup))

    def handle(self, trips, snap, reason) -> None:
        self._last_built = self._now()
        # lockout-violation witness: only fills can violate a lockout, so only
        # read the file on fills (saves a disk round-trip on every briefing/reconnect).
        if reason == "fill":
            try:
                lk = self.lockout_store.active(self._now())
            except StateFileError as exc:
                # Present-but-unreadable lockout state: fail CLOSED — assume locked + scream.
                self.alert(f"\U0001f6d1 {b('BRAKE BLIND')}: lockout state unreadable ({esc(exc)}). "
                           f"Assume you ARE locked out — inspect/clear config/lockout.json.")
            else:
                if lk:
                    self.alert(f"⚠️ {b('LOCKOUT VIOLATION')}: you traded futures while a {esc(lk.kind)} "
                               f"lockout is active (until {lk.until:%H:%M}, reason: {esc(lk.reason)}).")
        # Edge-triggered alerts: a standing trip (ANY severity) is announced ONCE
        # when it appears and stays quiet while it persists — so the staleness
        # refresh (~every 270s) and the 3x/day briefings don't re-spam a condition
        # that holds all day (e.g. the futures losing-streak limit / platform-off).
        # A trip that clears gets a one-line "cleared"; if it later re-trips, the
        # edge re-arms and it alerts (so a NEW episode is never missed).
        current_rule_ids = {t.rule_id for t in trips}
        for t in trips:
            if t.rule_id in self._announced_keys:
                continue  # standing trip already announced — don't repeat it
            self.alert(rule_alert(t))
            if t.action not in _ACTIONABLE:
                continue
            last = self._last_executed.get(t.action.value)
            if last is not None and \
                    (self._now() - last).total_seconds() < self.config.live.action_cooldown_seconds:
                # post-execute cooldown: don't re-stage the same action while it settles
                self.alert(f"(cooldown) {code(t.action.value)} executed recently — not re-staging "
                           f"for ~{int(self.config.live.action_cooldown_seconds)}s.")
                continue
            token = self.tokens.issue(payload=t, now=self._now(), dedup_key=t.action.value)
            mode = "DRY-RUN" if self.config.live.dry_run else "ARMED"
            self.alert(staged_action_message(t.action.value, mode,
                                             self.config.live.confirm_ttl_seconds, token),
                       action_token=token)

        cleared = self._announced_keys - current_rule_ids
        if cleared:
            self.alert(f"✅ cleared: {esc(', '.join(sorted(cleared)))}")
        self._announced_keys = current_rule_ids

        if not trips:
            log.info("[%s] OK nav=%.0f fut_pnl=%.0f trades=%d", reason, snap.nav,
                     snap.futures_realized_pnl_today, snap.futures_trade_count_today)

    def on_confirm(self, reply_text: str) -> bool:
        """Confirm a staged circuit-breaker ACTION (in-memory token) from a typed
        message.

        Returns True iff a live token matched and the action was dispatched —
        so the message router knows whether to keep looking (order / NL).
        """
        pending = self.tokens.verify(reply_text, self._now())
        if pending is None:
            return False
        trip = pending.payload
        self.alert(f"✅ confirmed: {code(trip.action.value)} — executing.")
        self._execute(trip.action)
        return True

    def _confirm_action(self, token: str) -> str:
        """Confirm a staged circuit-breaker ACTION from a button tap. Same token
        gate + executor as on_confirm, but returns an outcome string so the tapped
        card can be edited in place. A failed execute alerts loudly via _execute."""
        pending = self.tokens.verify(token, self._now())
        if pending is None:
            return "⏳ That action expired or was already used — re-trigger it if you still want it."
        trip = pending.payload
        self._execute(trip.action)
        return f"✅ {b('Confirmed')} — {code(trip.action.value)} executed."

    async def _reply(self, text: str, token: str | None = None) -> None:
        """Send a CHAT reply — telegram only. Distinct from alert(), which is for
        loud brake notifications (telegram + macOS + WARNING log). `text` is
        Telegram-HTML. When *token* is given, attach ✅/✖️ inline buttons so the
        user can tap to confirm/cancel."""
        markup = _confirm_keyboard(token) if token else None
        if self._telegram_cfg.enabled:
            await self.telegram.send(text, parse_mode="HTML", reply_markup=markup)
        else:
            log.info("telegram reply (telegram not configured): %s", strip_tags(text))

    async def handle_telegram_text(self, text: str) -> None:
        """Route one inbound Telegram message through the branches:

        0. `/start` / `/help` -> onboarding;  a `/leverage`-style shortcut -> quick answer,
        1. a staged circuit-breaker ACTION confirm (in-memory token),
        2. an ORDER confirm (`CONFIRM <token>` -> gate submit chokepoint),
        3a. a read-only QUESTION -> the deterministic fast-path, else the ask agent,
        3b. a natural-language ORDER -> the headless `claude -p` order agent,
            each preceded by an instant ack so the user isn't staring at silence.

        Order placement never happens here — it flows through `gate submit`,
        which enforces both locks, the _guarded chokepoint, and the BLOCK
        refusal. The order agent only proposes + stages; the ask lane is read-only.
        """
        stripped = text.strip().lower()
        if stripped in _COMMANDS:
            await self._reply(help_message())
            return
        slug = stripped.split()[0] if stripped else ""
        if slug in _QUICK_COMMANDS:                      # /leverage, /pnl, … menu shortcuts
            await self._reply(self._quick_or_unavailable(_QUICK_COMMANDS[slug]))
            return
        if self.on_confirm(text):
            return
        token = _confirm_token(text)
        if token is not None:
            await self._reply(await self._submit_staged_order(token))
            return

        intent = classify_message(text)
        if intent is Intent.ASK:
            # Read-only ask lane. A recognized factual question is answered instantly
            # off the daemon's already-live connection — no subprocess, no new socket
            # — and works even when the order agent is disabled. Misses fall to the
            # (slower) read-only ask agent.
            view = self._account_view()
            if view is not None:
                answer = quick_answer(text, view)
                if answer is not None:
                    await self._reply(answer)
                    return
            if not self.config.telegram_agent.enabled:
                log.info("telegram_agent disabled — no ask-agent fallback")
                return
            async with self._agent_sema:
                await self._reply("🔎 Looking into that…")
                reply = await run_ask_agent(text, self.config.telegram_agent)
            await self._reply(reply)                     # ask agent replies HTML; read-only, no token
            return

        if not self.config.telegram_agent.enabled:
            log.info("telegram_agent disabled — ignoring non-confirm message")
            return
        async with self._agent_sema:    # bound concurrent agent subprocesses
            await self._reply("🔍 Got it — analyzing your order now (about a minute)…")
            reply = await run_agent(text, self.config.telegram_agent)
        # The order agent returns plain prose — escape it so a literal &/</> in the
        # text can't break HTML parsing. If it proposed an order (reply carries a
        # CONFIRM token) attach ✅/✖️ buttons; a BLOCK / clarifying reply has none.
        await self._reply(esc(reply), token=_confirm_token(reply))

    def _quick_or_unavailable(self, question: str) -> str:
        """Answer a slash-shortcut question from the live account view, or a
        friendly fallback when we can't read the account."""
        view = self._account_view()
        answer = quick_answer(question, view) if view is not None else None
        return answer or "⚠️ Can't read your account right now — try again in a moment."

    async def handle_callback(self, data: str, callback_id: str | None = None,
                              message_id: int | None = None) -> None:
        """Handle an inline-button tap. `data` is namespaced by what it confirms:
          - 'confirm:<token>' → place a staged ORDER via the gate submit chokepoint
          - 'action:<token>'  → execute a staged circuit-breaker ACTION (in-memory)
          - 'cancel:<token>'  → discard a staged order
        Same safety path as a typed CONFIRM — the token gates the action. When
        *message_id* is known, the tapped card is edited in place into its outcome
        (and the keyboard dropped) instead of spawning a second message."""
        action, _, raw = data.partition(":")
        token = _normalize_token(raw)
        if token is None:                       # malformed/forged tap → tell the user, do nothing
            if callback_id is not None and self._telegram_cfg.enabled:
                await self.telegram.answer_callback(callback_id, text="Expired or invalid")
            return
        if callback_id is not None and self._telegram_cfg.enabled:
            await self.telegram.answer_callback(callback_id)  # clear the tap spinner
        if action == "confirm":
            outcome = await self._submit_staged_order(token)
        elif action == "action":
            outcome = self._confirm_action(token)
        elif action == "cancel":
            outcome = await self._cancel_staged_order(token)
        else:                                   # unknown namespace → ignore quietly
            return
        await self._render_outcome(outcome, message_id)

    async def _render_outcome(self, text: str, message_id: int | None) -> None:
        """Show the result of a tap. Edit the tapped card in place when we know its
        id (one clean card, keyboard gone); otherwise fall back to a fresh reply."""
        if message_id is not None and self._telegram_cfg.enabled and \
                await self.telegram.edit_message(message_id, text, parse_mode="HTML"):
            return
        await self._reply(text)

    async def _cancel_staged_order(self, token: str) -> str:
        """Consume + discard a staged order so it can't be confirmed later. Shares
        the placement lock so a cancel can't race a concurrent confirm of the same
        token (both consuming the non-atomic staged file)."""
        store = StagedOrderStore(resolve_staged_path(),
                                 ttl_seconds=self.config.live.confirm_ttl_seconds)
        async with self._place_lock:
            try:
                record = store.consume(token, self._now())
            except Exception as exc:  # noqa: BLE001 — never crash the loop on a bad cancel
                log.error("cancel failed: %s", exc)
                return "⚠️ Couldn't cancel that — try again."
        if record is None:
            return "Nothing to cancel — that order was already placed, cancelled, or expired."
        sym = record.get("intent", {}).get("symbol", "your")
        return f"✖️ Cancelled — the {sym} order was discarded. Nothing was placed."

    async def _submit_staged_order(self, token: str) -> str:
        """Place a previously staged order via the gate submit chokepoint, and
        return a chat-ready result line. Never raises (failure -> a message).
        Serialized under _place_lock so two near-simultaneous confirms of the same
        token can't both consume it and double-submit."""
        async with self._place_lock:
            return await self._submit_locked(token)

    async def _submit_locked(self, token: str) -> str:
        try:
            rc, out, err = await _gate_submit(token, _SUBMIT_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, TimeoutError):
            log.error("gate submit timed out for token %s", token)
            return ("⚠️ The order is taking longer than expected — status is UNCERTAIN. "
                    "Check TWS before re-sending so you don't place it twice.")
        except Exception as exc:  # noqa: BLE001 — a bad submit must not crash the poll loop
            log.error("gate submit failed: %s", exc)
            return "⚠️ Couldn't place that order. Nothing happened to your account."
        return _friendly_submit_reply(rc, out, err)

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
            self.alert(f"\U0001f6d1 {b('ACTION FAILED')}: {code(action.value)} did not complete "
                       f"({esc(exc)}). The brake may NOT be armed — verify manually.")

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
        log.error("disconnected from TWS — reconnecting")
        asyncio.ensure_future(self._reconnect())

    async def _reconnect(self) -> None:
        """Reconnect with capped backoff, persistently. The nightly Gateway
        auto-restart darkens the API for ~2-3 min, so we never 'give up'; instead
        we stay quiet during an expected restart / the Sunday weekly window, and
        edge-trigger ONE BRAKE-BLIND alert for an unexpected outage past the grace.
        On success we re-subscribe reqPnL (lost on disconnect) and re-evaluate."""
        if self._reconnecting:
            return
        self._reconnecting = True
        start = self._now()
        delays = (5, 10, 20, 40, 60)
        attempt = 0
        try:
            while True:
                await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
                attempt += 1
                try:
                    await self.conn.connect_async()
                except Exception as exc:  # noqa: BLE001 — retry on any failure
                    now = self._now()
                    elapsed = (now - start).total_seconds()
                    expected = is_expected_restart(
                        now, self.config.live.gateway_restart_et,
                        self.config.live.restart_quiet_window_min) or is_weekly_relogin_window(
                        now, self.config.live.weekly_relogin_reset_et,
                        self.config.live.weekly_relogin_probe_et)
                    if not self._blind_alerted and should_alert_blind(
                            elapsed, expected,
                            self.config.live.reconnect_alert_after_seconds,
                            self.config.live.restart_quiet_window_min):
                        self.alert(f"\U0001f6d1 {b('BRAKE BLIND')}: disconnected from TWS for "
                                   f"~{int(elapsed)}s and still retrying — check the Gateway.")
                        self._blind_alerted = True
                    log.error("reconnect failed (elapsed %.0fs): %s", elapsed, exc)
                    continue
                # connected
                self._subscribe_pnl()
                if self._blind_alerted:
                    self.alert(f"✅ {b('reconnected')} — brake restored.")
                self._blind_alerted = False
                log.info("reconnected to TWS")
                self.evaluate_and_handle("reconnect")
                return
        finally:
            self._reconnecting = False

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
        # Drain any pre-startup backlog WITHOUT handling it: a `CONFIRM <token>`
        # the operator sent before a restart could otherwise auto-submit a staged
        # order that survived on disk — "nothing auto-fires" must hold across
        # restarts too. Tell the user so a dropped message isn't a silent void.
        try:
            backlog, _cbs, self._tg_offset = await self.telegram.poll(self._tg_offset)
            if backlog:
                log.warning("telegram: skipped %d backlog message(s) on startup", len(backlog))
                await self._reply(
                    f"♻️ I just restarted and skipped {len(backlog)} earlier message(s) "
                    f"for safety — please resend anything you still want."
                )
        except Exception as exc:  # noqa: BLE001
            log.error("telegram backlog drain failed: %s", exc)
        # Register the slash-command menu so /leverage, /pnl, … show as shortcuts.
        await self.telegram.set_my_commands(_BOT_COMMANDS)
        while True:
            try:
                texts, callbacks, self._tg_offset = await self.telegram.poll(self._tg_offset)
                for cb in callbacks:                            # button taps are cheap → inline
                    await self._safe_handle_callback(cb)
                for text in texts:
                    if _is_fast_message(text):
                        await self._safe_handle(text)              # cheap: inline, ahead of agent runs
                    else:
                        # slow agent run: don't block the poll loop. Hold a strong
                        # ref (asyncio keeps only a weak one) so it can't be GC'd
                        # mid-flight; the semaphore bounds concurrency.
                        task = asyncio.ensure_future(self._safe_handle(text))
                        self._agent_tasks.add(task)
                        task.add_done_callback(self._agent_tasks.discard)
            except Exception as exc:  # noqa: BLE001
                log.error("telegram poll error: %s", exc)
                await asyncio.sleep(5)

    async def _safe_handle(self, text: str) -> None:
        """Run handle_telegram_text guarded — one bad message (or a spawned agent
        task) must never drop the poll loop."""
        try:
            await self.handle_telegram_text(text)
        except Exception as exc:  # noqa: BLE001
            log.error("handling telegram message failed: %s", exc)
            await self._reply("⚠️ Something went wrong handling that. Your account is untouched.")

    async def _safe_handle_callback(self, cb: dict) -> None:
        try:
            await self.handle_callback(cb.get("data", ""), cb.get("id"), cb.get("message_id"))
        except Exception as exc:  # noqa: BLE001
            log.error("handling telegram callback failed: %s", exc)

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
            self.alert(f"\U0001f6d1 {b('BRAKE BLIND')}: snapshot refresh failed ({esc(exc)}) — "
                       f"data may be stale; check TWS.")

    async def _staleness_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.live.staleness_seconds)
            self._refresh_if_stale()

    def _check_weekly_relogin(self) -> None:
        """One weekly-probe tick. After the Sunday 01:00 ET token reset the Gateway
        needs a 2FA re-login; if the API is still down at probe time, send an
        ACTIONABLE nudge (distinct from the generic BRAKE-BLIND) so the tap happens
        before Monday's open. Edge-quiet when healthy."""
        if self.ib.isConnected():
            log.info("weekly probe: connection healthy")
            return
        self.alert(f"\U0001f510 {b('Weekly re-login required')}: the IBKR Sunday reset "
                   f"logged the Gateway out. Approve the IBKR-Mobile push (or open the "
                   f"Gateway) so the brake is live before Monday's open.")

    async def _weekly_probe_loop(self) -> None:
        while True:
            now = self._now()
            nxt = next_weekly_probe_dt(now, self.config.live.weekly_relogin_probe_et)
            await asyncio.sleep(max(0.0, (nxt - now).total_seconds()))
            try:
                self._check_weekly_relogin()
            except Exception as exc:  # noqa: BLE001 — a probe failure must not drop the loop
                log.error("weekly probe failed: %s", exc)

    def run(self) -> None:
        self.conn.connect()
        self._subscribe_pnl()
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
        asyncio.ensure_future(self._weekly_probe_loop())
        self.ib.run()  # loop.run_forever()


def _configure_logging() -> None:
    """Root logging for the daemon. Raise the httpx logger to WARNING: it logs
    every request URL at INFO, and the Telegram Bot API embeds the bot token in
    the URL path (/bot<TOKEN>/getUpdates), so on each poll the token was written
    to the log file in plaintext. WARNING+ still surfaces real HTTP failures."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    _configure_logging()
    load_env_file()  # populate Telegram creds from .env before telegram_from_env() reads them
    config = load_config("config/rules.yaml")
    BrakeDaemon(config).run()


if __name__ == "__main__":
    main()

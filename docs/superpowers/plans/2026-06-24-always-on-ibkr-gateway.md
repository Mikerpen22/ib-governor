# Always-On IBKR Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operator's always-on Mac keep an IBKR connection alive unattended (IB Gateway + IBC), and harden the daemon to survive the nightly auto-restart without false alarms while proactively surfacing the weekly Sunday re-login.

**Architecture:** Two halves. **Part 1 (code, TDD):** harden `daemon.py` so a multi-minute reconnect is routine, the "blind" alert is edge-triggered, `reqPnL` is re-subscribed after reconnect, and a Sunday probe nudges the weekly 2FA tap — all driven by four new pure helpers + five new `LiveConfig` fields. **Part 2 (operator runbook):** install IB Gateway + IBC under `launchd` on the Mac, harden macOS for always-on, create a second IBKR username, and cut over paper→live.

**Tech Stack:** Python 3.12, Pydantic v2 (config), `ib_async` (IBKR socket API), `asyncio` (daemon loops), pytest. IB Gateway + IBC (IbcAlpha) + macOS `launchd`/`pmset` for deployment.

Design spec: `docs/superpowers/specs/2026-06-24-always-on-ibkr-gateway-design.md`.

## Global Constraints

- **Python 3.12**; package is `governor` under `src/`; pytest sets `pythonpath = ["src"]` (run `pytest -q` from repo root).
- **Ships SAFE.** `config/rules.yaml` defaults `dry_run: true` AND `readonly: true`. **NEVER commit `config/rules.yaml` armed.** This plan changes neither default.
- **One write chokepoint** — `src/governor/actions/executor.py` (`_guarded`). Do **not** add write paths. (Part 1 is read/alert-only.)
- **Edge-triggered alerts only.** A standing condition (incl. "BRAKE BLIND") alerts ONCE per episode, with a one-line "recovered" on clear — never re-spam (the `no-redundant-standing-alerts` rule).
- **Integration tests skip without TWS** (marked `integration`); the new unit tests must pass on a bare clone with no TWS.
- **Commit style:** conventional commits (`feat:`/`fix:`/`test:`/`docs:`/`chore:`). Attribution is disabled globally — no `Co-Authored-By` trailer.
- **Style:** immutable patterns, small focused functions; new pure helpers live beside `next_briefing_dt`/`is_stale` in `daemon.py`; mirror existing test patterns (`SimpleNamespace` fakes, ET-aware datetimes).
- **All times are ET.** Use the module-level `ET = ZoneInfo("America/New_York")`.

---

## PART 1 — Daemon hardening (TDD)

### Task 1: Add unattended-operation config fields to `LiveConfig`

**Files:**
- Modify: `src/governor/config.py:82-115` (the `LiveConfig` model)
- Test: `tests/live/test_config_live.py`

**Interfaces:**
- Produces: five new `LiveConfig` fields — `gateway_restart_et: str`, `restart_quiet_window_min: float`, `reconnect_alert_after_seconds: float`, `weekly_relogin_reset_et: str`, `weekly_relogin_probe_et: str` — used by Tasks 2–6.

- [ ] **Step 1: Write the failing tests**

Add to `tests/live/test_config_live.py`:

```python
def test_live_unattended_defaults():
    lc = LiveConfig()
    assert lc.gateway_restart_et == "23:59"
    assert lc.restart_quiet_window_min == 10.0
    assert lc.reconnect_alert_after_seconds == 90.0
    assert lc.weekly_relogin_reset_et == "01:00"
    assert lc.weekly_relogin_probe_et == "09:00"


def test_live_rejects_bad_restart_time():
    with pytest.raises(ValueError):
        LiveConfig(gateway_restart_et="24:00")


def test_live_rejects_bad_probe_time():
    with pytest.raises(ValueError):
        LiveConfig(weekly_relogin_probe_et="9am")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/live/test_config_live.py -q`
Expected: FAIL (`AttributeError`/unexpected-keyword for the new fields).

- [ ] **Step 3: Add the fields + validator**

In `src/governor/config.py`, inside `LiveConfig` (after `action_cooldown_seconds`, before the existing validators):

```python
    # --- unattended operation (Gateway + IBC) ---
    gateway_restart_et: str = "23:59"                    # IBC AutoRestartTime (HH:MM ET); a disconnect near this is routine
    restart_quiet_window_min: NonNegativeFloat = 10.0    # +/- minutes around the restart treated as an expected outage
    reconnect_alert_after_seconds: NonNegativeFloat = 90.0  # UNEXPECTED disconnect -> edge-alert BRAKE BLIND after this
    weekly_relogin_reset_et: str = "01:00"               # IBKR Sunday token reset (HH:MM ET)
    weekly_relogin_probe_et: str = "09:00"               # Sunday connectivity probe (HH:MM ET) -> actionable re-login nudge
```

Then add a validator next to `_valid_close`:

```python
    @field_validator("gateway_restart_et", "weekly_relogin_reset_et", "weekly_relogin_probe_et")
    @classmethod
    def _valid_et_time(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError(f"time must be HH:MM, got {v!r}")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/live/test_config_live.py -q`
Expected: PASS (all, including the pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add src/governor/config.py tests/live/test_config_live.py
git commit -m "feat(config): add unattended-operation scheduling fields to LiveConfig"
```

---

### Task 2: `is_expected_restart` helper (with midnight-wrap)

**Files:**
- Modify: `src/governor/live/daemon.py` (add near `next_briefing_dt`, ~line 86)
- Test: `tests/live/test_daemon_core.py`

**Interfaces:**
- Consumes: `ET` (module constant), `_parse_hhmm` (added here).
- Produces: `_parse_hhmm(hhmm: str) -> tuple[int, int]`; `is_expected_restart(now: dt.datetime, restart_et: str, window_min: float) -> bool` — used by Task 5.

- [ ] **Step 1: Write the failing tests**

Add to `tests/live/test_daemon_core.py` (it already imports `dt`, `ET`):

```python
from governor.live.daemon import is_expected_restart  # add to the existing import line


def test_expected_restart_inside_window():
    now = dt.datetime(2026, 6, 17, 23, 55, tzinfo=ET)   # 4 min before 23:59
    assert is_expected_restart(now, "23:59", 10.0) is True


def test_expected_restart_outside_window():
    now = dt.datetime(2026, 6, 17, 12, 0, tzinfo=ET)
    assert is_expected_restart(now, "23:59", 10.0) is False


def test_expected_restart_wraps_past_midnight():
    now = dt.datetime(2026, 6, 18, 0, 5, tzinfo=ET)      # 6 min after a 23:59 restart
    assert is_expected_restart(now, "23:59", 10.0) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: FAIL (`ImportError: cannot import name 'is_expected_restart'`).

- [ ] **Step 3: Implement the helpers**

In `src/governor/live/daemon.py`, just below `ET = ZoneInfo(...)` / above `next_briefing_dt`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/governor/live/daemon.py tests/live/test_daemon_core.py
git commit -m "feat(daemon): is_expected_restart helper (midnight-wrap aware)"
```

---

### Task 3: Sunday helpers — `next_weekly_probe_dt` + `is_weekly_relogin_window`

**Files:**
- Modify: `src/governor/live/daemon.py` (below `is_expected_restart`)
- Test: `tests/live/test_daemon_core.py`

**Interfaces:**
- Consumes: `ET`, `_parse_hhmm`.
- Produces: `next_weekly_probe_dt(now, probe_et) -> dt.datetime`; `is_weekly_relogin_window(now, reset_et, probe_et) -> bool` — used by Tasks 5 & 6.
- Note: in `datetime`, Monday=0 … **Sunday=6**.

- [ ] **Step 1: Write the failing tests**

Add to `tests/live/test_daemon_core.py`:

```python
from governor.live.daemon import is_weekly_relogin_window, next_weekly_probe_dt


def test_next_weekly_probe_rolls_to_sunday():
    now = dt.datetime(2026, 6, 17, 12, 0, tzinfo=ET)    # Wed 2026-06-17
    nxt = next_weekly_probe_dt(now, "09:00")
    assert nxt.weekday() == 6                            # Sunday
    assert (nxt.hour, nxt.minute) == (9, 0)
    assert nxt.date() == dt.date(2026, 6, 21)           # the coming Sunday


def test_next_weekly_probe_same_sunday_before_time():
    now = dt.datetime(2026, 6, 21, 7, 0, tzinfo=ET)     # Sunday, before 09:00
    nxt = next_weekly_probe_dt(now, "09:00")
    assert nxt.date() == dt.date(2026, 6, 21)


def test_next_weekly_probe_after_time_goes_next_week():
    now = dt.datetime(2026, 6, 21, 10, 0, tzinfo=ET)    # Sunday, after 09:00
    nxt = next_weekly_probe_dt(now, "09:00")
    assert nxt.date() == dt.date(2026, 6, 28)


def test_weekly_relogin_window_true_on_sunday_morning():
    now = dt.datetime(2026, 6, 21, 3, 0, tzinfo=ET)     # Sunday 03:00, between 01:00 and 09:00
    assert is_weekly_relogin_window(now, "01:00", "09:00") is True


def test_weekly_relogin_window_false_off_sunday():
    now = dt.datetime(2026, 6, 20, 3, 0, tzinfo=ET)     # Saturday
    assert is_weekly_relogin_window(now, "01:00", "09:00") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

In `src/governor/live/daemon.py`, below `is_expected_restart`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/governor/live/daemon.py tests/live/test_daemon_core.py
git commit -m "feat(daemon): Sunday weekly-relogin window + next-probe helpers"
```

---

### Task 4: `should_alert_blind` decision helper

**Files:**
- Modify: `src/governor/live/daemon.py` (below the Sunday helpers)
- Test: `tests/live/test_daemon_core.py`

**Interfaces:**
- Produces: `should_alert_blind(elapsed_seconds: float, expected: bool, alert_after_seconds: float, restart_window_min: float) -> bool` — used by Task 5.

- [ ] **Step 1: Write the failing tests**

```python
from governor.live.daemon import should_alert_blind


def test_blind_alert_unexpected_after_grace():
    assert should_alert_blind(120.0, expected=False,
                              alert_after_seconds=90.0, restart_window_min=10.0) is True
    assert should_alert_blind(30.0, expected=False,
                              alert_after_seconds=90.0, restart_window_min=10.0) is False


def test_blind_alert_expected_tolerates_full_window():
    # Inside an expected restart: a normal 3-min outage must NOT alert (180s < 600s),
    # but a 12-min stall during the window does.
    assert should_alert_blind(180.0, expected=True,
                              alert_after_seconds=90.0, restart_window_min=10.0) is False
    assert should_alert_blind(720.0, expected=True,
                              alert_after_seconds=90.0, restart_window_min=10.0) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

```python
def should_alert_blind(elapsed_seconds: float, expected: bool,
                       alert_after_seconds: float, restart_window_min: float) -> bool:
    """Whether a still-down link warrants the (edge-triggered) BRAKE-BLIND alert.
    During an expected restart/weekly window, tolerate the full window before
    crying wolf; otherwise alert after the short grace."""
    threshold = restart_window_min * 60.0 if expected else alert_after_seconds
    return elapsed_seconds >= threshold
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/governor/live/daemon.py tests/live/test_daemon_core.py
git commit -m "feat(daemon): should_alert_blind threshold helper"
```

---

### Task 5: Reconnect hardening — persistent retry, edge alert, PnL re-subscribe

**Files:**
- Modify: `src/governor/live/daemon.py` — `BrakeDaemon.__init__` (~256-269), `_on_disconnect` (591-593), `_reconnect` (595-605)
- Test: `tests/live/test_daemon_core.py`

**Interfaces:**
- Consumes: `is_expected_restart` (Task 2), `is_weekly_relogin_window` (Task 3), `should_alert_blind` (Task 4), the config fields (Task 1).
- Produces: a `_reconnect()` that loops until connected, alerts BRAKE-BLIND at most once per episode, re-subscribes `reqPnL` on success, and is guarded by `self._reconnecting`.

- [ ] **Step 1: Write the failing test**

Add to `tests/live/test_daemon_core.py` (top-of-file imports already include `asyncio`? add `import asyncio` if missing):

```python
import asyncio


def test_reconnect_resubscribes_pnl_and_recovers(monkeypatch):
    d = BrakeDaemon(RulesConfig())
    events = []
    monkeypatch.setattr(d, "alert", lambda text, **k: events.append(("alert", text)))
    monkeypatch.setattr(d, "_subscribe_pnl", lambda: events.append(("pnl", None)))
    monkeypatch.setattr(d, "evaluate_and_handle", lambda reason: events.append(("eval", reason)))

    attempts = {"n": 0}

    async def fake_connect():
        attempts["n"] += 1
        if attempts["n"] < 3:            # fail twice, then succeed
            raise ConnectionError("gateway not up yet")

    monkeypatch.setattr(d.conn, "connect_async", fake_connect)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    asyncio.run(d._reconnect())

    assert ("pnl", None) in events                      # re-subscribed after reconnect
    assert ("eval", "reconnect") in events              # re-evaluated on return
    assert attempts["n"] == 3                           # retried until success


def test_reconnect_guard_prevents_concurrent_loops():
    d = BrakeDaemon(RulesConfig())
    d._reconnecting = True
    # Already reconnecting -> the coroutine returns immediately without touching conn.
    asyncio.run(d._reconnect())
    assert d._reconnecting is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: FAIL (`AttributeError: '_reconnecting'` / no PnL re-subscribe).

- [ ] **Step 3: Implement**

In `BrakeDaemon.__init__`, add beside the other state flags (near `self._announced_keys`):

```python
        self._reconnecting = False        # guard: at most one reconnect loop at a time
        self._blind_alerted = False       # edge-trigger: BRAKE BLIND announced once per blind episode
```

Replace `_on_disconnect` (drop the eager scream — the loop owns alerting now):

```python
    def _on_disconnect(self) -> None:
        log.error("disconnected from TWS — reconnecting")
        asyncio.ensure_future(self._reconnect())
```

Replace `_reconnect` entirely:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full live suite (no regressions)**

Run: `pytest tests/live/ -q -m 'not integration'`
Expected: PASS (the routing/telegram/handle tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/governor/live/daemon.py tests/live/test_daemon_core.py
git commit -m "feat(daemon): survive nightly restart — persistent reconnect, edge BRAKE-BLIND, PnL re-subscribe"
```

---

### Task 6: Weekly re-login probe

**Files:**
- Modify: `src/governor/live/daemon.py` — add `_check_weekly_relogin` + `_weekly_probe_loop`; wire into `run()` (~704-706)
- Test: `tests/live/test_daemon_core.py`

**Interfaces:**
- Consumes: `next_weekly_probe_dt` (Task 3); `self.ib.isConnected()`.
- Produces: `_check_weekly_relogin()` (one tick — actionable nudge if down); `_weekly_probe_loop()` (scheduler).

- [ ] **Step 1: Write the failing tests**

```python
from types import SimpleNamespace


def test_weekly_probe_alerts_when_disconnected(monkeypatch):
    d = BrakeDaemon(RulesConfig())
    d.conn.ib = SimpleNamespace(isConnected=lambda: False)
    msgs = []
    monkeypatch.setattr(d, "alert", lambda text, **k: msgs.append(text))
    d._check_weekly_relogin()
    assert any("re-login" in m.lower() for m in msgs)


def test_weekly_probe_quiet_when_connected(monkeypatch):
    d = BrakeDaemon(RulesConfig())
    d.conn.ib = SimpleNamespace(isConnected=lambda: True)
    msgs = []
    monkeypatch.setattr(d, "alert", lambda text, **k: msgs.append(text))
    d._check_weekly_relogin()
    assert msgs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: FAIL (`AttributeError: _check_weekly_relogin`).

- [ ] **Step 3: Implement**

In `src/governor/live/daemon.py`, add beside `_refresh_if_stale` / `_staleness_loop`:

```python
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
```

In `run()`, alongside the other `ensure_future` loop launches:

```python
        asyncio.ensure_future(self._weekly_probe_loop())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/live/test_daemon_core.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/governor/live/daemon.py tests/live/test_daemon_core.py
git commit -m "feat(daemon): Sunday weekly re-login probe with actionable nudge"
```

---

### Task 7: Document the new knobs + the Gateway port

**Files:**
- Modify: `docs/HANDBOOK.md` (the §5 circuit-breaker / launchd area) and `docs/RULES.md` only if it is config-generated (skip if so).

**Interfaces:** none (docs).

- [ ] **Step 1: Add a short subsection to `docs/HANDBOOK.md` §5**

Document, in the house style, that for unattended operation the daemon connects to **IB Gateway** (`live.port: 4001` live / `4002` paper, not TWS 7496/7497) and the new `live:` knobs: `gateway_restart_et`, `restart_quiet_window_min`, `reconnect_alert_after_seconds`, `weekly_relogin_reset_et`, `weekly_relogin_probe_et`. Note the daemon now tolerates the nightly restart silently and nudges once on Sunday morning if the weekly re-login is pending. Point to the deployment runbook (Part 2 of this plan / a future `docs/DEPLOY-MAC.md`).

- [ ] **Step 2: Verify the suite is still green**

Run: `pytest -q`
Expected: PASS (≈375+ tests; integration skips without TWS).

- [ ] **Step 3: Commit**

```bash
git add docs/HANDBOOK.md
git commit -m "docs(handbook): document Gateway port + unattended scheduling knobs"
```

---

## PART 2 — macOS deployment runbook (operator steps — NOT test-driven)

> These are one-time operator actions on the always-on Mac. They have no unit
> tests; the acceptance test is **B7** (paper validation through a real nightly
> restart). Do Part 1 first — deploy onto a daemon that already survives restarts.

### B1 — Create a second IBKR username (manual-use lane)

1. Client Portal → top-right user icon → **Settings → Users & Access Rights → Users → +**.
2. Create username **B**; mark it a secondary user; finish.
3. Decide market data: keep **live data subscriptions on username A** (the Gateway user). Do **not** duplicate them to B (billed per username).
4. Result: **A** = unattended Gateway (this plan), **B** = your manual TWS/mobile.

### B2 — Install IB Gateway (offline build) + IBC

1. Download **IB Gateway** (stable) — on Apple Silicon take the **native-ARM** build (no Rosetta on current versions). Use the **offline** installer (IBC requires it).
2. Download **IBC** for macOS from `github.com/IbcAlpha/IBC` (the `IBCMacos-*.zip`); unzip to `~/ibc`.
3. Copy IBC's sample `config.ini` to `~/ibc/config.ini`.

### B3 — Configure IBC (`~/ibc/config.ini`) + a creds file

Set at minimum (paper first):

```ini
IbLoginId=
IbPassword=
TradingMode=paper
ReadOnlyLogin=no
AutoRestartTime=23:59
ReloginAfterSecondFactorAuthenticationTimeout=yes
ExistingSessionDetectedAction=primary
AcceptIncomingConnectionAction=accept
IbAutoClosedown=no
```

- Put the username-A password only in this file; `chmod 600 ~/ibc/config.ini`. **Never commit it.** (Or use IBC's separate encrypted-credentials option.)
- Keep `AutoRestartTime` (`23:59`) equal to the daemon's `gateway_restart_et` so "expected restart" lines up.

### B4 — `launchd` LaunchAgent for IBC/Gateway

Create `~/Library/LaunchAgents/com.ib-governor.gateway.plist` modeled on the
existing `launchd/com.ib-governor.daemon.plist.template` (absolute paths; launchd
won't expand `~`/`$VARS`). It must:
- `ProgramArguments` → IBC's `gatewaystart.sh` (with `TWS_MAJOR_VRSN`, `IBC_INI=~/ibc/config.ini`, `TRADING_MODE=paper`).
- `RunAtLoad = true`, `KeepAlive = true`, `ThrottleInterval = 60` (calm respawn if Gateway isn't ready).
- `EnvironmentVariables` → `DISPLAY`-free Mac GUI session (it's a LaunchAgent, so it has the Aqua session); set `HOME`, `PATH`.
- Logs → `~/ib-governor/logs/gateway.{out,err}.log`.

Load it: `launchctl load ~/Library/LaunchAgents/com.ib-governor.gateway.plist`.
First start: approve the **IBKR-Mobile 2FA push** once. Confirm the API is up:
`nc -z 127.0.0.1 4002 && echo "paper API listening"`.

> Plan deliverable: add `launchd/com.ib-governor.gateway.plist.template` to the
> repo (mirroring the daemon template) in a follow-up PR so this is reproducible.

### B5 — Harden macOS for always-on

```bash
sudo pmset -c sleep 0 disablesleep 1 powernap 0 autorestart 1 womp 1   # no sleep on AC; auto-restart after power loss
```
- Enable **auto-login** (System Settings → Users & Groups) so a reboot reaches the GUI session IBC needs.
- **FileVault:** keep it **ON** (you prioritized security). Accept that a *cold* reboot (OS update / power loss) needs one manual disk-unlock — rare for a desk Mac. Set a short screen-lock timeout.
- Turn the **macOS firewall on**; keep **Screen Sharing off** unless you reach it via **Tailscale** (optional, for remote check-in).
- Confirm IBKR **login-notification emails** are on.

### B6 — Point the daemon at the Gateway

1. In your local (armed/skip-worktree) `config/rules.yaml`, set `live.port: 4002` (paper) — host stays `127.0.0.1`, `client_id: 4`.
2. Reload the daemon LaunchAgent (`launchctl unload … && launchctl load …`).
3. The daily-summary + NL/ask `claude` lanes already run on this Mac — no change.

### B7 — Validate on paper (the acceptance test)

- [ ] Daemon connects to the paper Gateway; a `/pnl` or `/positions` Telegram query answers.
- [ ] **Let the 23:59 nightly restart happen** (or set `AutoRestartTime` a few minutes out to test): the daemon must **reconnect quietly within a few minutes** and **NOT** send a BRAKE-BLIND (Part 1, Task 5).
- [ ] Force a logout (quit Gateway): confirm **one** BRAKE-BLIND alert, then a "reconnected — brake restored" when it comes back (edge-triggered).
- [ ] Optionally set `weekly_relogin_probe_et` to a near time with the Gateway down → confirm the **"Weekly re-login required"** nudge (Task 6).

### B8 — Cut over to live

1. Confirm live market-data subscriptions sit on username **A**.
2. `IBC config.ini`: `TradingMode=live`; daemon `live.port: 4001`.
3. **Stay SAFE:** leave `dry_run: true` + `readonly: true`. Arming is a separate, later, deliberate step (HANDBOOK §8) — out of scope here.
4. Reload both LaunchAgents; approve the 2FA push; verify `nc -z 127.0.0.1 4001`.

---

## Self-review (completed)

- **Spec coverage:** §5.1 reconnect → Tasks 4–5; §5.2 weekly probe → Tasks 3,6; §5.3 config → Task 1 (+ helpers Tasks 2–4); §5.4 health → covered operationally by B4/B7's `nc` probe (no code task — flagged, not silently dropped). §4 (Gateway+IBC), §4.3 (2nd username), §4.4 (macOS hardening) → Part 2 B1–B8. §7 security → B5. §9 rollout phases → Part 1 = Phase 1; Part 2 = Phases 2–4.
- **Placeholder scan:** no TBD/"handle errors"/"similar to" — every code step has real code and exact commands.
- **Type consistency:** helper names/signatures match across tasks (`is_expected_restart`, `is_weekly_relogin_window`, `next_weekly_probe_dt`, `should_alert_blind`, `_parse_hhmm`); config field names identical in Task 1 and their consumers (Tasks 5–6); `_reconnecting`/`_blind_alerted` defined in Task 5 and used there.
- **Known follow-ups (not blockers):** commit a `launchd/com.ib-governor.gateway.plist.template` to the repo (B4) and a `docs/DEPLOY-MAC.md` extracted from Part 2, in a follow-on PR.

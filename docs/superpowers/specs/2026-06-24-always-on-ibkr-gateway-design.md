# Design Spec — Always-On IBKR Gateway for the ib-governor Breaker

- **Date:** 2026-06-24
- **Status:** Draft for review — **rev 2** (host = operator's own always-on Mac; VPS demoted to alternative)
- **Topic:** Keep IBKR connectivity continuously authenticated on the operator's
  own always-on Mac so the circuit-breaker daemon never goes blind.

---

## 1. Problem & Goal

The `ib-governor` daemon is a **client**; TWS/IB Gateway is the **server** it
depends on. The daemon already self-heals socket drops (`_on_disconnect →
_reconnect`) — but only if the thing it reconnects *to* is alive and
authenticated. Today TWS runs **manually** on the operator's Mac, so the breaker
goes blind whenever the Mac sleeps, reboots for an update, or TWS logs out.

**Goal:** make that **same Mac** keep an IBKR connection alive unattended —
- IB Gateway runs under `launchd` + IBC, unattended 24/5,
- **daily** auto-restarts reuse the session token with **no 2FA**,
- the only human touch is **one IBKR-Mobile push tap each Sunday** (after the
  weekly token reset),
- the daemon **survives the nightly restart** without false alarms and
  **proactively surfaces the weekly logout** so the tap happens leisurely over
  the weekend, not at Monday's open.

**Non-goals:**
- ❌ *"Never logged out."* Impossible — IBKR invalidates every session **Sunday
  ~01:00 ET**; the first login after that *requires* 2FA.
- ❌ *Arming live trading.* Ships SAFE (`dry_run: true` + `readonly: true`) and
  stays there. This work changes **where/how** the connection lives, never the
  safety posture.
- ❌ Replacing `ib_async`/the TWS socket API with the Client Portal REST API
  (considered and rejected — §11).

---

## 2. Locked decisions (from brainstorming)

| Decision | Choice | Why |
|---|---|---|
| **Where to run** | **Operator's own Mac** (stays put, always on), hardened | Zero cost; **creds never leave the operator's hardware** (best credential custody — directly serves the "secure" priority); reuses the existing `launchd` pattern. Tradeoff is reliability, mitigated by no-sleep + auto-login. |
| **Gateway** | **IB Gateway (Mac app) + IBC**, native under `launchd` — **no Docker** | Gateway is ~40% lighter than TWS and built for unattended use; native `launchd` beats a Docker-Desktop VM for a Mac GUI app. IBC is the correct automation for the TWS *socket* API. |
| **Account** | **Live** (API port 4001), plus a **free second username** | The breaker watches live trading. Second username lets the operator still use TWS by hand without session collisions. |
| **Weekly 2FA** | **One IBKR-Mobile push tap** (`ReloginAfterSecondFactorAuthenticationTimeout=yes`) | Lowest-risk; keeps 2FA a real second factor. Rejected: TOTP-seed-on-disk. |
| **Claude lanes** | **Already running on this Mac** — no change | The NL-order / ask-agent / daily-summary `launchd` agents already use the local `claude` login. |
| **Remote access** | **Tailscale optional** | No public endpoint needed (outbound long-poll). At your desk, the weekly tap is just a phone push and IBC drives the GUI. Tailscale only if you want to reach the Mac while away. |

---

## 3. Architecture (macOS-native)

```
┌─────────── Your Mac — stays put, always on, sleep disabled ────────────┐
│  launchd LaunchAgents (per-user GUI session):                          │
│   • IBC → IB Gateway (Java GUI, username A = LIVE) ── API :4001 ─┐      │
│       auto-restarts nightly (no 2FA) · Sunday push re-login      │      │
│   • ib-governor daemon (existing agent, repointed →127.0.0.1:4001)│     │
│       watches fills · evaluates rules · Telegram alerts (OUTBOUND)│     │
│   • daily-summary agent (existing) ─────────────────────────────┘      │
│  pmset: no sleep · auto-login · FileVault handled                      │
└────────┬──────────────────────────────────────────┬────────────────────┘
         │ outbound long-poll                        │ phone push (weekly)
    ┌────▼─────┐                                ┌─────▼─────────────┐
    │ Telegram │                                │ Your phone        │
    │  (you)   │                                │  Sunday 2FA tap   │
    └──────────┘                                └───────────────────┘

   Manual trading: open TWS with USERNAME B on the same Mac anytime — no collision.
   Optional: Tailscale → Screen Sharing/VNC to reach the Mac while away.
```

**Key properties:**
- **No public ingress.** Daemon long-polls Telegram outbound; Gateway API binds
  to `127.0.0.1` (local only — not even the LAN). Nothing to reach in.
- **Creds stay local** — no third-party hardware ever holds them.
- **Reuses `launchd`** — the daemon already runs this way (HANDBOOK §5).

---

## 4. Components

### 4.1 IB Gateway + IBC under `launchd`

- Install **IB Gateway** (Mac app; the **offline** build IBC requires) + **IBC**
  (IbcAlpha). On Apple Silicon use the native-ARM Gateway build (no Rosetta on
  current versions).
- **IBC `config.ini`** (creds from a `chmod 600` file, never committed):
  - `IbLoginId` = username **A**; `ReadOnlyLogin=no` (we want 2FA + write
    capability available — the daemon's own `readonly` lock governs writes).
  - `AutoRestartTime` = a market-closed ET slot (e.g. `23:59`).
  - `ReloginAfterSecondFactorAuthenticationTimeout=yes` (loop the Sunday push
    until you tap).
  - `ExistingSessionDetectedAction=primary`, `AcceptIncomingConnectionAction=accept`,
    `IbAutoClosedown=no`.
- **LaunchAgent** runs IBC's `gatewaystart.sh`: `RunAtLoad`, `KeepAlive`,
  `ThrottleInterval ≈ 60`. API on **4001** (live) / **4002** (paper), bound to
  `127.0.0.1`.

### 4.2 The daemon (existing `launchd` agent, repointed)

- Config change only: `live.host: 127.0.0.1`, **`live.port: 4001`** (was TWS
  7496). `client_id: 4` unchanged (gate=5, daily=6, technicals=7 stay distinct).
- The existing agent already `load_env_file()`s and sets `PATH`/`HOME`; the
  `claude` lanes already work. No container, no new auth.

### 4.3 Second username (single-session constraint)

IBKR allows only **one trading session per username**. The Gateway holds
username **A**; create a free secondary username **B** (Client Portal → Settings
→ Users & Access Rights → Add) for manual TWS/mobile use.
- **Market-data caveat:** live data subscriptions bill **per username** — keep
  them on **A**; don't duplicate to B (the "pay twice" trap). **Low-stakes for
  the breaker:** the daemon falls back to `reqMarketDataType(4)` (delayed-frozen)
  for sizing/notional, so it degrades gracefully.

### 4.4 macOS always-on hardening

- **No sleep (AC power):** `sudo pmset -c sleep 0 disablesleep 1 powernap 0
  autorestart 1 womp 1`, plus `caffeinate -dimsu` as belt-and-suspenders.
- **Auto-login** enabled so a reboot reaches the GUI session IBC needs.
- **FileVault** is the reboot blocker (see §7F for the security trade) — decide:
  keep it ON (secure; a rare cold reboot needs one manual unlock) vs. a staged
  unlock. Recommendation: **ON** (you prioritized security; desk Macs rarely
  cold-reboot).
- LaunchAgents (per-user GUI session), **not** LaunchDaemons.

---

## 5. Code changes in `ib-governor` (host-agnostic — needed identically on Mac or VPS)

The repo-side, unit-testable work. The nightly restart and weekly reset happen
on **any** host, so none of this changes with the hosting decision — only the
config *values* (port 4001/4002, local host) differ.

### 5.1 Reconnect hardening — survive the nightly restart

`_reconnect()` retries `5→10→20→40→60s` then **gives up after ~135s** with
`"BRAKE BLIND: reconnect gave up — manual intervention required"`. The nightly
auto-restart darkens the API for **~2–3 minutes** → as-is, the daemon
**false-alarms every night**.

**Change:** retry **persistently** with a capped backoff (escalate to 60s, then
hold) instead of giving up. Make the BRAKE-BLIND alert **edge-triggered** (alert
once, a one-line "recovered" on reconnect — consistent with the existing
standing-trip discipline and the `no-redundant-standing-alerts` memory). Suppress
the alert entirely when the disconnect falls inside the **expected nightly-restart
window**; for an **unexpected** disconnect, alert after a short grace. After a
successful reconnect, **re-subscribe `reqPnL`** (only `run()` subscribes today;
the server-side subscription is lost on disconnect, so `/pnl` would go cold).

### 5.2 Weekly-logout detection / Sunday probe

The highest-leverage addition. The Sunday 01:00 ET reset leaves IBC looping the
push, waiting for your tap; a generic reconnect failure isn't actionable.

**Change:** emit one **actionable** alert — *"🔐 Weekly re-login required —
approve the IBKR-Mobile push."* Add a **scheduled Sunday-morning probe**
(configurable ET time) that verifies the API is alive and alerts if not —
converting a Monday-open surprise into a leisurely weekend tap.

### 5.3 Config additions (validated in `config.py`, mirroring the `_HHMM` pattern)

Add to `LiveConfig`:
- `gateway_restart_et: str = "23:59"` — daily Gateway/IBC auto-restart (HH:MM ET).
- `restart_quiet_window_min: float = 10.0` — a disconnect within ±this of the
  restart time is "expected" → no alert.
- `reconnect_alert_after_seconds: float = 90.0` — for an **unexpected** disconnect,
  edge-trigger BRAKE-BLIND if not reconnected within this.
- `weekly_relogin_probe_et: str = "09:00"` — Sunday probe time (HH:MM ET).

New pure helpers (in `daemon.py`, unit-tested like `next_briefing_dt`):
`is_expected_restart(now, restart_et, window_min) -> bool` and
`next_weekly_probe_dt(now, probe_et) -> datetime`.

### 5.4 Health

Optional TCP probe / launchd watch on the Gateway API port to catch "process up
but API not listening" (a complement to the disconnect event).

---

## 6. Error handling & failure modes

| Failure | Behavior |
|---|---|
| Nightly auto-restart (~2–3 min dark) | Quiet reconnect (expected window); no alarm; re-subscribe PnL on return. |
| Weekly Sunday logout | One actionable "re-login required" alert; IBC loops the push; Sunday probe pre-warns. |
| Crash / cold start mid-week | 2FA re-triggered (token lost) → same actionable alert. |
| **Mac reboot / OS update** | `launchd` `KeepAlive` + auto-login bring Gateway + daemon back; **FileVault must be handled** or a cold reboot stalls at unlock. |
| **Mac sleeps anyway** (lid/battery) | `pmset` prevents it on AC; if it still sleeps, the disconnect path alerts (BRAKE BLIND). |
| Gateway up but API silent | TCP/health probe surfaces it. |
| Any uncertainty | Fail **SAFE / BRAKE BLIND** — never assume all-clear. |

---

## 7. Security model, safety & secrets (CRITICAL — non-negotiable)

The Mac holds **live broker credentials + an authenticated session**, but on
**hardware you control** (no third-party hypervisor — the key win over a VPS).

**A. Application safety (unchanged project posture):**
- Ships SAFE: `dry_run: true` + `readonly: true`. A read-only session **cannot
  place orders**. Arming is separate and deliberate. (Note: IBC `ReadOnlyLogin`
  stays `no` so the *option* to write exists post-arming; the daemon's own
  `readonly` connection flag is the lock that actually gates writes.)
- One write chokepoint (`actions/executor.py` `_guarded`); confirm-gated tokens.

**B. Network — zero public attack surface:**
- Daemon talks **outbound only**; Gateway API bound to `127.0.0.1` (local only).
  No inbound ports. macOS firewall on; **Screen Sharing off** unless reached via
  Tailscale (tailnet only, never public).

**C. IBKR account hardening:**
- **2FA stays a real second factor** (no TOTP seed on disk).
- **Keep IBKR login-notification emails on** to detect unexpected logins.
- **IP allowlist (optional on a home IP):** residential IPs can rotate, so
  decide between allowlisting the home IP (re-allowlist if it changes) vs.
  relying on 2FA + notifications. Less critical here than on a cloud IP.
- Second username scoped to only the rights it needs.

**D. Secrets at rest:**
- IBC creds file + `.env` `chmod 600`, gitignored. **Never** commit creds or
  `config/rules.yaml` armed. **FileVault** encrypts them at rest (custody win).
- `claude` CLI auth is local and access-controlled.

**E. macOS hygiene:** keep the OS patched (updates reboot → auto-login +
FileVault handling must let it recover); minimal listening services.

**F. Residual risk (honest):** **physical access** to the Mac (someone at your
desk) — mitigated by FileVault + a short screen-lock timeout. **Auto-login is a
trade:** a reboot enters the session without the login password, so FileVault
(which still demands the disk password at *cold* boot) + screen-lock are what
keep an unattended reboot from being an open door. **No** third-party hypervisor
risk (the VPS's residual) — that's the security upside of hosting at home.

---

## 8. Testing

Mirror the project's split (pure logic unit-tested; wiring is integration that
skips without TWS):
- **Unit:** `is_expected_restart` (in/out of the window), `next_weekly_probe_dt`
  (next Sunday rollover), the edge-triggered blind-alert state machine, the new
  config validators. No live TWS needed.
- **Integration (skipped on bare clone):** reconnect across a simulated restart;
  PnL re-subscription.
- **Manual validation:** point a **paper** daemon at the Gateway (port 4002);
  watch a nightly restart pass without a false BRAKE-BLIND; force a logout and
  confirm the actionable alert + the Sunday probe fire.

---

## 9. Rollout (phased — each phase independently valuable)

1. **Phase 1 — Daemon hardening (pure code, lands first).** §5 work: reconnect
   survival + PnL re-subscribe + weekly-logout/Sunday probe + config. Testable
   against your current TWS/Gateway; ships as a normal PR. *No infra.*
2. **Phase 2 — Gateway + IBC on the Mac.** Install IB Gateway + IBC + a
   LaunchAgent; bring it up against **paper** (port 4002); repoint a paper daemon.
3. **Phase 3 — Harden the Mac.** `pmset` no-sleep, auto-login, the FileVault
   decision, the second username. Run paper end-to-end through a nightly restart
   and a Sunday.
4. **Phase 4 — Live cutover.** Live data subs on username A, `live.port: 4001` —
   **still `dry_run`/`readonly` SAFE.** Arming is later and deliberate.

---

## 10. Open questions / flagged uncertainties

- **IB Gateway native ARM** (if Apple Silicon) — use the native build; confirm
  the current version suffix.
- **FileVault vs. unattended reboot** — recommend FileVault **ON** (you
  prioritized security; a desk Mac rarely cold-reboots, and the rare manual
  unlock is acceptable). Revisit only if reboots prove frequent.
- **Home-IP allowlist** — decide whether to IP-restrict the IBKR account.
- **Sunday-probe time** — default Sunday 09:00 ET (well after the 01:00 reset,
  before Monday's open).

---

## 11. Alternatives considered & rejected

- **VPS + Docker (`gnzsnz/ib-gateway-docker` on Hetzner CX22, ~$5/mo).** Strong
  24/7 uptime + full decoupling from the Mac. **Rejected** in favor of the
  operator's own Mac for **zero cost + credential custody** (no third-party
  hypervisor access — which serves the stated security priority). **Cleanly
  revisitable:** the Phase-1 daemon hardening and the Gateway+IBC mental model
  port directly to a Linux VPS if the Mac's uptime ever proves insufficient
  (frequent travel/reboots). Full research retained in git history (rev 1).
- **Client Portal Web API (REST/OAuth).** Could be more automatable (headless
  OAuth), but is a different API surface — we'd rewrite the entire `ib_async`
  data/order layer, retail OAuth eligibility is murky, and IBeam (its automation
  tool) can't drive the TWS socket the daemon speaks. **Rejected:**
  disproportionate rewrite.
- **Cloudflare hosting.** Serverless/scale-to-zero (Workers = 128 MB isolates;
  Containers sleep on idle) cannot host a persistent GUI-bearing JVM. **Rejected**
  for hosting; not needed for access either (outbound long-poll).

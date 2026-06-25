# Design Spec — Always-On IBKR Gateway for the ib-governor Breaker

- **Date:** 2026-06-24
- **Status:** Draft for review
- **Author:** brainstormed with Claude
- **Topic:** Keep IBKR connectivity continuously authenticated so the circuit-breaker daemon never goes blind.

---

## 1. Problem & Goal

The `ib-governor` daemon is a **client**; TWS/IB Gateway is the **server** it
depends on. The daemon already self-heals socket drops
(`_on_disconnect → _reconnect`, 5→60s backoff) — but that only helps if the
thing it reconnects *to* is alive and authenticated. Today TWS runs **manually
on a daily-driver Mac**, so the breaker goes blind whenever the Mac sleeps,
reboots for an update, or TWS logs out. The daemon's reliability is
mathematically capped by TWS's reliability.

**Goal:** an always-on IBKR connection so the daemon runs continuously —
- IB Gateway runs unattended 24/5,
- **daily** auto-restarts reuse the session token with **no 2FA**,
- the only human touch is **one IBKR-Mobile push tap each Sunday** (after the
  weekly token reset),
- the daemon **survives the nightly restart** without false alarms and
  **proactively surfaces the weekly logout** so the tap happens leisurely over
  the weekend, not at Monday's open.

**Explicit non-goals:**
- ❌ *"Never logged out."* Physically impossible — IBKR invalidates every
  session **Sunday ~01:00 ET**; the first login after that *requires* 2FA. No
  tool bypasses this for a normal live account.
- ❌ *Arming live trading.* This project ships SAFE (`dry_run: true` +
  `readonly: true`) and stays there. This work changes **where/how** the
  connection lives, **never** the safety posture. Arming remains a separate,
  deliberate operator step.
- ❌ Replacing `ib_async`/the TWS socket API with the Client Portal REST API.
  (Considered and rejected — see §11.)

---

## 2. Locked decisions (from brainstorming)

| Decision | Choice | Why |
|---|---|---|
| **Where to run** | **VPS + Docker** (`gnzsnz/ib-gateway-docker`), daemon co-located | Highest uptime, decouples from the personal Mac, ~$5/mo. Matches the "containerized" instinct. |
| **Gateway image** | `gnzsnz/ib-gateway-docker` (Gateway + IBC + Xvfb + VNC) | The de-facto, actively-maintained image; bundles IBC, the correct automation for the TWS *socket* API. |
| **Account** | **Live** (API port 4001), plus a **free second username** | The breaker's whole purpose is to watch live trading. Second username lets the operator still use TWS by hand without collisions. |
| **Weekly 2FA** | **One IBKR-Mobile push tap** (`RELOGIN_AFTER_TWOFA_TIMEOUT=yes`) | Lowest-risk; keeps 2FA a real second factor. Rejected: TOTP-seed-on-disk (zero-touch but demotes 2FA to a secret on disk). |
| **Claude lanes** | **Set up everything** — NL ordering, ask agent, daily-summary on the box | Preserve the current Mac experience. The deterministic core needs no `claude`; the conveniences do. |
| **Remote access** | **Tailscale** for VNC (the weekly tap) | Outbound long-poll Telegram means **no public endpoint is needed** — so no Cloudflare Tunnel. Tailscale covers the one inbound need (VNC), privately. |

---

## 3. Architecture

```
┌──────────────── VPS — always on, never sleeps (~$5/mo) ─────────────────┐
│                                                                         │
│  docker compose                                                         │
│  ┌───────────────────────────────┐   ┌──────────────────────────────┐  │
│  │ ib-gateway (gnzsnz image)     │   │ ib-governor daemon (our image)│ │
│  │  • IB Gateway (Java GUI)      │   │  • watches fills / PnL        │  │
│  │  • IBC: auto-login + restart  │◄──┤  • evaluates rules            │  │
│  │  • Xvfb (virtual display)     │API│  • Telegram alerts (OUTBOUND) │  │
│  │  • x11vnc :5900               │   │  • claude lanes (NL/ask/daily)│  │
│  │  logs in as USERNAME A (LIVE) │   │  connects → ib-gateway:4003   │  │
│  └──────────────┬────────────────┘   └──────────────────────────────┘  │
│        VNC :5900 │ (tailnet only)                                        │
│      Tailscale + cloudflared? NO — Tailscale only                       │
└──────────────────┼──────────────────────────────────────────────────────┘
                   │
    ┌──────────────▼───────────────┐       ┌───────────────────────────┐
    │ Your phone / laptop          │       │ Telegram (you)            │
    │  • Sunday IBKR-Mobile 2FA tap│       │  ← daemon long-polls OUT  │
    │  • VNC over Tailscale if GUI │       │    (no inbound port)      │
    └──────────────────────────────┘       └───────────────────────────┘

   Your Mac is no longer load-bearing: it can sleep / reboot / travel.
   Log into TWS with USERNAME B anytime — no collision with the Gateway.
```

**Key properties:**
- **No public ingress.** The daemon long-polls Telegram via `getUpdates`
  (outbound) and alerts outbound — nothing needs to reach *in*. The Gateway API
  binds to the container network / `127.0.0.1`, never the public internet. VNC
  (:5900) is reachable **only over Tailscale**.
- **Decoupled.** The breaker's uptime no longer depends on the Mac.
- **Defense in depth preserved.** `READ_ONLY_API=yes` on the Gateway mirrors the
  project's `readonly: true` lock; both must be deliberately flipped to place
  live orders.

---

## 4. Components

### 4.1 `ib-gateway` service (gnzsnz image)

Pinned to a specific tag (never `:latest`). Key env (exact gnzsnz names):

| Purpose | Env | Value (initial) |
|---|---|---|
| Live creds | `TWS_USERID`, `TWS_PASSWORD` (or `_FILE`) | username **A** — secrets, never committed |
| Mode | `TRADING_MODE` | `paper` first → `live` at cutover |
| Read-only lock | `READ_ONLY_API` | `yes` (mirror `readonly: true`) until armed |
| Daily restart | `AUTO_RESTART_TIME` | a market-closed ET slot (e.g. `11:59 PM`) |
| Weekly 2FA | `TWOFA_TIMEOUT_ACTION` | `restart` |
| Weekly 2FA | `RELOGIN_AFTER_TWOFA_TIMEOUT` | `yes` (loop the push until you tap) |
| API dialog | `TWS_ACCEPT_INCOMING` | `accept` |
| Timezone | `TIME_ZONE` (note: **not** `TZ`) | `America/New_York` |
| VNC | `VNC_SERVER_PASSWORD` (or `_FILE`) | secret |

**Lifecycle this produces:** logs in once → reuses the token across nightly
auto-restarts (no 2FA) → on Sunday after 01:00 ET the token is dead, IBC
re-issues the IBKR-Mobile push **in a loop** until you approve once.

### 4.2 `ib-governor` daemon service (our image)

- **Dockerfile** (`docker/Dockerfile`): `python:3.12-slim`, `pip install -e .`,
  install + authenticate the **`claude` CLI** (the "set up everything" choice),
  copy `config/`, entrypoint `python -m governor.live.daemon`.
- **Connection:** `live.host: ib-gateway`, `live.port:` the Gateway's
  container-internal API listener (gnzsnz republishes via `socat`; the exact
  internal port — 4003 live / 4004 paper — to be confirmed against the pinned
  tag's README). No host-exposed API port.
- **Secrets/env:** `.env` (gitignored) supplies `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, IBKR creds, VNC password. `load_env_file()` already runs
  at daemon startup.
- **`claude` auth:** the CLI's login/config must be present in the container
  (mounted volume or one-time `claude login`). Confirm headless auth works on a
  Linux box (flagged in §10).

### 4.3 Second username (single-session constraint)

IBKR allows only **one trading session per username**. The unattended Gateway
holds username **A**; create a free secondary username **B** (Client Portal →
Settings → Users & Access Rights → add user) for manual TWS/mobile use.

- **Market-data caveat:** live data subscriptions are billed **per username**.
  Moving them to A means B sees delayed quotes unless shared/duplicated.
  **Low-stakes for the breaker specifically:** the daemon already falls back to
  `reqMarketDataType(4)` (delayed-frozen) for sizing/notional, so it degrades
  gracefully if A's live data lags during migration.

### 4.4 Networking & remote access

- **Tailscale** on the VPS + your phone/laptop. VNC (:5900) bound to the tailnet
  only → do the weekly 2FA tap from anywhere, nothing public.
- **Interactive `/pre-trade` from your Mac** reaches the Gateway over Tailscale
  on its own client id (gate=5, daily=6 — distinct from the daemon's 4, so no
  collision). The API port is exposed to the **tailnet only**, never public.
- **PTC / static IP (the real cloud gotcha):** IBKR triggers a Pre-Trade
  Compliance restriction on first login from a **new IP**. Reserve a **static
  IP**, allowlist it in Client Portal → IP Restrictions, and set Gateway
  "Trusted IPs". Never release the IP.

---

## 5. Code changes in `ib-governor` (not just ops)

This is the part that lives in the repo and is unit-testable.

### 5.1 Reconnect hardening — survive the nightly restart

`_reconnect()` retries `5→10→20→40→60s` then **gives up after ~135s** with
`"BRAKE BLIND: reconnect gave up — manual intervention required"`. But the
nightly Gateway auto-restart darkens the API for **~2–3 minutes** — so as-is the
daemon would **false-alarm every single night**.

**Change:** keep retrying with a capped backoff (e.g. escalate to 60s then hold)
for a long window instead of giving up at 135s; treat a reconnect within the
expected restart window as routine (quiet). After a successful reconnect,
**re-subscribe `reqPnL`** (currently only `run()` subscribes; the server-side
subscription is lost on disconnect, so `/pnl` quick-answers would go cold).

**Honor the no-redundant-alerts rule:** the "blind" condition must be
**edge-triggered** — alert once when it goes blind, a one-line "recovered" when
it returns — never re-spam (consistent with the existing standing-trip
discipline and the `no-redundant-standing-alerts` memory).

### 5.2 Weekly-logout detection / Sunday probe

The highest-leverage addition. The Sunday 01:00 ET reset leaves IBC looping the
push waiting for your tap; the daemon's generic reconnect failures aren't
actionable.

**Change:** distinguish the **weekly-logout** condition and emit one **actionable**
alert — *"🔐 Weekly re-login required — approve the IBKR-Mobile push (VNC in if
needed)."* Add a **scheduled Sunday-morning probe** (configurable ET time) that
verifies the API is alive and alerts if not — converting "discover the logout at
Monday's open" into "tap leisurely over the weekend."

### 5.3 Config additions (validated in `config.py`)

- Gateway host/port (`ib-gateway` : internal API port) for the live/paper modes.
- Restart-window awareness (so the reconnect path knows when a dark API is
  expected vs a genuine outage).
- Sunday-probe schedule (ET time).
- All validated on load — no hardcoded values (project rule).

### 5.4 Health

Optional: a **TCP probe** / compose `healthcheck` on the Gateway API port (the
gnzsnz image ships none) to catch "process up but API not listening."

---

## 6. Error handling & failure modes

| Failure | Behavior |
|---|---|
| Nightly auto-restart (~2–3 min dark) | Quiet reconnect; no alarm; re-subscribe PnL on return. |
| Weekly Sunday logout | One actionable "re-login required" alert; IBC loops the push; Sunday probe pre-warns. |
| Crash / cold start mid-week | 2FA re-triggered (token lost) → same actionable alert. |
| VPS IP change | PTC restriction → mitigated by reserved static IP + allowlist. |
| Gateway up but API silent | TCP/healthcheck probe surfaces it. |
| Any uncertainty | Fail **SAFE / BRAKE BLIND** — never assume all-clear (existing posture). |

---

## 7. Safety & secrets (CRITICAL — non-negotiable)

- **Ships SAFE.** `dry_run: true` + `readonly: true` + `READ_ONLY_API=yes`.
  Arming live is a separate, deliberate step — out of scope here.
- **Creds never committed.** All secrets via `.env` / Docker secrets, gitignored.
  **Never commit `config/rules.yaml` armed.** Verify diffs before any commit.
- **One write chokepoint unchanged** — `actions/executor.py` (`_guarded`). No new
  write paths.
- **VNC never public** — Tailscale only. **Static IP allowlisted.**
- **`claude` auth** stored on the box must be access-controlled like a credential.

---

## 8. Testing

Mirror the project's split (pure logic unit-tested; wiring is integration that
skips without TWS):
- **Unit:** "is this dark API inside the expected restart window?", "is this the
  weekly-logout condition?", the edge-triggered alert state machine, config
  validation. No live TWS needed.
- **Integration (skipped on bare clone):** reconnect across a simulated restart;
  PnL re-subscription.
- **Manual validation:** bring the compose stack up against **paper** first;
  watch a nightly restart pass without a false BRAKE-BLIND; force a logout and
  confirm the actionable alert + Sunday probe.

---

## 9. Rollout (phased — each phase independently valuable)

1. **Phase 1 — Daemon hardening (pure code, lands first).** Reconnect survival +
   PnL re-subscribe + weekly-logout/Sunday probe + config. Testable on your Mac
   against the existing TWS; ship via normal PR. *No infra needed.*
2. **Phase 2 — Containerize.** `docker/Dockerfile` + `docker/docker-compose.yml`
   (gateway + daemon + claude). `compose up` locally against **paper**.
3. **Phase 3 — VPS bring-up.** Provision VPS + reserved IP, Tailscale, second
   username, IP allowlist. Run **paper** on the VPS end-to-end for a few days
   (verify a nightly restart and a Sunday tap).
4. **Phase 4 — Live cutover.** Move live data subs to username A, set
   `TRADING_MODE=live` (port 4001) — **still `dry_run`/`readonly` SAFE**. Arming
   is later and deliberate.

---

## 10. Open questions / flagged uncertainties

- **gnzsnz inter-container API binding** — confirm the exact internal port and
  any `socat`/bind-to-network setting for the daemon container against the
  pinned tag's README.
- **`claude` CLI headless auth on Linux** — confirm the login flow works
  unattended in the container (mount the config vs `claude login` once). If it's
  fragile, fall back to "core breaker only" and run claude lanes on the Mac.
- **VPS provider** — Hetzner US (~$5/mo, simple static IP) vs Oracle free tier
  (idle-reclamation + post-June-2026 free-tier cuts). Pick in Phase 3; Hetzner
  is the safer default.
- **Exact Sunday-probe time** — far enough after 01:00 ET to be settled, early
  enough to act before Monday open (e.g. Sunday 09:00 ET).

---

## 11. Alternative considered & rejected: Client Portal Web API

A different auth path (REST + OAuth) could be *more* automatable (OAuth 1.0a can
be truly headless), **but** it's a different API surface — we'd rewrite the
entire `ib_async` data/order layer for REST, retail OAuth eligibility is
officially murky, and IBeam (its automation tool) can't drive the TWS socket the
daemon speaks. **Rejected:** disproportionate rewrite for a daemon that already
works on `ib_async`; the Gateway+IBC path reuses all existing code.

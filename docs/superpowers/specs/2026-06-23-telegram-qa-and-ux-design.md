# Telegram Q&A + UX overhaul — design & plan

**Date:** 2026-06-23
**Status:** proposed (awaiting operator review)
**Branch:** `claude/eager-hawking-1esja9`

## Goal

Make the Telegram bot a real two-way cockpit, in three moves:

1. **Ask anything (read-only):** positions, leverage, today's trades, "analyze
   my book", a natural-language technical read on a symbol, news — not just
   order placement.
2. **Cleaner formatting:** Telegram-HTML house style (headers, bold, italic,
   underline, monospace) with no decorative separator lines.
3. **Frictionless confirm:** tap-to-confirm buttons on *every* confirmable
   thing (orders **and** circuit-breaker actions), and the card edits itself in
   place on tap instead of dropping a second message.

## Non-negotiable safety model (unchanged)

Nothing here weakens the brake. Restating the invariants every change must hold:

- **One write chokepoint.** Orders are placed only by `gate submit` →
  `ActionExecutor.place_order(s)` → `_guarded`. The new ask lane is **read-only**
  and never imports a write path.
- **Two default-closed locks.** `config/rules.yaml` keeps `dry_run: true` +
  `readonly: true`. None of this work flips a lock.
- **Nothing auto-fires.** Every order/action still waits for an explicit tap or
  typed `CONFIRM`. Buttons are a nicer *transport* for the same token; the token
  still gates `gate submit`.
- **Comms can never crash the brake.** Every new send/edit path is best-effort
  and falls back; an HTML parse error or a flaky agent degrades, never throws
  into the daemon loop.

## What already exists (reused, not rebuilt)

- `comms/telegram.py` — hand-rolled `httpx` client: `send` / `poll` /
  `answer_callback`. ~65 lines, no third-party Telegram lib.
- `comms/agent_runner.py` — a headless `claude -p` bridge with a read-only
  sandbox (`GOVERNOR_AGENT_SANDBOX=1` forces dry-run). Today its system prompt
  is hard-wired to *order placement only*.
- `live/daily.py::collect_day_data()` — already returns nav, `gross_leverage`,
  `margin_cushion`, today's `fills`, `positions`, unrealized P&L, index/VIX
  backdrop. This is the data source for the ask lane.
- `_confirm_keyboard()` + `handle_callback()` — inline ✅/✖️ buttons, but only on
  the NL-order path (callback data `confirm:` / `cancel:`).
- `technicals/assess.py` — pure setup assessment; needs a thin read-only CLI so
  the ask lane can request a technical read *without* staging an order.

---

# Workstream A — formatting (Telegram-HTML house style)

### Problem

`send.py --html` already supports `parse_mode=HTML`, and the daily-summary skill
uses it well. But the **daemon's own messages** (`alert()`, `_reply()`,
`_HELP_TEXT`, staged-action lines, submit replies) are sent as **plain text** —
no `parse_mode`. And `gate/render.py` panels lean on leading-space "indentation"
that Telegram ignores unless wrapped in `<pre>`, so they render ragged on a
phone.

### Design

New pure module `src/governor/comms/format.py`:

```python
def esc(s: str) -> str: ...                 # HTML-escape &, <, > (Telegram HTML)
def b(s: str) -> str: ...                   # <b>…</b>  (escapes inside)
def i(s: str) -> str: ...                   # <i>…</i>
def u(s: str) -> str: ...                   # <u>…</u>
def code(s: str) -> str: ...                # <code>…</code>
def header(emoji: str, title: str) -> str:  # e.g. "💰 <b>Book</b>"
def section(head: str, lines: list[str]) -> str:  # header + blank-line-spaced bullets
def strip_tags(s: str) -> str: ...          # for the macOS banner + plain fallback
```

House style (documented in the module + HANDBOOK): bold section headers with an
emoji anchor, blank line between sections, **no** `─────` separator lines,
italic for asides, `<code>` for tokens/symbols/numbers a user might copy.

**Robust send (the load-bearing safety bit).** `TelegramClient.send` gains an
internal fallback: if a `parse_mode="HTML"` post fails (Telegram 400 on bad
markup), retry once as **plain text with tags stripped**, so a formatting bug
can never silently swallow a brake alert. Logged as a warning.

Thread `parse_mode="HTML"` through `alert()` and `_reply()`. Convert the daemon's
ad-hoc message strings to small **pure builder functions** (testable without a
network), e.g. `help_message()`, `staged_action_message(action, mode, ttl)`,
`rule_alert(trip)`, and HTML-wrap `_REASON_REPLIES` / `_friendly_submit_reply`.
All dynamic content (symbols, messages, numbers) goes through `esc()`.

### Files

- NEW `src/governor/comms/format.py`
- EDIT `src/governor/comms/telegram.py` — HTML-with-plain-fallback in `send`
- EDIT `src/governor/live/daemon.py` — `parse_mode="HTML"`, builder functions
- EDIT `src/governor/comms/send.py` — reuse `strip_tags` for the banner
- EDIT `docs/HANDBOOK.md` — the house-style note

### Tests

- NEW `tests/comms/test_format.py` — escaping (incl. `&<>` in symbols/messages),
  builders, `strip_tags`.
- EDIT `tests/comms/test_telegram.py` — HTML send + the plain-text fallback on a
  simulated 400.
- EDIT daemon message tests — assert messages are HTML and well-formed.

---

# Workstream B — confirm UX (buttons everywhere + in-place edits)

### Problem

Buttons exist for NL orders, but **circuit-breaker actions** (lockout / trim /
platform-off) still go out as plain `Reply CONFIRM <token>` text via `alert()` —
no buttons (this is the "copy the token and type confirm" pain). And after a tap
the bot sends a *new* message instead of editing the original card.

### Design

**Namespaced callback data (back-compat).** Keep `confirm:<token>` = order (no
test churn) and add `action:<token>` for circuit-breaker actions; `cancel:<token>`
unchanged. `handle_callback` dispatches:

- `confirm:` → `_submit_staged_order` (gate submit chokepoint) — unchanged
- `action:`  → `on_confirm`-style execute via the in-memory `ConfirmTokenGate`
- `cancel:`  → `_cancel_staged_order` (orders) / drop the in-memory token (actions)

`_confirm_keyboard(token, kind="order")` builds the right prefix + button labels
("✅ Place order" vs "✅ Run lockout / Trim now").

**Buttons on circuit-breaker actions.** In `handle()`, the staged-action
announcement routes through a button-bearing send (best-effort, scheduled like
`alert`) carrying `action:<token>`, instead of a plain `CONFIRM <token>` line.
Typed `CONFIRM <token>` still works for both paths.

**In-place card edits (`editMessageText`).** Add
`TelegramClient.edit_message(message_id, text, parse_mode, reply_markup=None)`
and have `poll()` return the callback's `message_id` + `chat_id` (already in the
`callback_query.message` payload). On a tap, edit the original card to the
outcome ("✅ Placed — BUY 10 ORCL", "✖️ Cancelled", "🛑 Blocked") and drop the
keyboard — one clean card, no message spam. Typed-CONFIRM (no card id) keeps
sending a fresh reply. `send()` returns the sent `message_id` so non-callback
flows can edit later if needed.

### Files

- EDIT `src/governor/comms/telegram.py` — `edit_message`; `poll` returns
  `message_id`/`chat_id`; `send` returns `message_id`
- EDIT `src/governor/live/daemon.py` — namespaced keyboard + dispatch, buttons on
  actions, edit-on-tap
- EDIT `docs/HANDBOOK.md` — updated confirm flow

### Tests

- EDIT `tests/live/test_daemon_telegram_routing.py` — `action:` tap executes the
  breaker action (not gate submit); `confirm:`/`cancel:` unchanged; edit-on-tap
  called with the outcome.
- EDIT `tests/comms/test_telegram.py` — `edit_message`; `poll` surfaces
  `message_id`.

---

# Workstream C — the natural-language "ask" lane (read-only)

### Problem

Every non-confirm message is fed to an agent whose prompt insists it's an order.
"What's my leverage?" gets mis-read as a trade. The bridge + sandbox + data
collectors already exist; they're just pointed only at ordering.

### Design — three tiers, fastest first

**1. Intent split (`classify_message`).** A pure classifier returns
`ORDER | ASK` for any message that isn't already a command/confirm (those are
handled earlier). Heuristic: leading trade verbs (buy/sell/short/long/grab/trim/
add/close) + a symbol/qty → `ORDER`; otherwise `ASK`. **A misclassification is
not a safety event** — both lanes are read-only (the order lane only *stages*),
so we can tune the heuristic freely. Documented as such.

**2. Deterministic fast-path (`comms/ask.py::quick_answer`) — sub-second.** The
daemon already holds a **live** `ib` connection (client_id 4) with account
values, portfolio, and fills streamed/cached by `ib_async`. For the handful of
high-frequency questions we answer instantly *off the existing connection* — no
`claude` subprocess, no new TWS socket:

| Asks like… | Answer from |
|---|---|
| leverage / leverage ratio | `gross_leverage` (+ true econ. exposure incl. FUT notional) |
| trades today / what did I trade | today's `fills` summary |
| positions / book / what do I hold | `positions` table |
| p&l / pnl / how am I doing | realized today + top unrealized |
| cushion / margin | `margin_cushion` |

`quick_answer(text, snapshot) -> str | None` is pure (snapshot in, HTML out);
`None` = not a quick question → fall through to tier 3. The daemon computes the
snapshot via the existing `self.build()` (fast — `ib_async` accessors read warm
cached state; the slow index/VIX backdrop is **not** used here).

**3. The ask agent (open-ended analysis / technicals / news) — ~60–90s.** A
second `claude -p` lane with its own read-only system prompt and a wider, still
read-only toolbelt:

- tools: `Bash`, `Read`, **`WebSearch`, `WebFetch`** (news) — keep every write
  deny + the `GOVERNOR_AGENT_SANDBOX` dry-run env as defense-in-depth.
- it can run `governor.live.daily --json` (book/P&L/leverage), the new read-only
  `governor.technicals` CLI (a symbol's Stage-2/VCP or futures four-factor read,
  **without staging an order**), and search the web for news.
- system prompt: "You answer read-only questions about the operator's IBKR
  account and the market. Never place or stage orders. Reply concise, in
  Telegram HTML." Returns a chat-ready string; never raises.

**Read-only technicals CLI.** `gate analyze` writes a staged token even for a
mere question — an unwanted side effect on the ask lane. Add
`python -m governor.technicals <SYMBOL> [--sec-type stk|fut] [--json]` that
fetches bars + renders the setup panel via the existing pure `assess_setup` /
`render_panels`, with no connection write and no staging.

**Routing (updated `handle_telegram_text`).**

```
0. /start /help            -> help/menu
1. on_confirm (action tok) -> execute
2. order CONFIRM <token>   -> gate submit
3. classify_message:
     ORDER -> order agent (existing run_agent)  [+ ✅/✖️ buttons]
     ASK   -> quick_answer (instant) OR ask agent (fall-through, ~1 min)
```

Quick-answer questions are treated as **fast messages** (handled inline, like
confirms) so they return immediately; the ask-agent fall-through runs off the
poll loop under the existing concurrency semaphore, with a reworded ack
("🔎 Looking into that…").

### Files

- NEW `src/governor/comms/ask.py` — `classify_message`, `quick_answer`
- NEW `src/governor/technicals/__main__.py` (+ small `cli` helper) — read-only
  setup CLI
- EDIT `src/governor/comms/agent_runner.py` — add an `ask` runner (prompt +
  web tools) alongside the order runner; share `build_claude_argv` plumbing
- EDIT `src/governor/live/daemon.py` — routing, fast/slow classification, acks
- EDIT `docs/HANDBOOK.md` + `docs/FORCLAUDE.md` — the ask lane + grammar

### Tests

- NEW `tests/comms/test_ask.py` — `classify_message` (order vs ask, ambiguous),
  `quick_answer` for each recognized question + `None` fall-through, HTML output.
- NEW `tests/technicals/test_cli.py` — read-only setup CLI renders/serializes,
  **stages nothing** (asserts the staged-orders file is untouched).
- EDIT `tests/comms/test_agent_runner.py` — ask-runner argv (web tools present,
  write denies intact); graceful when `claude` absent.
- EDIT daemon routing tests — ASK → fast-path; ASK → agent fall-through; ORDER
  still → order agent.

---

## PR breakdown (small, test-backed, each ships SAFE)

1. **PR1 — formatting:** `format.py`, HTML house style, HTML-with-plain fallback.
   Pure, low-risk, immediately visible.
2. **PR2 — confirm UX:** namespaced callbacks, buttons on breaker actions,
   `editMessageText` in-place updates.
3. **PR3 — ask lane (tiers 1–2):** `classify_message` + deterministic fast-path
   off the daemon's live connection. No new subprocess, no network.
4. **PR4 — ask agent (tier 3):** read-only ask runner + web tools + the
   read-only `technicals` CLI, wired as the fall-through. Docs.

Order: PR1 → PR2 (quick wins) → PR3 → PR4 (the big one). `config/rules.yaml`
stays SAFE throughout; the single write chokepoint is untouched.

## Library question (recorded decision)

- **`ib_async` (IBKR):** keep it. Maintained `ib_insync` fork, async-native,
  wraps the full TWS API incl. `reqHistoricalNews` / `reqFundamentalData` /
  scanners we haven't tapped. The ceiling on news/fundamentals is IBKR data
  *entitlements*, not the library. Don't rebuild on raw `ibapi`.
- **Telegram client:** keep hand-rolled for now; extend it incrementally
  (`edit_message`, optional `setMyCommands` menu, the plain-text fallback).
  Revisit `python-telegram-bot` only if we commit to Mini Apps or stateful
  multi-turn conversation — a heavier dependency to place inside the
  safety-critical daemon process.

## Open questions for the operator

1. **News source:** web search (free, broad) vs. IBKR `reqHistoricalNews`
   (needs a news subscription, ticker-scoped). Default plan: web search.
2. **Ask-agent reach:** read-only account + technicals + web only (planned), or
   also let it read the research vault (`$VAULT_DIR`) for thesis continuity like
   the daily-summary skill does?
3. **Command menu:** add a Telegram `/` command menu (`setMyCommands`) for the
   common asks (/leverage, /today, /positions, /book) as one-tap shortcuts?

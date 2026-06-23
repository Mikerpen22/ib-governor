# Comprehensive `/pnl` panel — design

**Date:** 2026-06-23
**Status:** approved (brainstorming) → ready for implementation plan

## Goal

Replace the misleading `/pnl` quick-answer with an accurate panel showing
**Daily, Realized, and Unrealized** P&L, each in **dollars and as % of NAV**.

## Bug being fixed

`ask._fmt_pnl` currently renders `Net {realized_today + Σ position.unrealizedPNL}
so far today.` The Σ-unrealized term is *cumulative* (since each position's
entry), so with **zero trades today** it reported `Net -$30,447 so far today` —
implying a $30k loss today that never happened. Verified live on 2026-06-23
(account NAV $341,756, 0 fills today, open book −$30,447). The new `Daily` row
uses IBKR's true today-MTM instead.

## Decisions (from brainstorming)

- **Metrics:** Daily, Realized, Unrealized.
- **Units:** each shown in `$` (whole dollars, signed) **and** `%` of **current NAV**.
- **MTD/YTD:** out of scope — not exposed by the TWS socket API (confirmed: no
  ytd/mtd/period tag in `accountValues`/`accountSummary`). Deferred to a future
  IBKR Flex Web Service integration (see Out of scope).
- **Data source:** IBKR `reqPnL` (account-level) — fields `dailyPnL`,
  `realizedPnL`, `unrealizedPnL`. It is the *only* source carrying `dailyPnL`,
  and is the authoritative real-time P&L stream. (Note: `reqPnL.unrealizedPnL`
  can differ from `accountValues.UnrealizedPnL` due to different IBKR
  computations/timing; we deliberately use `reqPnL` for all three so the panel is
  internally consistent.)

## Layout

Telegram HTML; the figure table sits in a `<pre>` block so columns align.

```
🔴 P&L — today

Daily        -$3,637   -1.06%
Realized       +$265   +0.08%
Unrealized   -$9,099   -2.66%

% of NAV $341,756
```

- Header mood 🔴/🟢 from `dailyPnL`.
- `Daily` = today's account MTM; `Realized` = today's booked; `Unrealized` =
  total open P&L (cumulative — labeled plainly, **never** "today").
- The three are **distinct views, not addends** (Daily ≠ Realized + Unrealized);
  the layout never implies a sum.
- All `%` = amount ÷ current NAV; explicit `+`/`-` signs.

## Components & data flow

Flow: `/pnl` → `daemon._quick_or_unavailable("how am I doing")` →
`daemon._account_view()` → `daily.collect_account_view(self.ib)` →
`ask.quick_answer` → `ask._fmt_pnl`.

- **`live/daily.py` — new `fetch_account_pnl(ib, account) -> dict`**
  Returns `{"daily": float|None, "realized": float|None, "unrealized": float|None}`.
  Reads `ib.reqPnL(account)`; a field that is `None`/`nan`/non-finite → `None`
  (so an unsettled subscription never yields a phantom number). Pure-ish seam,
  unit-testable with a fake `ib` whose `reqPnL` returns a stub `PnL`.
- **`live/daily.py` — `collect_account_view`** adds `"pnl": fetch_account_pnl(ib,
  account)` to the returned dict (account from `config.live.account` or
  `ib.managedAccounts()[0]`). Existing `nav`/`fills`/`positions`/`realized_pnl_today`
  keys are unchanged, so `/today` and `/positions` are untouched.
- **`live/daemon.py`** subscribes `ib.reqPnL(account)` once after connect (in
  `run()`), so the daemon's quick-answer reads are warm and stay sub-second.
  Best-effort: a failure to subscribe must not block startup.
- **`comms/ask.py` — rewrite `_fmt_pnl(view)`** to render the panel from
  `view["pnl"]` + `view["nav"]`:
  - each present metric → signed `$` (existing `_usd`) and signed `%` of NAV;
  - `%` suppressed when `nav <= 0`;
  - a `None` field renders `n/a`;
  - if `view["pnl"]` is absent or all-`None`, fall back to the prior
    realized-from-fills figure (clearly labeled), so the answer degrades and
    never blanks.

## Calculation

- `daily_pct = daily / nav`, `realized_pct = realized / nav`,
  `unrealized_pct = unrealized / nav`; omit `%` when `nav <= 0`.
- Dollars via existing `_usd` (whole dollars, signed). Percent signed, 2 dp.

## Error handling

- `reqPnL` unavailable / field `nan`/non-finite → that field is `None` → `n/a`
  row; never a phantom number.
- `nav <= 0` → omit `%` (amounts still shown).
- Upstream read failure already handled (`_account_view` → "Can't read your
  account"). `_fmt_pnl` never raises into the daemon loop.

## Testing

- **Unit (pure, TDD):** `_fmt_pnl` from hand-built views — signs, `%` math,
  `nav<=0` (no `%`), per-field `None` → `n/a`, all-`None` → fallback. Update the
  existing `test_ask.py` P&L test to the new format.
- **Unit:** `fetch_account_pnl` with a fake `ib` — `nan`/non-finite → `None`;
  good values pass through.
- **Real-IBKR (integration, in `test_quick_commands_live.py`):**
  `fetch_account_pnl`'s daily/realized/unrealized match a fresh `reqPnL` read; the
  rendered `/pnl` panel's `Daily` dollar figure matches `reqPnL.dailyPnL` and each
  `%` equals amount/NAV.

## Out of scope / future

- **MTD/YTD** via the IBKR Flex Web Service (needs a Flex token + query in
  Account Management; EOD granularity, with today layered on via `dailyPnL`).
- Per-row 🟢/🔴 dots (trivial follow-up).
- `/positions` and `/today` renderers (unchanged).

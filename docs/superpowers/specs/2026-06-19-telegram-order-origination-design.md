# Telegram order origination — design

**Date:** 2026-06-19
**Status:** approved
**Branch:** `feat/telegram-order-origination`

## Goal

Let the operator initiate an order from Telegram — including in natural
language — without opening Claude Code or a terminal. The brake daemon already
polls Telegram and already owns the confirm machinery; this teaches it to turn
an inbound message into a *staged, confirm-gated* order that flows through the
existing pre-trade gate.

## Non-negotiable safety model (unchanged)

- **One write chokepoint.** Orders are placed only by `gate submit` →
  `ActionExecutor.place_order(s)` → `_guarded`. The daemon never imports a new
  write path; it shells to `python -m governor.gate submit`.
- **Two default-closed locks.** Placement requires `live.dry_run=false` AND
  `live.readonly=false`. Default config analyzes + replies but never places.
- **`BLOCK` cannot be overridden from the phone — enforced in code, not by
  trusting the agent** (see "BLOCK hardening").

## Roles

- **Daemon** — a thin Telegram ↔ gate bridge + confirm executor. Its brake
  loops (fills, briefings, staleness, circuit-breaker confirms) are untouched.
- **Gate CLI** (`gate analyze` / `gate submit`) — the deterministic brain-stem
  and the single write chokepoint. Reused as-is (plus the BLOCK hardening).
- **`claude -p` + `/pre-trade-*` skill** — the natural-language cortex. The
  strong brain already on the machine; reused, not duplicated.

## Message routing (`daemon._telegram_loop`, per inbound text)

1. **`CONFIRM <token>`** → try the in-memory `ConfirmTokenGate` first
   (circuit-breaker actions — existing behaviour). No match → run
   `python -m governor.gate submit --token <token>` as a subprocess. This
   enforces both locks and the `_guarded` chokepoint. Relay the result.
2. **Deterministic fast-path** — `parse_order_command(text)` recognises an
   exact compact command (`buy/sell qty SYMBOL [fut|stk] [@ price] [stop price]
   [sl price] [tp price]`). On a match the daemon runs `gate analyze <args>
   --json` and relays the rendered panels + token. Offline, instant, no model.
   Returns `None` for anything that isn't this grammar → fall through to (3).
   Raises `OrderParseError` for order-looking-but-malformed text → usage hint.
3. **Natural language** → `run_agent(text)` shells `claude -p "<text>"` with:
   - `--allowed-tools "Bash(python -m governor.gate analyze:*)" "Read"` plus
     read-only IBKR tools — **never** submit/place,
   - the `/pre-trade-*` skill available,
   - an appended system prompt: understand the order, run `gate analyze`, reply
     with the decision panel + the confirm token, or ask a clarifying question.
   The daemon relays the agent's stdout to the chat. Runs as an async subprocess
   with a timeout; fails soft; can never block or arm the brake.

`CONFIRM …` never collides with an order command — it doesn't start with
`buy`/`sell`, so the parser returns `None` and it is handled by (1).

## BLOCK hardening (deterministic refusal)

`gate analyze` stages the order even on a `BLOCK` verdict (so a deliberate
CLI `--override` submit is possible). To guarantee the phone cannot punch
through the brake regardless of what the agent says:

- `StagedOrderStore` records the **verdict** alongside the intent.
- `gate submit` **refuses** a `BLOCK`-staged order unless `--override` is
  explicitly passed. The daemon never passes `--override`.

So even if a `BLOCK` token reaches the chat, `CONFIRM <token>` cannot place it.

## Config

New `telegram_agent` section in `rules.yaml`:

```yaml
telegram_agent:
  enabled: true
  claude_bin: claude       # CLI to invoke for the NL path
  timeout_seconds: 120
```

Default-safe: the NL path is inert unless `enabled` is true AND the `claude`
CLI is present + authenticated (the operator's existing Claude Code login —
likely no new API key). If unavailable, the NL path is disabled with a
one-time warning; the deterministic fast-path and confirm still work.

No new Python dependency — no `anthropic` package, no embedded model, no new
secret stored in the daemon process.

## Components / files

- NEW `src/governor/comms/order_parser.py` — pure deterministic parser:
  `parse_order_command(text) -> OrderIntent | None`, `OrderParseError`.
- NEW `src/governor/comms/agent_runner.py` — `run_agent(text, cfg, *, runner=...)`
  building the `claude -p` argv and returning stdout; subprocess seam injected
  for tests.
- EDIT `src/governor/gate/staged.py` — store + return the verdict with the
  staged intent.
- EDIT `src/governor/gate/cli.py` — stage the verdict on analyze; add
  `--override` to submit and refuse a BLOCK-staged order without it.
- EDIT `src/governor/config.py` — `TelegramAgentConfig` + `telegram_agent` on
  `RulesConfig`.
- EDIT `src/governor/live/daemon.py` — routing, `_run_gate(args)` helper, order
  confirm via `gate submit`, NL relay via `run_agent`.
- EDIT `config/rules.yaml` — `telegram_agent` block (ships SAFE).
- DOCS — `docs/HANDBOOK.md` (flow, grammar, `claude` auth requirement) +
  a line in `docs/FORCLAUDE.md`.

## Testing

- `tests/comms/test_order_parser.py` — every grammar form, defaults, bracket vs
  stop-entry, stop-limit, malformed → `OrderParseError`, non-order → `None`.
- `tests/comms/test_agent_runner.py` — argv construction + stdout relay with a
  fake subprocess runner (no real `claude`, no network).
- `tests/gate/` — staged store carries the verdict; `gate submit` refuses a
  BLOCK-staged order without `--override`, allows it with.
- Daemon routing — unit tests with fakes for the three branches.
- Suite green offline; the live NL path skips when `claude` is absent.

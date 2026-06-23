# FOR CLAUDE — ib-governor, Explained

> A living explainer for `~/ib-governor`. Written in plain language so that
> anyone can pick this up cold. Updated as the project is built.
> **Status:** **All plans built & merged — the circuit-breaker (futures/equities/portfolio) and the pre-trade gate are complete (284 tests).** Ships SAFE: `dry_run` *and* `readonly` both default-closed; see the Arming Checklist before going live.

> **Scope note: options are descoped — futures + equities + portfolio only.**

---

## What this project actually is

A governor caps an engine's max RPM. **ib-governor** caps overtrading. It targets a common discipline failure — *the analysis is fine; giving back gains by overtrading after a win is what does the damage.* It's a documented behavioral pattern: a run of impulsive trades after a big win, oversized overnight leverage, churning the same name. The fix isn't better analysis — it's a mechanism that makes the disciplined plan actually get followed.

This project is that mechanism. Not a robo-advisor that tells you what to buy — that's a different problem. It's a **trustworthy, data-driven co-pilot that sits between the trader and a bad trade**, grounded in rules set in advance and (optionally) in the trader's own written-down learnings.

It is really **two machines wearing one coat**, because the two ways a disciplined plan breaks happen at opposite moments:

- **The circuit-breaker** — watches account *state* (P&L, trade count, leverage) and, when a line set in cold blood is crossed, *reacts*: it alerts and stages a corrective action. This is the backstop for impulsive GUI churn that's too fast to pre-screen.
- **The pre-trade gate** — for *deliberate* trades routed through a tool, it analyzes the trade *before* it executes (sizing, thesis↔instrument fit, optional notes-grounded learnings) and won't submit until the trader confirms.

Iron rule across both: **nothing ever touches the account without an explicit tap.** A bug can annoy you; it can never trade for you.

---

## Technical Architecture

Think of it as a nervous system with four layers:

```
 Telegram (2-way) + macOS  ──  COMMS    "you're in the house-money zone — lock out? [confirm]"
            │
 Python daemon (always on) ──  BRAIN-STEM (reflexes)   deterministic, NO LLM, ~arithmetic
            │ escalates only on a trip / at the gate
 Claude skills            ──  CORTEX (judgment)        deep per-asset analysts, on-demand
            │
 ibkr-cli + ib_async      ──  SENSES                   live positions, P&L, fills, orders
            │
 notes + rules.yaml       ──  MEMORY                   optional learnings + tunable thresholds
```

The load-bearing idea: **the reflexes are deliberately dumb.** The always-on part that decides "is a line crossed?" is plain Python you can read in one sitting — no LLM in the hot path. The LLM (cortex) only runs *after* a deterministic trip, to add reasoning ("here's *why* this matters"). Reflexes are arithmetic; judgment is reasoning; they live in separate boxes. That separation is the whole trust story — nobody keeps believing a brake that hallucinated a P&L number.

The rules are **segregated by asset class** (futures / equities / portfolio) because each class fails differently: futures = churn + overnight leverage; equities = concentration + chasing.

---

## Codebase Structure (what exists today)

Plan 1 built the **deterministic core** — pure logic, zero I/O, so it's provable at memory speed:

```
src/governor/
  model.py        # immutable types: StateSnapshot, Trip, AssetClass, Severity, ActionType
  config.py       # pydantic-validated thresholds; loads config/rules.yaml
  rules/
    futures.py    # the six futures safeguards, each a pure (snapshot, cfg) -> Trip|None
    engine.py     # evaluate(snapshot, config) -> list[Trip]   ← the one thing later plans call
config/rules.yaml # every threshold, with sensible defaults, tunable
tests/            # 37 tests, 100% coverage, incl. a historical-replay acceptance test
```

**The public surface later plans build on:** `evaluate(snapshot, config)`, `StateSnapshot`, `Trip`, `RulesConfig`. That's it. Everything downstream (the daemon, the comms, the gate) consumes these and nothing else.

**Built since (each has a section below):** Plan 2 (live wiring — daemon + snapshot builder), Plan 3 (Telegram + staged actions), Plan 4 (equities/portfolio rules + the rule catalog), Plan 5 (the pre-trade gate + analyst skills). The gate added `src/governor/gate/` (intent · analysis · staged · runner · cli) and `skills/pre-trade*`.

---

## Technologies Used (and why)

- **Python 3.12** — matches the `ibkr-cli` ecosystem this builds on; the tooling is all there already.
- **`ib_async`** — the library `ibkr-cli` already wraps; it talks to TWS and (verified) natively supports futures, equities, what-if previews. We extend it rather than reinvent.
- **`pydantic` v2** — validates `rules.yaml` on load, so a fat-fingered threshold fails loudly instead of silently disabling a safeguard.
- **`pytest` + `pytest-cov`** — TDD throughout; the historical replay is itself a test.
- **Telegram Bot API** — chosen over WhatsApp because two-way works over plain long-polling (no public webhook, no message templates, no ToS/ban risk on the user's phone number).
- **macOS `launchd`** — keeps the daemon alive during market hours (TWS is local, so the daemon is local too).

---

## Technical Decisions (the "why" behind the choices)

- **No LLM in the reflex loop.** Trust requires the core be deterministic and auditable. The LLM adds judgment after a trip, never decides the trip.
- **Event-driven monitoring + 3 daily briefings**, not fixed polling. The poll is essentially free (local socket, no tokens), and a brake has to sample *faster than the behavior it's braking* — checking 3×/day can't catch a spiral that starts and ends between checks. So: react to fills in real time; brief at open+1h / noon / close.
- **Confirm-required for every action.** Staged, never automatic. Stop-trading actions (cancel orders, "platform OFF") would be safe to automate, but ib-governor deliberately keeps a tap on everything; position-changing actions (trim/flatten) always need it.
- **Fail loud.** If the daemon can't read state, it screams "BRAKE BLIND" — it never treats blindness as all-clear.
- **Extend, don't reinvent.** `ibkr-cli`'s order layer is stock-only, but TWS/`ib_async` support futures natively — so we add `Future` builders, not a new API client.
- **Deliberate trades through the gate; impulsive churn backstopped by the breaker.** You can't run an LLM before each of dozens of rapid scalps, and that's fine — those are exactly what the breaker exists for.

---

## Lessons Learned

The richest section, and the reason this doc exists.

- **Probing the live API caught two design bugs that mocks never would have.** (1) IBKR's account `RealizedPnL` is cumulative and account-wide — *not* "today" and *not* per-asset — so the most important rule (house-money lockout, which needs realized *futures* P&L *today*) must derive it from execution fills. A mock would have happily returned a number the real API can't produce. (2) `ibkr-cli`'s order preview is stock-only — discovered from the source, not at 2am mid-build. *Lesson: assumptions about an external system are exactly what unit tests can't catch; hit the real thing early.*
- **"100% coverage" ≠ "boundaries tested."** The rules hit 100% branch coverage while the most bug-prone inputs — the exact thresholds (`-1500.00` vs `-1500.01`) — went untested, because a test at `-1500.01` covers the same branch. For a brake where "fires one trade too early/late" is the failure mode, boundary tests matter more than the coverage number.
- **A passing test isn't an honest test.** The historical replay had to be audited for circularity (does it run the *real* engine?), a doctored config (does it use the *shipped* defaults?), and faithful inputs. It passed all three — which is what lets the project claim it would genuinely have caught the modeled blow-up.
- **The final whole-branch review caught a landmine in the *plan*, not the code:** the plan's `git add -A` cleanup step would have committed pytest's `.coverage` artifact, because it wasn't gitignored. Per-task reviews see their task; only fresh eyes on the whole thing catch that.
- **Assert the *consequence*, not just the firing.** A replay test that proved "the lockout rule tripped" but not "it tripped as HARD with the 48h-lockout action" would let a future regression silently downgrade a hard stop to a soft warning — and that action field is exactly what the comms layer will read.
- **The brake's dumbness is a feature.** Every instinct says "make it smart." The opposite is correct: the part you must trust in the heat of a bad day should be the part that can't surprise you.

---

---

## Plan 2 — Live Wiring (built & merged)

`src/governor/live/` connects the brain to the live account: `LiveConfig` (connection + tunable runtime), a **pure `build_snapshot()`** that turns live IBKR objects into a `StateSnapshot` (derivation logic stays unit-testable with fakes), a read-only `BrakeConnection`, and a `BrakeDaemon` that recomputes on every futures fill (`commissionReportEvent`) and on a 3×/day briefing loop. **Read-only and dry-run** — it logs trips, never acts; `dry_run=False` raises until Plan 3. `scripts/pin_contract.py` verifies the data contract against the live account (it passed). Run it: `python -m governor.live.daemon`.

### More lessons (Plan 2)

- **Realized P&L isn't on the fill event.** `execDetailsEvent` fires with `realizedPNL` still empty; it's populated on the *later* `commissionReportEvent`. Key the recompute off the wrong event and the house-money lockout silently reads zeros and never fires. Only reading the `ib_async` source revealed this.
- **A multi-currency account hides a NAV bug.** `accountValues()` reports `NetLiquidation` in *both* USD and any foreign-holding currencies; "last wins" in a dict comprehension could silently pick the wrong one — and since NAV is the denominator for every %-rule, a wrong NAV mis-scales the whole brake. Fixed with a `currency == "USD"` filter. A bug that exists *because* the account holds foreign instruments.
- **Running the live pin caught a stale constant the tests couldn't.** The MNQ-equivalent reference defaulted to a guessed value; the live pin showed MNQ at its true notional (front-month price × multiplier). A unit test asserting "6 contracts → 6.0 equiv" passes against *any* reference value — only real market data exposes the staleness. Exactly why you probe the live API.
- **A review is to be verified, not obeyed.** A reviewer suggested swapping `asyncio.ensure_future` for `create_task` — which would have *broken* startup, since `create_task` needs a running loop that doesn't exist yet when the task is scheduled. Checking the suggestion before applying it avoided introducing the bug it claimed to fix.

---

---

## Plan 3 — Comms & Confirm-Gated Actions (built & merged)

The brake now speaks and — once armed — acts. `src/governor/comms/` (async Telegram client + macOS notifier) and `src/governor/actions/` (`ConfirmTokenGate`, `LockoutStore`, `ActionExecutor`) plug into a rewritten `BrakeDaemon.handle()`: a trip → loud alert (Telegram + macOS) → if it carries an action, a staged confirm request → you reply `CONFIRM <token>` → the action runs through the chokepoint. Actions: cancel-all-orders, 48h/EOD lockout (cancel + persistent flag + a *violation witness* that calls you out if you trade during your own lockout), and trim-futures. Per your choice, "platform OFF" = cancel + alert, no TWS-kill.

**The one safety invariant, restated:** exactly one method (`ActionExecutor._guarded`) can touch the account, and it returns early — logging intent — when `dry_run` is set. A whole-repo grep confirms no write call exists anywhere else. The path from a trip to execution is single and gated by a single-use, expiring, chat-authed token.

### More lessons (Plan 3)

- **A flag you assume is a guardrail may not be one.** `readonly=True` reads like a safety interlock — it isn't, in `ib_async` (it only skips a startup fetch; `placeOrder` sends regardless). The real interlock had to be *built*: one dry-run-gated chokepoint in our code, plus TWS's server-side "Read-Only API" setting as a backstop. Verify your guardrails are load-bearing before you lean on them.
- **Test the seam, not just the parts.** Every safety unit (token gate, executor, dry-run) was tested — but the composition (`handle → confirm → execute`) was only safe-by-inspection until the final review caught it. For money-touching code, the place the parts compose is exactly what must be pinned by a test.
- **Bugs hide in the malformed-input seam.** The token reply split on literal spaces (a tab would've silently dropped a valid confirm); the lockout file crashed on a missing key. Both passed the happy path; both were caught by asking "what if the input is weird?"

### ⚠️ Arming checklist (before ever setting `live.dry_run: false`)

The brake ships **safe**: `dry_run: true` (confirmed actions are logged, never executed). To arm it on the live account, in order:
1. ✅ **Telegram — configured & validated** (your bot, e.g. @your_bot; `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`; getMe + a test message confirmed). Still worth one `python -m governor.live.daemon` run to watch the live alert → `CONFIRM <token>` → "DRY-RUN would execute…" loop end-to-end.
2. ✅ **Trim idempotency — DONE.** `dedup_key` gives one-outstanding-token-per-action (a re-trip invalidates the prior token), and a post-execute cooldown (`live.action_cooldown_seconds`, default 300s, arms on *success* only) suppresses re-staging a just-executed action — both over-trim vectors closed.
3. Turn TWS's **"Read-Only API" setting OFF** (the server-side backstop must lift for actions to go through), set `live.readonly: false` (the second app-level lock) — and only then set `live.dry_run: false`.
4. ✅ **`mnq_notional_usd` — DONE (dynamic fetch).** The overnight-contracts count now normalizes against the LIVE MNQ notional (front-month price × multiplier), fail-soft to the config value if TWS data is unavailable — no stale constant to keep re-tuning.

---

## Plan 4 — Equities & Portfolio Rules + the Rule Catalog (built & merged)

The breaker grew its other two asset classes. **Equities:** single-name concentration (>15% NAV), sector concentration (>25%), re-trade churn (same name >2×/week), and add-into-drawdown (averaging down a loser while the book is underwater). **Portfolio (cross-asset):** margin cushion, gross leverage, drawdown moratorium. Sector labels come from `reqContractDetails().industry` (no Reuters subscription needed); an unknown sector buckets to `"unknown"` and still surfaces, rather than silently passing.

This is also when the rules earned a **catalog.** With 13 rules scattered as pure functions across three modules, nobody could see the whole safeguard surface at a glance, and a hand-written summary would rot the first time someone added a rule. So `src/governor/rules/catalog.py` is *data* — one `RuleSpec` per rule (id, asset class, severity, config key, one-liner) — `docs/RULES.md` is generated from it, and a test asserts the catalog covers *exactly* the engine's registries.

### Lesson (Plan 4 / catalog)
- **Bind the doc to the code or it rots.** The catalog isn't a parallel copy of the rules; it's a projection the engine is checked against, so drift becomes a failing test rather than a silent lie. The naming convention (`rule_id == "<section>.<function>"`) was already there — the catalog just made it *enforced* instead of merely *observed*.

---

## Plan 5 — The Pre-Trade Gate (built & merged)

The proactive half — and the realization of the original goal: *before any deliberate trade, analyze it against the rules and past learnings.* For a deliberate order the flow is:

```
intent ("buy 50 ORCL @145")
  → build the order   → ib.whatIfOrder (margin preview, READ-ONLY)
  → hypothetical post-trade snapshot   → run the EXISTING rule engine on it
  → + per-trade sizing (≤1.5% NAV) + active-lockout honor
  → GO / CAUTION / BLOCK   + a single-use staged token
  → you confirm   → submit through the one guarded write chokepoint
```

It's split along the trust boundary. The **deterministic gate** (`src/governor/gate/`) answers the *checkable* questions — margin, sizing, would-a-rule-trip, is-there-a-lockout — and emits them as JSON. The **LLM analyst skills** (`/pre-trade` → `pre-trade-equities` / `pre-trade-futures`) answer the *judgment* question: given those facts *and* (optionally) the trader's notes about this name, is this a GO? The LLM never computes a number it could get wrong; it reasons over numbers the deterministic core already nailed down. Order types: market / limit / stop / stop-limit.

### Lessons (Plan 5 — the richest yet)

- **A test can enshrine a bug.** `test_corrupt_file_returns_none` looked like honest coverage; it actually *locked in* a fail-**open** — an unreadable lockout file read as "no lockout," i.e. the brake waving you through. Tests encode intent, and when the intent is subtly wrong, green gives false confidence. The fix inverted the test: a present-but-unreadable lockout now fails **closed** (assume locked + scream). For a safety interlock, "fail open vs fail closed" is *the* question — it must over-stop, never wave you through on uncertainty.
- **"Faithful refactor" and "safe behavior" are different audits.** Two review agents read the same line and disagreed — one called it "fail-safe," the other "fail-open." Both were right: the refactor *was* faithful to the old behavior, and the old behavior *was* unsafe. A clean refactor can faithfully preserve a latent hazard; you need the correctness lens *and* the safety lens.
- **Enforce the confirm across process boundaries.** A skill runs discrete CLI commands, so the "no submit without analysis + confirm" invariant can't live in memory. Hence two verbs: `analyze` mints and *persists* a single-use, expiring token; `submit` consumes it. Even with an LLM driving, the deterministic gate stays the sole path to the exchange — there's no way to reach `placeOrder` without a fresh analysis and your tap.
- **Reuse the rules; don't grow a second source of truth.** Concentration is already a *rule*, and the gate runs the real engine on a *hypothetical post-trade snapshot* — so it gets concentration/leverage/drawdown checks for free. The only genuinely gate-only check is per-trade **sizing** (a property of the *order*, not the resulting *state* — no rule expresses it). A parallel concentration function would have been a second truth that could disagree with the first.
- **Two locks, both default-closed.** Arming needs *both* `dry_run: false` (the app gate) *and* `readonly: false` (the IBKR connection itself rejects writes) — defense in depth. But it bites: `placeOrder` is fire-and-forget, so an armed-but-still-readonly submit would report "placed" while TWS silently rejects it. The gate now warns on that exact misconfig.
- **Real captured fixtures catch what mocks can't.** `scripts/capture_fixtures.py` freezes real IBKR response shapes (account-scrubbed) into replayable fixtures — guarding against e.g. the IBKR "unset" sentinel (`1.79e308`) leaking into a realized-P&L sum, a shape a hand-written mock would never include.
- **A good implementer doesn't stop at "tests pass."** Several implementer subagents self-reviewed past their first "done," found their own nits (an unguarded `IndexError`, missing boundary tests), and shipped the fix — which the controller then has to reconcile (a late self-review commit can race a review already dispatched against the earlier one).

---

*ib-governor is feature-complete: a deterministic core (rules + engine + catalog), a live daemon (event-driven + briefings + confirm-gated actions), a pre-trade gate (deterministic facts + optional notes-grounded LLM judgment), and a setup read that adds entry-quality awareness to the gate — all dry-run-safe until you run the Arming checklist above. Known follow-up: `cli.build_current_snapshot` and `daemon.build` are a justified read-only near-duplicate worth DRYing into one shared `build_snapshot_readonly` helper.*

---

## Phase 6 — Setup Read: The Gate Learns to Look at the Chart

### The gap the gate had

After Plan 5 the pre-trade gate was nearly complete — it knew whether you could *afford* a trade (margin, sizing, concentration, rules) but not whether the entry was any *good*. You could pass through a trade that was inside every risk limit while buying extended into a resistance high, or entering counter-trend in a trending market. The gate was risk-complete but setup-blind.

The fix is the setup read: one more piece of data the gate gathers before it hands you the verdict, quietly, over the same connection it already has open.

### How it works

The gate calls `reqHistoricalData` on the IBKR socket it's already connected to — one request, fail-soft (if the bars don't come back, the setup is marked `available: False` and the gate proceeds without it; it never blocks on missing data). The bars go through a small stack of pure Python:

- **Equities:** Minervini's 7-criterion Stage-2 checklist (MA stack, MA200 slope, 52-week position, range ratio), plus VCP contraction detection (pivot level, distance-from-pivot band, last contraction grade, volume dry-up). The result is `confirmed` (6–7/7), `candidate` (4–5), or `none` (≤3), plus a VCP band: `actionable`, `extended`, or `too_late`.

- **Futures:** a four-factor structural read on the continuous contract — trend alignment (price vs 20/50/200 MAs), volatility regime (ATR percentile over the prior 100 bars), location/extension (distance from the 20-day high/low range), and momentum (RSI/ROC). Counter-trend, chasing, or elevated-vol setups get flagged.

Both paths land in `GateFacts.setup` (a `SetupAssessment` frozen dataclass). `decide()` reads it: if the setup is poor, it escalates the verdict to **CAUTION** — but never to BLOCK, and never downgrades a real BLOCK that a rule or lockout already earned. The setup is a quality signal, not a veto.

### Where the rendering lives

There's a deliberate split at the trust boundary. The gate's Python renders the *factual* panels — 📋 ORDER, 💰 RISK & SIZING, 📈 SETUP — as a pre-formatted string (`panels`) in the JSON response. The analyst skill owns the *judgment* layer: the banner (final verdict after vault escalation), the 🧭 VERDICT paragraph, the 📓 VAULT section, and the confirm line. Python renders what it can prove from the numbers; the LLM reasons over those numbers and the trader's own notes.

The reason for the split: the fact panels are computed from real IBKR bars and are the same no matter who reads them. The verdict can change — your vault might contain a note saying "this name is an exception" or "I've chased this setup twice and lost both times." That's the LLM's job, not the gate's.

### Lessons from this phase

- **Fail-soft is the only option for a brake.** A setup that can't be assessed (TWS gave no bars, symbol has too little history) must *not* block the trade — a risk gate that refuses to let you close a position because it can't read a chart would be a hazard, not a help. The rule: missing data produces an `available: False` assessment and the rest of the gate runs as if setup didn't exist.
- **The ATR percentile edge case is worth documenting.** In a dead-flat regime where every bar has the same range, the current bar's ATR ranks at the 100th percentile of itself — the percentile reads "elevated" even though there's no actual volatility expansion. It's fail-safe (CAUTION, never BLOCK), but it will fire in quiet markets and the operator should know why. See the HANDBOOK for the tuning knob.
- **The MA200-rising criterion needs ~221 bars, not 200.** "MA200 rising" means the 200-bar MA itself has a positive slope over a 20-bar window — so you need 200 bars for the MA *plus* 20 more to compare the slope endpoint to its starting point. With the default `history_duration: "1 Y"` (~252 bars) you're fine; only names with exactly 200–220 bars of history cap at 6/7 on this criterion. It's production-moot but worth knowing if you ever see an unexpected `candidate` on a name that looks clean.

## Phase 7 — Order from Telegram (built & merged)

The daemon already polled Telegram (for `CONFIRM <token>`) and already owned the
write chokepoint. Phase 7 lets you *start* an order from the chat — including in
plain language — without opening a terminal.

### The shape: a thin bridge, one reused brain

The temptation was to embed a small LLM in the daemon to parse messages. We
didn't. The project's whole architecture is "deterministic core, intelligence on
top via skills" — so the natural-language understanding stays in the skill we
already have. `daemon.handle_telegram_text` routes each inbound message:

1. **circuit-breaker action confirm** — an in-memory `ConfirmTokenGate` token
   (unchanged Plan-3 path),
2. **order confirm** — `CONFIRM <token>` → `python -m governor.gate submit`
   subprocess (the single guarded write path),
3. **anything else** — a headless `claude -p` agent (`comms/agent_runner.py`)
   with the `/pre-trade-*` skill, confined to the gate's `analyze` subcommand +
   `Read`. The agent interprets, analyzes, stages, and replies a token.

So the daemon stays dumb about *understanding* and *placing*: it relays to the
agent and executes confirmed tokens through the gate CLI. No new Python
dependency, no embedded model, no new secret — the agent reuses the existing
Claude Code login, exactly like the daily-summary launchd job.

### The BLOCK guarantee is in code, not in trust

`gate analyze` stages even a BLOCK (so a deliberate CLI `--override` is possible).
If the only thing stopping a BLOCKed phone order were the agent choosing not to
print the token, that'd be trust, not a guarantee. So the staged record now
carries the **verdict** (`StagedOrderStore`), and `gate submit` **refuses** a
BLOCK-staged order unless `--override` is passed — which the daemon's confirm
path never does. The phone cannot punch through the brake even if a token leaks.

### Lessons

- **`--allowed-tools` is additive, not a sandbox.** The first cut confined the
  agent with `--allowed-tools "...analyze..." "Read"` and called it "structurally
  unable to place an order." It wasn't: the spawned CLI also loads the operator's
  global `~/.claude/settings.json`, which allows `Bash(python *)` in auto mode —
  so the agent could have run `gate submit --override`. The real confinement is
  **deny rules** (deny wins over any allow), **strict MCP isolation**
  (`--strict-mcp-config --mcp-config '{"mcpServers":{}}'` so the ibkr-tws
  `place_order` MCP can't load), and **`--permission-mode default`** (not the
  global `auto`). A residual remains — the global `Bash(python *)` allow still
  permits arbitrary python — closed only by tightening that global allow-list.
  Lesson: a money-path sandbox must be verified against the *effective* merged
  permission set, not the flags you passed.
- **Reuse the strong brain before adding a weak one.** A Haiku-in-the-daemon would
  have duplicated the pre-trade skill's intelligence and added an online
  dependency + secret to the safety-critical process. Delegating to `claude -p`
  kept one brain and zero new deps.
- **Move the safety invariant to where it's deterministic.** "The phone can't
  override BLOCK" became real only once `submit` enforced it from persisted state
  — not from the agent's good behaviour.
- **A subprocess seam keeps the async daemon testable.** `run_agent` and
  `_gate_submit` are module-level seams; the routing tests inject fakes and never
  spawn `claude` or a gate process.

## Phase 8 — the chat becomes a cockpit (Q&A + UX)

Phase 7 made the chat *place* orders. Phase 8 makes it *answer* — and cleans up
the interaction surface — without weakening the brake.

**Three tiers, fastest first.** `handle_telegram_text` now splits a non-confirm
message with `classify_message` (ORDER vs ASK — a cheap heuristic; a misread is
harmless because both lanes are read-only):

1. **Deterministic fast-path** (`comms/ask.py::quick_answer`, sub-second). The
   daemon already holds a live `ib` (client_id 4) with account values, portfolio
   and fills streamed by `ib_async`. Leverage / cushion / P&L / today / positions
   are answered straight from `live.daily.collect_account_view` — the
   connection-cheap subset of the daily collector (everything *except* the slow
   market backdrop). No subprocess, no new socket. Works even with the order
   agent disabled.
2. **Slash shortcuts** (`setMyCommands`): `/leverage /pnl /positions /today
   /cushion` map to the same quick answers as one-tap buttons.
3. **The ask agent** (`run_ask_agent`, ~a minute) for open-ended questions —
   "analyze my book", NL technicals, news. A second `claude -p` lane with the
   web tools added (`WebSearch`/`WebFetch`) and a **read-only** prompt; it can run
   `governor.live.daily --json`, the new read-only `governor.technicals` CLI (a
   setup read that **stages nothing**, unlike `gate analyze`), and read the
   research vault. Same deny rules + dry-run sandbox as the order agent.

**Formatting + confirm UX.** `comms/format.py` gives a Telegram-HTML house style,
threaded through the daemon's messages; `TelegramClient.send` falls back to plain
text on a markup error so a formatting bug can never drop a brake alert. Confirm
buttons now ride on *every* confirmable thing (orders **and** circuit-breaker
actions, via namespaced callback data `confirm:` / `action:` / `cancel:`), and a
tap **edits the card in place** (`editMessageText`) into its outcome instead of
spawning a second message.

### Lessons

- **The warm connection is the feature.** The instant-answer tier exists only
  because the always-on daemon already holds the account streamed; the trick was
  carving the *cheap* read (`collect_account_view`) away from the slow backdrop,
  not adding a new data path.
- **A read-only lane still earns its own dedicated CLI.** `gate analyze` stages a
  token as a side effect — wrong for a mere question — so NL technicals get
  `governor.technicals`, which qualifies + fetches + assesses and writes nothing.
- **Two read-only lanes make classification low-stakes.** Because neither the
  order lane (stages only) nor the ask lane (reads only) can move money, the
  ORDER/ASK split can stay a tunable heuristic instead of a safety gate.

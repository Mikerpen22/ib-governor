"""Headless `claude -p` bridge: a natural-language Telegram message -> a staged,
confirm-gated order, produced by the *existing* pre-trade skill.

The daemon hands a non-confirm message to `run_agent`, which shells out to the
`claude` CLI in print mode with:

  - the pre-trade skill available (it resolves from the project automatically),
  - tools confined to read-only analysis: an `analyze`-only allow-list, PLUS deny
    rules for every write path (`gate submit`, `ibkr_cli`, the MCP `place_order`)
    because `--allowed-tools` is additive to the operator's global settings —
    deny wins over those allows — PLUS strict MCP isolation so no inherited
    server loads. The agent can *propose and stage* an order (and reply a confirm
    token); placement still happens only when the operator replies
    `CONFIRM <token>`, through the gate's single guarded chokepoint under the two
    locks, which independently refuses a BLOCK-staged order.

Residual: the global allow `Bash(python *)` still lets the agent run *arbitrary*
python; the deny rules close the known governor/ibkr_cli write commands, but a
hand-rolled raw-API script is not pattern-blockable here. Tightening the
operator's global allow-list (or a dedicated config dir) closes that remainder.

Everything is injected (the subprocess runner and the binary-presence check) so
the unit tests never touch a real `claude` or the network. Failures degrade
gracefully — `run_agent` always returns a chat-ready string and never raises, so
a flaky agent can never block or destabilise the brake daemon.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Awaitable, Callable

from .proc import run_capture

log = logging.getLogger("governor.agent_runner")

# We auto-approve the bare `Bash` tool (plus `Read`). Scoped `Bash(<prefix>:*)`
# rules are NOT reliably honored by Claude Code in a headless launchd context
# (they fall through to "ask", defeating the point), whereas bare `Bash` runs —
# this is the same allow form the daily-summary launchd job uses successfully.
#
# We do NOT lean on the allow/deny matcher for the safety guarantee, because if
# scoped *allow* matching is unreliable then scoped *deny* matching is too. The
# real guarantee is STRUCTURAL: the agent subprocess runs with
# GOVERNOR_AGENT_SANDBOX=1 (see _agent_env), which forces the gate into dry-run,
# so any `gate submit` the agent ran would place NOTHING. Deny rules remain as
# defense-in-depth; --strict-mcp-config (a hard flag, always honored) keeps the
# ibkr-tws `place_order` MCP from loading at all.
_ALLOW_TOOLS = ["Bash", "Read"]

_DENY_TOOLS = [
    "Bash(python -m governor.gate submit:*)",    # the order chokepoint (bonus; dry-run is the real block)
    "Bash(python -m governor.gate submit *)",
    "Bash(python -m ibkr_cli:*)",                # the other write CLI on this machine
    "Bash(python -m ibkr_cli *)",
    "mcp__ibkr-tws__place_order",                # belt-and-suspenders if any MCP slips in
]

_SYSTEM_PROMPT = (
    "You are the pre-trade gate for a Telegram trading-discipline bot. The user "
    "message is a request to place an order. Interpret it into a concrete order "
    "(action, quantity, symbol, sec-type, order type, prices), then run "
    "`python -m governor.gate analyze ...` to evaluate it. Reply with a SHORT, "
    "chat-friendly summary: what you read the order as, the verdict and key "
    "reasons, and — only if the verdict is GO or CAUTION — the exact line "
    "`Reply CONFIRM <token>` using the token the gate printed. If the verdict is "
    "BLOCK, state the block reasons and do NOT provide a confirm token. If the "
    "request is ambiguous or not an order, ask one brief clarifying question. "
    "You cannot place orders yourself; only the user's CONFIRM does that."
)

# The ask lane is READ-ONLY: it answers questions, it never proposes/stages an
# order. It gets the web tools (news) on top of Bash/Read; the same deny rules +
# the dry-run sandbox still apply as defense-in-depth.
_ASK_ALLOW_TOOLS = ["Bash", "Read", "WebSearch", "WebFetch"]

_ASK_SYSTEM_PROMPT = (
    "You answer the operator's READ-ONLY questions about their IBKR account and "
    "the market, for a Telegram trading-discipline bot. You can:\n"
    "- run `python -m governor.live.daily --json` for today's positions, P&L, "
    "leverage and margin cushion;\n"
    "- run `python -m governor.technicals <SYMBOL> [--sec-type stk|fut] --json` "
    "for a read-only Stage-2/VCP (equity) or trend/vol/location/momentum (futures) "
    "setup read of a symbol;\n"
    "- read the operator's research vault under $VAULT_DIR (when set) for prior "
    "theses and recaps;\n"
    "- search the web for current news and headlines.\n"
    "NEVER place or stage an order: you have no trading authority and must NOT run "
    "`gate analyze` or `gate submit` (they stage/place). Answer the question only. "
    "Reply concise and skimmable in Telegram HTML (<b>, <i>, <code>) — no markdown, "
    "no separator rules."
)

# A runner takes the argv + a timeout and returns (returncode, stdout, stderr).
Runner = Callable[[list[str], float], Awaitable[tuple[int, str, str]]]


def _agent_env() -> dict:
    """Environment for the agent subprocess: inherit the daemon's env (PATH/HOME
    so `claude` and the venv `python` resolve) and set the sandbox flag so any
    gate the agent runs is forced dry-run — the structural can't-place guarantee.
    """
    return {**os.environ, "GOVERNOR_AGENT_SANDBOX": "1"}


async def _default_runner(argv: list[str], timeout: float) -> tuple[int, str, str]:
    return await run_capture(argv, timeout, env=_agent_env())

# Normie-friendly: outcome + next step, no CLI/PATH internals (those go to logs).
_DISABLED_MSG = (
    "⚠️ Order-by-text is turned off right now. Your safety brake is still running normally."
)
_UNAVAILABLE_MSG = (
    "⚠️ Order-by-text is temporarily unavailable. Your safety brake is still running normally."
)
_FAILED_MSG = (
    "😕 I couldn't finish checking that one — mind sending it again? "
    "(Your account is untouched.)"
)
_EMPTY_MSG = "🤔 I didn't catch an order in that. Try e.g. 'buy 10 oracle', or send /help."

# Ask-lane variants of the graceful gates.
_ASK_DISABLED_MSG = (
    "⚠️ Q&A by text is turned off right now. Your safety brake is still running normally."
)
_ASK_UNAVAILABLE_MSG = (
    "⚠️ Q&A is temporarily unavailable. Your safety brake is still running normally."
)
_ASK_FAILED_MSG = (
    "😕 I couldn't dig into that one — mind asking again? (Your account is untouched.)"
)
_ASK_EMPTY_MSG = (
    "🤔 Not sure how to answer that. Try your positions, leverage, P&L, or a symbol's setup."
)


def _build_argv(text: str, cfg, allow_tools: list[str], system_prompt: str) -> list[str]:
    """The shared headless `claude -p` argv. Confinement: bare allow-list (the form
    that runs headless), strict MCP isolation (`place_order` MCP can't load), deny
    rules as defense-in-depth. The load-bearing guarantee is NOT here — it's the
    GOVERNOR_AGENT_SANDBOX dry-run env applied by the runner (see _agent_env)."""
    return [
        cfg.claude_bin,
        "-p",
        text,
        "--permission-mode",
        "default",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers": {}}',
        "--allowed-tools",
        *allow_tools,
        "--disallowed-tools",
        *_DENY_TOOLS,
        "--append-system-prompt",
        system_prompt,
    ]


def build_claude_argv(text: str, cfg) -> list[str]:
    """Argv for the ORDER agent (propose + stage, read-only analysis tools)."""
    return _build_argv(text, cfg, _ALLOW_TOOLS, _SYSTEM_PROMPT)


def build_ask_argv(text: str, cfg) -> list[str]:
    """Argv for the read-only ASK agent (Q&A; web/news tools, never stages)."""
    return _build_argv(text, cfg, _ASK_ALLOW_TOOLS, _ASK_SYSTEM_PROMPT)


async def _invoke_claude(argv: list[str], cfg, *, runner: Runner,
                         which: Callable[[str], str | None],
                         disabled: str, unavailable: str, failed: str, empty: str) -> str:
    """Shared run/gate logic for the order + ask agents. Never raises."""
    if not cfg.enabled:
        return disabled
    if which(cfg.claude_bin) is None:
        log.warning("telegram_agent: %r not found on PATH — disabled", cfg.claude_bin)
        return unavailable
    try:
        rc, stdout, stderr = await runner(argv, cfg.timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — a flaky agent must never crash the brake
        log.error("telegram_agent run failed: %s", exc)
        return failed
    if rc != 0:
        log.error("telegram_agent exited %s: %s", rc, stderr.strip()[:500])
        return failed
    reply = stdout.strip()
    return reply if reply else empty


async def run_agent(
    text: str,
    cfg,
    *,
    runner: Runner = _default_runner,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Run the headless ORDER agent on *text*; return a chat-ready reply. Never raises."""
    return await _invoke_claude(
        build_claude_argv(text, cfg), cfg, runner=runner, which=which,
        disabled=_DISABLED_MSG, unavailable=_UNAVAILABLE_MSG,
        failed=_FAILED_MSG, empty=_EMPTY_MSG)


async def run_ask_agent(
    text: str,
    cfg,
    *,
    runner: Runner = _default_runner,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Run the read-only ASK agent on *text*; return a chat-ready reply. Never raises."""
    return await _invoke_claude(
        build_ask_argv(text, cfg), cfg, runner=runner, which=which,
        disabled=_ASK_DISABLED_MSG, unavailable=_ASK_UNAVAILABLE_MSG,
        failed=_ASK_FAILED_MSG, empty=_ASK_EMPTY_MSG)

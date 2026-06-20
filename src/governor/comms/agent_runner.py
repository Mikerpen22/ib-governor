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
import shutil
from typing import Awaitable, Callable

from .proc import run_capture

log = logging.getLogger("governor.agent_runner")

# The ONLY Bash command the agent may run — the read-only gate analysis, scoped so
# it cannot reach `gate submit` or any other command. Plus Read for vault context.
_ANALYZE_TOOL = "Bash(python -m governor.gate analyze:*)"

# CRITICAL: `--allowed-tools` is ADDITIVE to the operator's global
# ~/.claude/settings.json, which typically allows `Bash(python *)` in auto mode.
# An allow-list alone therefore does NOT confine the agent — it could run
# `python -m governor.gate submit --override` or an ibkr_cli write. We close the
# write paths with DENY rules (deny wins over any allow) and refuse to load the
# inherited MCP servers (so the ibkr-tws `place_order` tool is unavailable).
_DENY_TOOLS = [
    "Bash(python -m governor.gate submit:*)",   # the order chokepoint — agent must never call it
    "Bash(python3 -m governor.gate submit:*)",
    "Bash(python -m ibkr_cli:*)",               # the other write CLI on this machine
    "Bash(python3 -m ibkr_cli:*)",
    "mcp__ibkr-tws__place_order",               # belt-and-suspenders if any MCP slips in
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

# A runner takes the argv + a timeout and returns (returncode, stdout, stderr).
Runner = Callable[[list[str], float], Awaitable[tuple[int, str, str]]]

_DISABLED_MSG = (
    "⚠️ Natural-language ordering is offline (telegram_agent disabled). "
    "The brake is still running."
)
_UNAVAILABLE_MSG = (
    "⚠️ Natural-language ordering is unavailable (the `claude` CLI isn't "
    "installed or on PATH). The brake is still running."
)
_FAILED_MSG = (
    "⚠️ Couldn't process that order request — the analysis agent failed or timed "
    "out. Try again, or run the gate from a terminal. The brake is unaffected."
)
_EMPTY_MSG = "⚠️ The analysis agent returned no reply. Try rephrasing the order."


def build_claude_argv(text: str, cfg) -> list[str]:
    """Build the headless `claude -p` argv for analysing *text*. Pure.

    Confinement (see _DENY_TOOLS): `default` permission mode (not the operator's
    global `auto`), an analyze-only allow-list, explicit deny rules for every
    write path, and strict MCP isolation so no inherited server (e.g. ibkr-tws's
    place_order) is reachable.
    """
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
        _ANALYZE_TOOL,
        "Read",
        "--disallowed-tools",
        *_DENY_TOOLS,
        "--append-system-prompt",
        _SYSTEM_PROMPT,
    ]


async def run_agent(
    text: str,
    cfg,
    *,
    runner: Runner = run_capture,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Run the headless agent on *text*; return a chat-ready reply. Never raises."""
    if not cfg.enabled:
        return _DISABLED_MSG
    if which(cfg.claude_bin) is None:
        log.warning("telegram_agent: %r not found on PATH — NL ordering disabled", cfg.claude_bin)
        return _UNAVAILABLE_MSG

    argv = build_claude_argv(text, cfg)
    try:
        rc, stdout, stderr = await runner(argv, cfg.timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — a flaky agent must never crash the brake
        log.error("telegram_agent run failed: %s", exc)
        return _FAILED_MSG

    if rc != 0:
        log.error("telegram_agent exited %s: %s", rc, stderr.strip()[:500])
        return _FAILED_MSG

    reply = stdout.strip()
    return reply if reply else _EMPTY_MSG

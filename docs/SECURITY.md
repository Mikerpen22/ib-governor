# Security Posture & Hardening — ib-governor

> **Scope:** the brake daemon (`governor/`), its dependencies, and its operational threat model. `ib-governor` is a **single-operator personal trading tool** that, when armed, can place real brokerage orders; the trust boundary is the operator's own machine.

## Reporting a Vulnerability

Please report security issues **privately**, not via public GitHub issues.

- Use GitHub's **"Report a vulnerability"** button (Security → Advisories) on this repository to open a private security advisory.
- **Do not** open a public issue or pull request for a suspected vulnerability — that discloses it before a fix exists, and this tool can move money.
- This is a solo-maintained project. Expect a **best-effort** acknowledgement within a few days; please allow reasonable time for a fix before any public disclosure.

When reporting, include the affected version/commit, reproduction steps, and the impact you observed.

## Bottom line

**No Critical or High issues in the shipped dry-run state.** The system is outbound-only (no inbound socket), keeps all secrets out of the repo, uses TLS, has no shell/eval/pickle reachable, and gates every account-touching action behind a single dry-run chokepoint + a single-use, chat-authed confirm token.

**It ships SAFE — you arm it at your own risk.** Two independent locks are **closed by default**: `live.dry_run: true` *and* `readonly: true`. While either holds, no order can leave the daemon. **Residual risk is concentrated at *arming*** (`dry_run: false`), where the Telegram bot token becomes transitively authority to move money — see the Arming Security Checklist.

## Architecture security properties (verified by review)

- **No inbound attack surface** — the daemon is a *client* only: outbound to TWS on `127.0.0.1` and an outbound HTTPS long-poll to Telegram. No listening socket / webhook / server.
- **Two default-closed locks** — `live.dry_run: true` and `readonly: true` both ship enabled. The daemon only transmits an order when **both** are explicitly disabled; the safe defaults mean a fresh clone cannot trade until the operator deliberately arms it.
- **Secrets externalized** — Telegram creds come only from env (`.env`, gitignored); `.env.example` has empty values; git history scanned for token-shaped strings → no secrets ever committed. IBKR auth is delegated to the TWS desktop app (the daemon connects to an already-authenticated TWS) — no brokerage credentials live in this codebase.
- **One gated write chokepoint** — every `reqGlobalCancel`/`placeOrder` is inside `ActionExecutor._guarded`, which returns early (logging) when `dry_run` is set. Grep-verified: no write call anywhere else.
- **Chat-id auth (load-bearing, test-pinned)** — `TelegramClient.poll()` drops any update not from the configured `chat_id`, so a stranger messaging the bot cannot confirm an action.
- **TLS intact** — httpx→Telegram over HTTPS (no `verify=False`); TWS link is loopback-only.
- **No dangerous primitives** — no `eval`/`exec`/`pickle`/`os.system`/`shell=True`; YAML via `safe_load`.

## Dependency audit (pip-audit)

- **The brake's own dependencies are clean** — `ib_async 2.1.0`, `httpx 0.28.1`, `pydantic 2.13.4`, `PyYAML 6.0.3`, `eventkit`, `certifi`: **no known vulnerabilities** at audit time. Supply-chain provenance verified (`ib_async` = maintained fork by the original `ib_insync` author; no typosquats).
- **`pip` itself** has historically carried *install-time* CVEs (malicious-wheel path traversal etc.), not runtime. → keep `pip` upgraded in whatever virtualenv runs the daemon.
- **Isolate the daemon's environment** — run `ib-governor` in its own dedicated virtualenv. Do not share an interpreter with unrelated heavyweight packages (data-science/ML stacks, notebook servers, etc.); their CVEs are not the brake's, and a clean, minimal environment keeps the audit surface small.
- **`pip-audit`** should be run against that virtualenv (and wired into CI — see the checklist) so dependency drift on a money-touching daemon is caught early.

## Findings & status

| # | Severity | Finding | Status |
|---|---|---|---|
| H1 | High (armed only) | Confirm token 16 bits; no attempt limit; a re-tripping rule mints multiple valid tokens | **Partially hardened:** entropy → 32 bits + one-token-per-action-key applied. Attempt-budget deferred (checklist). |
| M2 | Medium | Leaked bot token → reads alert stream (NAV/P&L) + can spam the chat | **Deferred to arming:** redact $ from Telegram text; treat token as critical secret. |
| M3 | Medium | Lockout file world-readable + trusted on read (local tamper could disable a safety control) | **Hardened:** file written `0600`. |
| L4 | Low | `osascript` escaping incomplete (backslash) — AppleScript notification spoofing at most, no RCE | **Hardened:** backslash escaped before quotes. |
| L5 | Low | Broad `except` around comms | **Accepted** (availability — the brake must outlive a Telegram outage). |

## Hardening applied in this pass

- Confirm-token entropy `secrets.token_hex(2)` → `token_hex(4)` (16 → 32 bits).
- One-outstanding-token-per-action-key — a re-tripping rule no longer accumulates multiple valid tokens (also closes the `trim` double-confirm risk).
- `osascript` notifier escapes backslash before quotes.
- Lockout state file written with `0600` permissions.
- `pip` upgraded in the daemon's virtualenv.

## ⚠️ Arming Security Checklist — do ALL before `live.dry_run: false`

The brake ships SAFE (`dry_run: true` **and** `readonly: true`, both default-closed). Arming means deliberately disabling those locks — **you do so at your own risk.** Before arming:

1. **TWS-side (the real backstop):** keep "Allow connections from localhost only" checked; restrict "Trusted IP Addresses" to `127.0.0.1`; **keep the server-side "Read-Only API" checkbox ON until the moment you arm** — `readonly=True` in `ib_async` is NOT an interlock (it only skips a startup fetch). Test against the **paper account (port 7497)** first.
2. **Bot token = brokerage-grade secret:** `chmod 600 .env`; never commit it; rotate via @BotFather if a daemon host is ever compromised. Token possession alone lets an attacker read your alerts via `getUpdates` and post to your chat.
3. **Redact $ from Telegram** *(code change at arming):* stop putting NAV/P&L dollar figures in Telegram message bodies; keep specifics to the local desktop notification + logs. Caps info-disclosure if the token leaks.
4. **Add a confirm attempt-budget** *(code change):* after N wrong `CONFIRM` guesses against outstanding tokens, invalidate all pending + alert the operator.
5. **Pin dependencies:** pin exact versions (especially `ib_async`) with a lockfile + hashes; wire `pip-audit` into CI. Supply-chain drift on a money-touching daemon is a real risk.
6. **Restrict log perms** if logs are shared, since alert text can carry P&L.

## Trust boundary

This is a single-operator daemon on the operator's own trusted machine. Anyone with local shell on that machine can read `.env`, edit `rules.yaml` thresholds, or tamper with the lockout file — these are **owner-trusted by design**. The model protects against remote/network adversaries and accidental self-harm, not against a compromised local account.

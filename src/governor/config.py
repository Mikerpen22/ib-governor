"""Tunable thresholds, loaded and validated from config/rules.yaml.

No threshold is hardcoded in rule logic — every number lives here, seeded from
the vault's trigger card and owned by the user.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, NonNegativeFloat, NonNegativeInt, PositiveFloat, field_validator

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class TelegramConfig(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    poll_timeout: int = 30  # long-poll seconds for getUpdates

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


def load_env_file(path: str | Path = ".env") -> None:
    """Populate os.environ from a .env file (KEY=VALUE lines), WITHOUT overriding
    already-set vars. No-op if the file is absent. Lets the daemon pick up secrets
    under launchd, which doesn't source your shell profile."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def telegram_from_env() -> TelegramConfig:
    """Telegram creds come from env (secrets), not rules.yaml."""
    return TelegramConfig(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )


class FuturesRules(BaseModel):
    """Defaults are the vault trigger-card numbers (Trading Post-Mortem Jun 12)."""

    house_money_win_usd: PositiveFloat = 3000.0      # win above this -> 48h lockout
    daily_loss_usd: PositiveFloat = 1500.0           # loss beyond this -> platform off
    max_losing_trades: NonNegativeInt = 3            # losing-streak -> platform off
    overtrading_warn: NonNegativeInt = 10            # trades/day soft warning
    overtrading_hard: NonNegativeInt = 20            # trades/day hard halt
    max_overnight_contracts: NonNegativeFloat = 2.0  # MNQ-equiv overnight cap (≈⅓ NAV at live MNQ notional ~$61k/contract on a ~$350k NAV; not the stale $42k config default)
    close_window_min: NonNegativeFloat = 15.0        # minutes-before-close to check overnight
    max_notional_pct: float = Field(0.50, gt=0, le=5)  # intraday futures notional / NAV; fraction of NAV, 0–5 range; 1.0 = 100% of NAV (not 0–1 percent)
    churn_count: NonNegativeInt = 5                  # same-contract trades/day -> scalping flag


class LiveConfig(BaseModel):
    """Connection + runtime wiring for the live daemon. All tunable; lives under
    `live:` in rules.yaml. host/port/client_id are environment config, not secrets."""

    host: str = "127.0.0.1"
    port: int = 7496                       # live TWS; set 7497 for paper
    client_id: int = 4                     # Desktop=2, Claude Code=3, ibkr-cli=1, daemon=4
    gate_client_id: int = 5               # the pre-trade gate's own client id, distinct from the daemon's client_id (4) so the gate can connect while the daemon is running
    daily_client_id: int = 6              # the daily-summary collector's own client id, distinct from daemon (4) + gate (5) so it can read while the daemon holds 4
    readonly: bool = True                  # Plan 2 only reads; Plan 3 introduces actions
    account: str = ""                      # blank = the sole managed account
    session_close_et: str = "16:00"        # HH:MM ET — the "close" the overnight rule keys off
    briefing_times_et: list[str] = Field(default_factory=lambda: ["10:30", "12:30", "15:55"])
    mnq_notional_usd: PositiveFloat = 42000.0   # MNQ-equiv reference for overnight normalization
    staleness_seconds: PositiveFloat = 90.0     # snapshot age beyond this -> BRAKE BLIND
    dry_run: bool = True                   # log staged actions; never execute (Plan 3 wires real)
    confirm_ttl_seconds: PositiveFloat = 300.0  # a staged action's confirm token expires after this
    action_cooldown_seconds: PositiveFloat = 300.0  # after an action executes, suppress re-staging the SAME action this long (prevents post-execute over-trim)

    @field_validator("session_close_et")
    @classmethod
    def _valid_close(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError(f"session_close_et must be HH:MM, got {v!r}")
        return v

    @field_validator("briefing_times_et")
    @classmethod
    def _valid_briefings(cls, v: list[str]) -> list[str]:
        for t in v:
            if not _HHMM.match(t):
                raise ValueError(f"briefing time must be HH:MM, got {t!r}")
        return v


class EquitiesRules(BaseModel):
    single_name_pct: float = Field(0.15, gt=0, le=1)   # any one name > this % of NAV
    sector_pct: float = Field(0.25, gt=0, le=1)        # any one sector > this % of NAV
    retrade_per_week: NonNegativeInt = 2               # same name traded > this many times/week
    drawdown_for_add_flag: float = Field(0.10, gt=0, le=1)  # add-into-drawdown active above this DD


class PortfolioRules(BaseModel):
    min_cushion: float = Field(0.25, gt=0, le=1)       # excess-liquidity/NAV below this -> alert
    max_gross_leverage: PositiveFloat = 2.0            # gross position value / NAV above this -> alert
    drawdown_moratorium_pct: float = Field(0.10, gt=0, le=1)  # DD beyond this -> moratorium alert


class GateRules(BaseModel):
    """Pre-trade gate thresholds: per-trade sizing band keyed off NAV."""

    max_trade_pct_nav: float = Field(0.015, gt=0, le=1)  # single trade notional > this % of NAV -> CAUTION


class EquitySetupRules(BaseModel):
    """Minervini Stage-2 + VCP thresholds (defaults seeded from the /vcp skill)."""
    stage2_confirmed_min: NonNegativeInt = 6     # of 7 criteria -> "confirmed"
    stage2_candidate_min: NonNegativeInt = 4     # 4-5 -> "candidate"; <=3 -> "none"
    high_proximity_pct: float = Field(0.75, gt=0, le=1)   # 52wk position to count as "near high"
    min_range_ratio: PositiveFloat = 1.30        # 52wk high/low
    ma200_slope_lookback: NonNegativeInt = 20    # bars
    pivot_extended_pct: float = Field(0.05, gt=0, le=1)   # past pivot -> extended -> CAUTION
    pivot_wait_pct: float = Field(0.10, gt=0, le=1)       # extended->wait boundary (must sit between extended and too_late)
    pivot_too_late_pct: float = Field(0.15, gt=0, le=1)
    contraction_loose_pct: float = Field(0.18, gt=0, le=1)


class FuturesSetupRules(BaseModel):
    """Futures four-factor setup thresholds."""
    ma_fast: NonNegativeInt = 20
    ma_mid: NonNegativeInt = 50
    ma_slow: NonNegativeInt = 200
    atr_period: NonNegativeInt = 14
    atr_lookback: NonNegativeInt = 100           # window for the ATR percentile
    atr_elevated_pctile: float = Field(0.70, gt=0, le=1)
    atr_compressed_pctile: float = Field(0.30, gt=0, le=1)
    range_lookback: NonNegativeInt = 20          # 20-day high/low for location
    extension_chase_pct: float = Field(0.02, gt=0, le=1)
    rsi_period: NonNegativeInt = 14
    rsi_overbought: PositiveFloat = 70.0
    rsi_oversold: PositiveFloat = 30.0


class SetupRules(BaseModel):
    history_duration: str = "1 Y"                # reqHistoricalData duration for the candidate
    min_bars: NonNegativeInt = 200               # need 200+ for MA200; below -> "insufficient"
    equities: EquitySetupRules = Field(default_factory=EquitySetupRules)
    futures: FuturesSetupRules = Field(default_factory=FuturesSetupRules)


class RulesConfig(BaseModel):
    futures: FuturesRules = Field(default_factory=FuturesRules)
    live: LiveConfig = Field(default_factory=LiveConfig)
    equities: EquitiesRules = Field(default_factory=EquitiesRules)
    portfolio: PortfolioRules = Field(default_factory=PortfolioRules)
    gate: GateRules = Field(default_factory=GateRules)
    setup: SetupRules = Field(default_factory=SetupRules)


def load_config(path: str | Path) -> RulesConfig:
    """Read and validate a rules YAML file.

    Raises FileNotFoundError if missing, ValueError on bad YAML or values that
    violate the schema (e.g. a negative threshold).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"rules config not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {p}: {exc}") from exc
    try:
        return RulesConfig.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError -> surface as ValueError
        raise ValueError(f"invalid rules config in {p}: {exc}") from exc

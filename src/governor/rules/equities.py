from __future__ import annotations

from ..config import EquitiesRules
from ..model import ActionType, AssetClass, Severity, StateSnapshot, Trip


def _worst_over(mapping: dict[str, float | int], threshold: float) -> tuple[str, float] | None:
    """Return the (key, value) pair with the highest value that exceeds *threshold*, or None."""
    over = [(k, v) for k, v in mapping.items() if v > threshold]
    if not over:
        return None
    return max(over, key=lambda kv: kv[1])


def single_name(s: StateSnapshot, c: EquitiesRules) -> Trip | None:
    worst = _worst_over(s.name_weights, c.single_name_pct)
    if worst is None:
        return None
    name, w = worst
    return Trip("equities.single_name", AssetClass.EQUITY, Severity.WARN,
                f"{name} is {w:.0%} of NAV (> {c.single_name_pct:.0%} single-name cap).",
                ActionType.ALERT_ONLY, {"name": name, "weight": f"{w:.4f}"})


def sector_concentration(s: StateSnapshot, c: EquitiesRules) -> Trip | None:
    worst = _worst_over(s.sector_weights, c.sector_pct)
    if worst is None:
        return None
    sec, w = worst
    note = " (sector unknown — verify)" if sec.lower() == "unknown" else ""
    return Trip("equities.sector_concentration", AssetClass.EQUITY, Severity.WARN,
                f"{sec} is {w:.0%} of NAV (> {c.sector_pct:.0%} sector cap){note}.",
                ActionType.ALERT_ONLY, {"sector": sec, "weight": f"{w:.4f}"})


def retrade_churn(s: StateSnapshot, c: EquitiesRules) -> Trip | None:
    worst = _worst_over(s.name_trade_counts_week, c.retrade_per_week)
    if worst is None:
        return None
    name, k = worst
    return Trip("equities.retrade_churn", AssetClass.EQUITY, Severity.WARN,
                f"You've traded {name} {k}x this week (> {c.retrade_per_week}). Churn.",
                ActionType.ALERT_ONLY, {"name": name, "count": str(k)})


def add_into_drawdown(s: StateSnapshot, c: EquitiesRules) -> Trip | None:
    if s.drawdown_pct > c.drawdown_for_add_flag and s.equity_adds_at_loss_today:
        names = ", ".join(s.equity_adds_at_loss_today)
        return Trip("equities.add_into_drawdown", AssetClass.EQUITY, Severity.WARN,
                    f"Adding to losing name(s) [{names}] while down {s.drawdown_pct:.0%}. "
                    f"Averaging down, or buying weakness?",
                    ActionType.ALERT_ONLY, {"names": names})
    return None

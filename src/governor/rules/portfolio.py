from __future__ import annotations

from ..config import PortfolioRules
from ..model import ActionType, AssetClass, Severity, StateSnapshot, Trip


def margin_cushion(s: StateSnapshot, c: PortfolioRules) -> Trip | None:
    if s.nav <= 0:
        return None
    if s.margin_cushion < c.min_cushion:
        return Trip("portfolio.margin_cushion", AssetClass.PORTFOLIO, Severity.WARN,
                    f"Margin cushion {s.margin_cushion:.0%} < {c.min_cushion:.0%}. Thin buffer.",
                    ActionType.ALERT_ONLY, {"cushion": f"{s.margin_cushion:.4f}"})
    return None


def gross_leverage(s: StateSnapshot, c: PortfolioRules) -> Trip | None:
    if s.gross_leverage > c.max_gross_leverage:
        return Trip("portfolio.gross_leverage", AssetClass.PORTFOLIO, Severity.WARN,
                    f"Gross leverage {s.gross_leverage:.2f}x > {c.max_gross_leverage:.2f}x.",
                    ActionType.ALERT_ONLY, {"gross_leverage": f"{s.gross_leverage:.4f}"})
    return None


def drawdown_moratorium(s: StateSnapshot, c: PortfolioRules) -> Trip | None:
    if s.nav <= 0:
        return None
    if s.drawdown_pct > c.drawdown_moratorium_pct:
        return Trip("portfolio.drawdown_moratorium", AssetClass.PORTFOLIO, Severity.WARN,
                    f"Drawdown {s.drawdown_pct:.0%} > {c.drawdown_moratorium_pct:.0%} from the high. "
                    f"2-week moratorium on new overlays (your rule).",
                    ActionType.ALERT_ONLY, {"drawdown": f"{s.drawdown_pct:.4f}"})
    return None

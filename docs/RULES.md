# Rule Catalog

> Generated from `src/governor/rules/catalog.py` — do not edit by hand.
> Regenerate with `python -m governor.rules.catalog`.

13 rules across futures, equities, and portfolio. Every threshold lives in `config/rules.yaml`; the **Config keys** column is the path to tune.

## Futures

| Rule | Severity | Action | Config keys | What trips it |
|------|----------|--------|-------------|---------------|
| `futures.house_money_lockout` | hard | lockout_futures_48h | `futures.house_money_win_usd` | Realized futures win exceeds the house-money threshold → stage a 48h futures lockout. |
| `futures.daily_loss_stop` | hard | platform_off_today | `futures.daily_loss_usd`<br>`futures.max_losing_trades` | Daily loss limit (realized + open futures P&L) or losing-streak limit hit → platform off for the day. |
| `futures.overtrading` | warn / hard | alert_only / platform_off_today | `futures.overtrading_warn`<br>`futures.overtrading_hard` | Futures trade count crosses the warn threshold (alert), then the hard threshold (platform off). |
| `futures.overnight_notional` | hard | trim_futures | `futures.max_overnight_contracts`<br>`futures.close_window_min` | Oversized futures position still on near the close → stage a trim to the overnight cap. |
| `futures.live_notional` | warn | alert_only | `futures.max_notional_pct` | Intraday futures notional exceeds its allowed share of NAV → leverage-creep alert. |
| `futures.same_contract_churn` | warn | alert_only | `futures.churn_count` | One contract traded too many times in a day → scalping / churn alert. |

## Equities

| Rule | Severity | Action | Config keys | What trips it |
|------|----------|--------|-------------|---------------|
| `equities.single_name` | warn | alert_only | `equities.single_name_pct` | A single name exceeds its share-of-NAV cap → concentration alert. |
| `equities.sector_concentration` | warn | alert_only | `equities.sector_pct` | A single sector exceeds its share-of-NAV cap → concentration alert. |
| `equities.retrade_churn` | warn | alert_only | `equities.retrade_per_week` | A name is traded too many times in one week → churn alert. |
| `equities.add_into_drawdown` | warn | alert_only | `equities.drawdown_for_add_flag` | Adding to a losing name while the book is in drawdown → averaging-down alert. |

## Portfolio

| Rule | Severity | Action | Config keys | What trips it |
|------|----------|--------|-------------|---------------|
| `portfolio.margin_cushion` | warn | alert_only | `portfolio.min_cushion` | Margin cushion (excess liquidity / NAV) falls below the floor → thin-buffer alert. |
| `portfolio.gross_leverage` | warn | alert_only | `portfolio.max_gross_leverage` | Gross leverage (gross position value / NAV) exceeds its cap → leverage alert. |
| `portfolio.drawdown_moratorium` | warn | alert_only | `portfolio.drawdown_moratorium_pct` | Drawdown from the high-water mark exceeds the limit → new-overlay moratorium alert. |

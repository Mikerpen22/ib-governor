# Order Types

> Generated from `src/governor/gate/order_catalog.py` — do not edit by hand.
> Regenerate with `python -m governor.gate.order_catalog`.

6 selectable order types for the pre-trade gate. Pick one with `--type` (and `--adaptive` for the Adaptive variants) on `python -m governor.gate analyze …`. **Required** flags are in bold; the rest are optional modifiers. Discover the same table from the CLI with `python -m governor.gate order-types`.

| Order type | `--type` | Required | Optional | What it does | When to use |
|------------|----------|----------|----------|--------------|-------------|
| **Market** (`market`) | `market` | — | `--tif`<br>`--stop-loss`<br>`--take-profit` | Fill immediately at the best available price; no price guaranteed. | You need it filled now and accept whatever the book gives you. |
| **Limit** (`limit`) | `limit` | `--limit` | `--tif`<br>`--stop-loss`<br>`--take-profit` | Fill only at your limit price or better; may not fill at all. | You have a price in mind and would rather miss the trade than chase it. |
| **Stop (stop-market)** (`stop`) | `stop` | `--stop` | `--tif` | Resting order that becomes a market order once the stop price trades. | Trigger an entry on a breakout, or exit once a level breaks — fill speed over price. |
| **Stop-Limit** (`stop-limit`) | `stop-limit` | `--stop`<br>`--limit` | `--tif` | Becomes a limit order (at --limit) once the stop price trades; bounds slippage but can miss. | Same trigger as a stop, but you refuse to fill worse than your limit. |
| **Adaptive Market** (`adaptive-market`) | `market` (`--adaptive`) | — | `--priority`<br>`--tif`<br>`--stop-loss`<br>`--take-profit` | A market order run through IBKR's Adaptive (IBALGO) for better fills than a naked market. | Want it filled now without watching the tape — better average fill than a raw market, no price to guess. |
| **Adaptive Limit** (`adaptive-limit`) | `limit` (`--adaptive`) | `--limit` | `--priority`<br>`--tif`<br>`--stop-loss`<br>`--take-profit` | A limit order run through IBKR's Adaptive (IBALGO) to work the order toward your limit. | You have a limit but want the algo to work the order patiently/urgently rather than rest passively. |

## Cross-cutting capabilities

These layer on *any* entry above — they are not separate order types.

### Adaptive (IBKR IBALGO)

`--adaptive` runs a **Market** or **Limit** order through IBKR's Adaptive algo for better average fills; it is a *modifier*, not a base type (`order.orderType` stays MKT/LMT). It is **invalid on Stop / Stop-Limit** (TWS rejects it). Tune its aggression with `--priority`:

- **Priorities:** Urgent, Normal, Patient (default **Normal**). Urgent leans toward immediacy; Patient leans toward price.

### Bracketing (attached protective legs)

Add `--stop-loss PX` and/or `--take-profit PX` to *any entry* to attach protective child orders. They are OCA-grouped (one cancels the other) and placed GTC by default (see TIF) so a filled entry is never left unprotected after the entry's own session ends.

### Time-in-force (TIF)

`--tif` sets the **entry's** lifetime (DAY / GTC; default **DAY**). `--protective-tif` sets the bracket children's lifetime (default **GTC** so protective stops outlive the session — a DAY protective stop would be cancelled by TWS at the close, leaving the fill unprotected overnight).

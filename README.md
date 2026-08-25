# mojotrader

Day-trading research and indicators for the index futures **NQ** (Nasdaq-100) and **ES** (S&P 500).

## Indicators

### `pinescript/golden_ticket_vwap_indicator.pine`
Golden Ticket VWAP — a recreation of the "VWAP GOLDEN TICKET" prop-firm strategy
concept: a session VWAP pullback entry gated by a 1-hour trend-symbol momentum
filter (default `CME_MINI:NQ1!`), VWAP-slope and RSI-extreme-reset quality
filters, fixed-point stop/target risk management, daily trade/loss/win
lockouts, configurable session windows, a rolling backtest window, and its own
self-computed "Legacy Performance Dashboard" (it does not use `strategy()`).
Entries/exits are also broadcast as JSON `alert()` payloads for a
"QuantCrawler Ghost" style Tradovate webhook bridge, with a dry-run toggle and
an independent webhook contract count.

The original script is closed/protected on TradingView (and TradingView was
unreachable from the build environment), so this is a best-effort
reconstruction from the published input panel and public description — not a
decompiled copy. See the file's header comment for details.

### `pinescript/bms_market_structure.pine`
BMS Market Structure — a ZigZag that plots confirmed swing points and labels them
`HH / HL / LH / LL`.

**Swing rule**
- A swing **LOW** is confirmed when a later candle **closes above** the low candle's high.
- A swing **HIGH** is confirmed when a later candle **closes below** the high candle's low.
- Toggle `Confirm swings on candle CLOSE` off to confirm the instant a wick breaks the level instead.

**Close-level markers**
When a swing point is confirmed, a small horizontal line is drawn at the **close
of the confirming candle** — the candle that closed above the low candle's high
(for a swing low) or below the high candle's low (for a swing high).

**Higher-timeframe structure on a lower-timeframe chart**
The same structure engine also runs on a **selectable higher timeframe** and is
overlaid on the current chart, so you can trade a fast chart while seeing the
slower structure. Configurable under the *Higher-timeframe structure* group:
- `Show higher-timeframe structure` — on/off
- `Higher timeframe` — a dropdown; pick any timeframe (defaults to `15`).
- Separate leg colors/width, label toggle, and confirming-candle close markers,
  styled distinctly (blue/orange, thicker) so they stand apart from the chart-
  timeframe structure.

The HTF engine is **non-repainting**: a higher-timeframe bar is only processed
once it has fully closed.

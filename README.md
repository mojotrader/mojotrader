# mojotrader

Day-trading research and indicators for the index futures **NQ** (Nasdaq-100) and **ES** (S&P 500).

## Indicators

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

## Strategies

### `pinescript/orbib_strategy.pine`
**ORBIB** — three setups on one equity curve: Halyard (a 15-minute opening-range
break that builds its own 15m candles, so the chart can be 1m/3m/5m/15m), ORB
(09:30–09:45 range) and IB (09:30–10:30 range), both entering on a fib pullback
after the range breaks. Pine nets everything into one position, so a "seat" rule
lets only one setup hold a trade at a time: **Halyard > ORB > IB**.

#### No broker automation

The PickMyTrade / Tradovate webhook block has been **removed**. The script places
orders in TradingView's own strategy engine (so the Strategy Tester and the
risk/reward boxes work as before) and emits nothing to any third party.

The only `alert()` calls left are the **manual break alerts** — plain readable
text carrying the direction, the level that broke, and the planned entry, stop and
target, so the trade can be placed by hand. Create one alert on the script with
condition *"alert() function calls only"* and point it at a notification channel.

Do **not** point that alert at a broker webhook. A TradingView alert set to
*"alert() function calls only"* carries every `alert()` call in the script to
whatever destination it was given, and TradingView sends anything that is not
valid JSON as `text/plain` — which an order endpoint answers with HTTP 415
*unsupported media type*.

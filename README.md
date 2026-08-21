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

### `pinescript/anchored_vwap_0930_15m_dots.pine`
Anchored VWAP from the **09:30 NY open** through **16:00**, re-anchored every RTH session —
but **calculated on the 15-minute timeframe** and drawn as **dots**, so it can be read on a
1-minute chart.

- **Calculation timeframe** (default `15`) — the running `sum(price*vol)/sum(vol)` is built
  from bars of this timeframe whatever timeframe the chart is on, so the value matches what
  the same VWAP shows on a 15m chart. Keep the chart at or below it.
- **One dot per closed 15m bar**, placed on the chart bar that closed it (e.g. the 09:44 bar
  of a 1m chart for the 09:30–09:45 15m bar).
- **Dot colour = slope** vs. the previous dot: green up, red down, white flat. The session's
  first dot is white — there is nothing before it to slope from.
- `Flat threshold (ticks)` decides how much movement still counts as flat. It defaults to `0`,
  i.e. only an exactly unchanged VWAP is white; raise it to colour a near-flat VWAP white too.

Like the HTF structure engine, the 15m data is pulled with the canonical
`expr[1]` + `lookahead_on` idiom, so it is **non-repainting** — a 15m bar is only used once it
has fully closed.

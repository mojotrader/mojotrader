# mojotrader

Day-trading research and indicators for the index futures **NQ** (Nasdaq-100) and **ES** (S&P 500).

## Indicators

### `pinescript/mtf_sweep_fvg_mss_indicator.pine`
MTF Setup — a three-timeframe checklist that highlights the bar where 4h direction,
a 15m liquidity sweep and 1m confirmation all line up. **Run it on a 1-minute chart.**
It only draws the setup; it places no orders.

**1) 4h gives direction**
- Last **closed** 4h candle closes **above** the one before it → long bias.
- Closes **below** it → short bias.
- `4h bias rule` selects the comparison: previous **close** (default), previous
  **high/low** (a close beyond the whole candle — stricter), or previous **midpoint**.

**2) 15m gives the sweep**
- Short: the 15m candle spikes **above** the previous 15m **high**, then trades back **under** it.
- Long: the 15m candle spikes **below** the previous 15m **low**, then trades back **over** it.
- The 15m candle is aggregated from the 1m bars rather than requested, so the
  **developing** candle is visible — the spike is caught on the 1m bar that makes it
  instead of 15 minutes later.
- With `Only look for setups in the 4h direction` on (default), only the sweep that
  matches the 4h bias arms a window.

**3) 1m gives the confirmation, inside that spiking candle**
- A **Fair Value Gap** in the trade direction — bearish `low[2] > high`, bullish `high[2] < low`.
- A **Market Structure Shift** — the first 1m **close** below the last swing low (short)
  or above the last swing high (long). Swings come from a pivot of configurable
  left/right bars (`1/1` = the classic 3-bar swing).
- `Required order` can force *FVG then MSS* or *MSS then FVG*; the default accepts either.
- `Allow 1m confirmation N bars after the sweep candle closes` defaults to `0`, i.e.
  both must land **inside** the spiking 15m candle. Raise it to let confirmation spill over.

**Signal timing**
- `Live (inside the 15m candle)` — the trigger is published on the 1m bar that completes
  the trio, while the 15m candle is still open (it could still close back beyond the swept level).
- `Confirm on 15m close` — the trigger is held until the 15m candle actually closes back
  inside the swept level, then published anchored to the **original** trigger bar.

Neither mode repaints: the trigger bar is fixed the moment it happens, and the 4h bias
only ever reads fully-closed 4h candles.

**On the chart** — dotted line at the swept level, shaded sweep window, the 1m FVG box,
a dashed line on the broken swing, a `LONG SETUP` / `SHORT SETUP` label at the trigger,
and a top-right checklist table. Alerts exist for long, short, and either side.

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

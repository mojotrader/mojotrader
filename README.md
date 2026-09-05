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

### `pinescript/sweep_reversal_strategy.pine`
Sweep Reversal — trades confirmed **liquidity-sweep reversals** (stop runs that fail).

**The sequence**
1. **Level** — a pivot high/low confirmed by `Swing length` bars on each side (a pool of resting stops).
2. **Sweep** — price trades through that level, optionally by a minimum ATR distance.
3. **Reclaim** — price closes back on the original side of the swept level.
4. **Confirmation** — within `Maximum confirmation bars`, a candle *closes* beyond the local structure of
   the last `Local structure length` bars, with a body of at least `Minimum displacement body` × ATR.
   That close is the signal; the order fills at the next bar's open.

**Trade handling**
- Stop just beyond the sweep extreme (the liquidity-grab wick) + an ATR buffer; setups whose stop is wider
  than `Maximum stop distance (ATR)` are skipped.
- Target as an R multiple of the initial risk, measured from the **actual fill**, not the signal close.
- The protective stop is submitted together with the entry, so the position is covered from its first bar.
- Optional break-even, ATR trailing stop, time stop, opposite-signal reverse, and EOD flatten.
- Sizing: fixed contracts, or % risk of a **fixed** account size (no compounding), `$ per point` per contract.
  The backtest itself runs with a large `initial_capital` and `margin_long/short = 0` so the broker emulator
  never rejects an entry for lack of funds — futures notional would otherwise refuse every order and the
  backtest would show no trades at all.
- Filters: RTH session + entry cutoff, max trades per day, weekday, EMA trend, date range.

Non-repainting: signals are evaluated on closed bars only and the swing levels are already confirmed pivots.

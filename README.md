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

### `pinescript/katana_model_strategy.pine`
**The Katana Model** — a 15-minute FVG displacement scalp built on the idea that price
delivers to 50% of any range. Implemented from the "Katana Model" methodology breakdown
(@SSJTRADES, May 2026). **Run it on a 1m–5m chart** (chart timeframe ≤ the FVG timeframe)
so the limit, stop and target are evaluated intrabar; FVGs are read from **closed** 15m
candles only, so the model does not repaint.

**The four steps**
1. **Displacement** — a closed 15m Fair Value Gap sets the bias.
   Bullish gap (`candle1.high < candle3.low`) → **long**, buying the discount retrace;
   bearish gap (`candle1.low > candle3.high`) → **short**, selling the premium retrace.
2. **Set the range** — anchor the fib where the displacement leg *began* (the lowest low
   before the FVG for longs, the highest high for shorts) and terminate it at the highest
   high / lowest low made *after* the FVG formed. The terminus keeps extending while price
   runs, so the fib re-scales until the entry fills. `Fib anchor` picks between the whole
   leg (a lookback over the last N 15m candles, default) and the three gap candles alone;
   `Keep the anchor when another same-direction FVG forms` treats a leg containing several
   FVGs as one range.
3. **Entry / stop / target** — all retracements of that range: **entry `.50`** (equilibrium,
   a resting limit), **stop `.75`**, **target `.25`** — a clean **1:1** on every setup.
4. **Let it play out** — no breakeven, no cutting early in drawdown. The only overrides are
   the entry cutoff, the EOD flatten and the daily max-loss halt.

**Guards and knobs**
- Session, last-entry cutoff and EOD flatten (ET), plus per-weekday toggles.
- Unfilled limits are cancelled after `maxWait` minutes, if the leg's origin gives way, or
  when the setup leaves the trading window; `Max trades per day` caps re-entries.
- Filters: minimum FVG size, min/max range size, "entry level must sit inside the FVG",
  and "arm only while price is still beyond the entry level" (skips setups that would fill
  the limit instantly).
- Risk sizing by % of a **fixed** account size (no compounding) with a daily max-loss halt,
  or a fixed contract count.
- Draws the FVG box and the live entry/stop/target lines (they re-scale with the fib, then
  freeze on the fill), a $ risk/reward label per trade, a rules panel, and `alert()` calls
  for setup armed / filled.

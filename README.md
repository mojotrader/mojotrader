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

### `pinescript/ib_acceptance_breakout_strategy.pine`
IB Acceptance Breakout — an Initial Balance strategy built on the
[tradingstats.net IB breakout study](https://tradingstats.net/initial-balance-breakout-statistics/)
(2,686 ES + 2,833 NQ RTH sessions, 2015–2025). Run it on a 5m chart.

**Where the edge is.** Touching the IB edge carries almost no information — 97.8%
of ES days and 96.2% of NQ days break the IB at least once, and 28.7% (ES) /
22.6% (NQ) break *both* sides ("rotation day"). A plain stop order at the IB high
is therefore a ~1-in-4 whipsaw by construction. Three conditions in the study do
carry information, and the strategy trades only where all three line up:

1. **Acceptance, not a wick.** When the C-period (10:30–11:00 ET, the 30 minutes
   right after the IB) *closes* above the IB high on ES, 45.5% of those days go on
   to a full 100% extension — one whole IB range beyond the edge. A C-period close
   below the IB low gives 50.0% downside. Both are more than double the
   unconditional rate. That close is the trigger.
2. **IB width vs ATR sets how far it travels.** Narrow IB (< 0.5× ATR) breaks
   98.7% of days with a median extension of 74.8% of the IB range; Wide
   (1.0–1.5×) breaks 93.5% / 84.1% and extends 39.6% / 36.7%; Extreme (> 1.5×)
   breaks only 66.7–76.9% and extends ~22.3%. Targets are therefore tier-scaled,
   and the Extreme tier is skipped by default.
3. **The midpoint is where the edge dies.** If price never retraces more than 25%
   into the IB, the day closes in the breakout direction 93.8% of the time and
   none of those days became double-break days. Past a 50% retrace only 24.8%
   still close in the breakout direction and over half turn into double breaks.
   The IB midpoint is the default stop.

**Rules**
- **IB** 09:30–10:30 ET; classified by `IB range / daily ATR(14)` as of yesterday's
  close (non-repainting) into Narrow / Normal / Wide / Extreme.
- **Trigger** the 11:00 confirm close beyond the IB edge; an optional second look
  at 11:30 (over 80% of first breakouts have happened by then). Skipped if the
  opposite edge already broke.
- **Entry** market at the confirm close, or a stop beyond the confirm period's
  extreme if you want follow-through first.
- **Stop** IB midpoint by default (25% retrace / opposite extreme / broken edge
  also selectable), floored by a minimum tick distance so a thin stop can't
  inflate size.
- **Target** tier-scaled beyond the broken edge — Narrow 1.00×, Normal 0.75×,
  Wide 0.50×, Extreme 0.25× the IB range, with an optional scale-out at the 50%
  extension and the stop pulled to breakeven.
- One trade per day, entry cutoff 11:30, flat at 16:00. Optional August/December
  and weekday filters (those two months show the study's weakest breakout rates).
- Sizing by % risk on a fixed account size (no compounding), with an optional
  daily max-loss halt.

An on-chart panel shows today's IB range, ATR, IB/ATR ratio, tier, the study's
reference break rate and median extension for that tier, the target, and whether
the confirm close accepted or not.

> This encodes the study's conditional probabilities as rules; it is not a
> backtest of the study. Validate on your own data and sample before sizing up.

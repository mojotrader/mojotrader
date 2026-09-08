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

### `pinescript/mojo_daytrader_engine.pine`
**MojoTrader Day-Trading Engine** — one script that runs four intraday setups
through a single shared risk, exit and reporting engine.

**Why it exists.** The other 18 strategy scripts each re-implement their own
session handling, sizing and exits, so their Strategy Tester numbers are not
comparable to one another — a better-looking profit factor might just be a
different stop rule or a different contract count. This runs one setup at a
time through identical plumbing, so setups can be compared head to head.

**Setups** (select one; Pine nets all orders into a single position, so running
several at once corrupts their stops and targets):

| Setup | Range | Entry | Stop | Target |
|---|---|---|---|---|
| OR15 Breakout | 09:30–09:45 | stop order, first side to break | opposite extreme | N x range |
| IB Breakout (bias) | 09:30–10:30 | stop order in the bias direction | opposite extreme | N x range |
| PDH/PDL Breakout | prior day H/L | stop order, first side to break | N x ATR | N x ATR |
| VWAP Mean-Reversion | session VWAP | limit at the N-sigma band | wider sigma band | VWAP |

The IB bias rule is the nqstats observation that where the first hour *closes*
inside its own range predicts which extreme breaks: a close in the top quartile
favours the high going, the bottom quartile favours the low, and the middle is
a coin flip worth standing aside for.

**The cost panel is the point.** Zarattini & Aziz's 5-minute opening-range
breakout on QQQ posts a Sharpe of 1.06 before costs and 0.23 after two cents a
share of slippage — the edge lived inside the bid-ask spread. The on-chart panel
breaks out gross P&L, commission paid, and estimated slippage cost, and states
plainly whether the strategy survives.

To use it: raise **Slippage (ticks)** and re-read the panel. The tick count at
which net profit crosses zero is that setup's margin of safety. A setup that
only works at zero ticks is not tradable. On NQ one tick is 0.25 points = $5 per
contract; on MNQ it is $0.50. Breakout entries are stop orders, which slip most
in exactly the fast conditions that trigger them, so test at two ticks and up.

**Shared risk management**: % risk of a fixed account size (no compounding, so
the equity curve shows the edge rather than the leverage), a contract safety
cap, a daily max-loss halt, per-weekday filters, an entry cutoff, and a flatten
time set before the cash close.

**Note on the slippage input.** Pine cannot read back the slippage set in the
strategy declaration, so the value has to be repeated in a second input for the
panel's estimate. Keep the two in sync.

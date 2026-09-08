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

### `pinescript/orbib_ib_aligned_strategy.pine` *(superseded — kept for reference)*
**ORBIB — IB aligned to ORB.** The three-setup ORBIB script (Halyard + ORB + IB),
shipped in an IB-only configuration where the ORB is demoted from a strategy to a
direction filter.

**The rule.** At 09:45 the opening range resolves a direction. At 10:30 the initial
balance resolves its own. The IB setup is armed only when the two **agree** — long
IB after a long ORB bias, short IB after a short ORB bias. Disagree and the day is
skipped. A morning where the ORB produced *no* direction counts as disagreement:
alignment means an actual matching signal, not merely the absence of a conflicting
one.

**Why it works mechanically.** `Run ORB strategy` gates order placement only. The
ORB range, its first-extreme test, its close-depth test and the resulting `or_dir`
are computed every session regardless of that toggle. So the ORB filters the IB
while never taking a trade of its own.

Shipped defaults: Halyard **off**, ORB trading **off**, IB **on**, alignment **on**.
Turn `IB: only trade when the ORB bias AGREES` off to get unfiltered IB behaviour
back; turn the run toggles on to restore the original three-setup script, whose
seat-precedence rules are left fully intact.

**Auditing the filter.** An invisible filter is one you cannot check, so the ORB
range lines stay on the chart while the ORB is acting as the filter, a small orange
arrow at 09:45 marks the bias the IB must match, and a grey label at 10:30 names
every day the rule blocked and what each side wanted.

**Expect roughly half the trades.** Requiring two independent reads of the same
morning to agree discards the mornings where the first fifteen minutes and the first
hour disagree — which is the chop the IB pullback gets minced in, but it is also a
large cut to the sample. Give it a long backtest before drawing conclusions, and
note that the probability table's IB rows now sample only the days the filter let
through, so they are not comparable with a run taken with the filter off.

**Also note:** the ORB min-range filter gates ORB *orders*, not the ORB *direction*,
so it has no effect on the alignment rule. To exclude narrow opening ranges from the
filter as well, tighten the ORB close-depth input instead.

### `pinescript/orbib_htf_trend_strategy.pine`
**ORBIB + higher-timeframe trend filter.** The original three-setup ORBIB
(Halyard + ORB + IB, all running, seat precedence intact) with one rule layered
over all three: a **long needs price above the 5-day EMA, a short needs it below.**

Nothing else changes — same ranges, same pullback entries, same stops, targets and
precedence. Only the direction permission is new.

**Reference price.** Each setup is judged at the instant it decides its own
direction, on the price that decision already uses: the ORB range's final close at
09:45, the IB range's final close at 10:30, the breakout candle's close for Halyard.

**The EMA is deliberately one day stale.** `request.security(sym, "D", ta.ema(close, 5))`
— the obvious spelling — hands an intraday backtest the *completed* daily EMA for the
day being tested, which at 10:30 contains the rest of that day's trading. That is
lookahead, and it is the most common way a higher-timeframe filter flatters a
backtest. This file uses `ta.ema(close, len)[1]` with `lookahead_on` instead: the
previous completed daily EMA, knowable before the open, fixed all session, identical
on historical and real-time bars.

**Blocked setups.** A blocked ORB or IB setup is unwound completely, so orders,
drawings, alerts, the probability study and the seat logic all see "no setup" without
their own gate. A blocked Halyard break still *consumes* the day, matching how every
other Halyard filter behaves.

**Halyard's reversal is gated too**, unlike Halyard's own 15m trend filter which by
design gates only the first trade. The reversal is by definition the opposite side of
the loser, so leaving it open would wave through the one trade the filter most wants
to stop. An input restores the first-trade-only convention.

Inputs: master on/off, timeframe (D/W), EMA length, per-setup toggles for ORB / IB /
Halyard, and blocked-setup markers. The EMA plots on the chart coloured by side —
teal when longs are permitted, red when shorts are.

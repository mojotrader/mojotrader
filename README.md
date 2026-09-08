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

### `pinescript/orbib_session_filters_strategy.pine`
**ORBIB + two switchable session filters.** The three-setup ORBIB (Halyard + ORB +
IB, seat precedence intact) with two independent day-classification filters over the
ORB and IB setups, each a mode dropdown so scenarios are swept from the settings
dialog rather than by editing code.

**Filter 1 — overnight (Globex) range.** Where the 09:30 RTH open sits in the
18:00–09:30 range.

| Mode | Long allowed | Short allowed | Blocks days? |
|---|---|---|---|
| Open outside ON range | open > ON high | open < ON low | yes, if open is inside (your choice) |
| Open vs ON midpoint | open ≥ midpoint | open < midpoint | never |

**Filter 2 — prior-day location.** Where the same open sits against yesterday.

| Mode | Long allowed | Short allowed | Blocks days? |
|---|---|---|---|
| Open outside prior range | open > PDH | open < PDL | yes, if open is inside (your choice) |
| Open vs prior close | gap up | gap down | never |
| Open vs prior midpoint | open ≥ midpoint | open < midpoint | never |

Prior-day levels come from **RTH only** or from the symbol's **daily bar** — which
for futures spans the full ~23-hour session including Globex and is therefore
materially wider, making "open outside the prior range" much rarer. That switch is a
scenario in its own right.

**Both ship `Off`**, so out of the box this is the unfiltered three-setup ORBIB.
That is the baseline. Get a number with everything off, then enable one filter, then
the other. Enabling both at once tells you nothing about either.

Other scenario knobs: AND/OR when both filters are live, and a reference price of
either the 09:30 open (one verdict for the whole day) or the price at the moment each
setup resolves (09:45 for ORB, 10:30 for IB).

**How to read the result.** Compare **trades removed against P&L removed.** A filter
that cuts 40% of trades and 40% of profit has done nothing but shrink the sample. A
filter earns its place only by removing disproportionately more loss than trades.

**No Halyard toggle, deliberately.** Both filters classify the day from the 09:30 RTH
open. Halyard's range candle forms at 00:00/01:00 ET and its break can fire at 03:00
ET — before today's RTH open exists and while the overnight range is still forming. A
Halyard toggle would silently compare against yesterday's open and an unfinished
range, and would look like it was working. Filtering Halyard needs levels knowable at
its own decision time.

**Neither filter can look ahead.** The overnight range is frozen at the RTH open.
Prior-day RTH levels are rolled from the previous session before the current one
starts writing. The daily-bar source uses `[1]` + `lookahead_on` — the previous
completed daily bar; asking for the daily high or low without that `[1]` would hand an
intraday backtest today's finished range.

Blocked setups are labelled with the direction refused and which filter refused it,
and the levels each active filter reads are drawn on the chart.

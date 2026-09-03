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
**ORBIB** — three setups on one equity curve: HALYARD (a 15-minute opening-range
break that builds its own 15m candles, so any chart timeframe from 1m to 15m
gives identical levels), ORB (09:30–09:45 range) and IB (09:30–10:30 range),
both entered on a fib pullback after the range breaks. Only one of the three can
be in a trade at a time (Halyard > ORB > IB).

**Exit: take profit or stop loss, nothing else.**
The `Hold trades until TP or SL (no time-based exit)` input ships **ON**, and it
switches off every clock-driven exit:

- no 15:30 ET end-of-day flatten for ORB/IB,
- no 13:30 / 14:30 ET day flat for Halyard,
- no bar-count time stop.

A filled trade runs until its target or its stop trades — through the evening,
the Asian session, London, and into the next day if that is what it takes. The
stop and target orders are kept live on every bar, overnight included.

What is still on the clock is **entry** management only: an unfilled limit is
cancelled at 15:30 ET so nothing rests overnight, and each engine's entry window
is unchanged. The daily max-loss circuit breaker (off by default) still closes
everything, carried trades included — it is a risk rule, not a clock.

Two consequences worth knowing:

1. **Run the chart with extended trading hours on.** The exits can only fill on
   bars that exist; on a regular-hours chart an overnight trade sits untouched
   until the next 09:30 ET open and fills on the gap.
2. **An open trade owns its engine until it closes.** While an ORB trade is still
   running, the ORB engine takes no new setup the next morning — same for IB and
   Halyard. That is the existing one-trade-at-a-time rule, extended across the
   date change.

Untick the input to get the original behaviour back (Halyard flat at 13:30/14:30
ET, ORB/IB flat at 15:30 ET).

**Sizing: a progressive equity ladder (ORB / IB).**
Sizing mode 3, `Progressive contracts (equity ladder)`, ships on and overrides
the fixed-contract and risk-$ modes. Contracts climb in $6,000 rungs off the
account's starting capital ($30,000 as shipped, editable in Properties):

| Equity | ORB (first / avg-down) | IB (first / avg-down) |
|---|---|---|
| below $30,000 | 1 / 1 | 2 / 2 |
| $30,000 (base) | 1 / 1 | 2 / 2 |
| $36,000 | 2 / 2 | 4 / 4 |
| $42,000 | 3 / 3 | 6 / 6 |
| $48,000 | 4 / 4 | 8 / 8 |

The ladder runs both ways — give $6,000 back and every unit drops a rung, so
$42k → $36k takes ORB from 3 to 2 and IB from 6 to 4. A rung is a **full** step
in each direction, so $35,999 is still base size and so is $29,999.

**It never sizes below the base.** Under $30,000 it stays at ORB 1/1 and IB 2/2
however deep the drawdown runs — the arithmetic would reach 0 contracts at
$24,000 and go negative below that, and the two minimum inputs clamp it at the
base counts instead. So the ladder scales a winning account up, hands back the
same steps on the way down, and never trades smaller than it started or stops on
its own. (Set a minimum to 0 only if you *do* want that engine to switch off in a
deep drawdown.)

The rung is read from **closed** equity (starting capital + net profit), not from
an open trade's floating value, so a live trade swinging around cannot change the
size of the setup being armed beside it. Size is locked when a setup arms — 09:45
for ORB, 10:30 for IB. Halyard is not on this ladder; it keeps its own sizing
block. Every step, the base counts, the per-rung counts, the minimums and an
optional hard cap are inputs.

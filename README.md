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

### `pinescript/ib_break_overnight_strategy.pine`
**IBBRK** — a 60-minute initial balance on *any* hour of the clock, then the first
side a **1-minute candle closes beyond**, stop on the other side of the range,
scale out at risk:reward multiples, flat at the session close whether or not a
target was hit.

The window is the point of the file, so it's all inputs. Three clock settings
define a session — the IB itself (`0030-0130` by default, crossing midnight is
fine), the **trade cutoff** after which no new break is taken, and the **flatten**
that closes whatever is still open. Both times are converted once into *minutes
after the IB closes*, so an overnight window needs no wrap-around special case.
Moving the flatten is what varies "session length": `0230` is a 2-hour session,
`0330` a 3-hour one. Pulling the cutoff in ahead of the flatten stops the engine
opening a trade with ten minutes left to live; it never touches a position
already open.

- **Entry** — a close beyond the level on a confirmation timeframe you choose
  (5m by default), or a wick/touch option to measure the difference, since an
  overnight range gets tagged constantly.
- **Stop** — how deep into the range it sits, measured back from the edge that
  broke: `100%` (the far IB edge), `75%`, `50%` (midpoint), a custom %, or fixed
  points. The entry is a candle close and can land well past the level, so
  anchoring to the edge keeps the stop on a price the market recognises.
- **Targets** — three legs at configurable R multiples with configurable position
  shares, each its own entry id with its own exit order. Optional breakeven after
  target 1. Anything the percentages leave unallocated becomes a runner carried
  to the session close.
- **Session close** — flat at market on the last bar of the window, target or no
  target. Not optional: a range from 01:30 means nothing by lunchtime.
- **Break-statistics table** — recomputes single-break / double-break / neither
  rates on your symbol and your window, using the same break test the entry uses.

The entry rule doesn't depend on the chart: the confirmation candle is built from
the clock inside the engine, so "5-minute close beyond the IB" means the same
thing on a 1m, 3m or 5m chart. All times are New York regardless of the chart's
timezone.

**What it measured on MNQ, 5m bars, 2026-05-24 → 2026-09-04 (103 days, ~70
sessions per window), 5m-close entry, stop on the far side of the IB, 1R target,
costs included:**

- The single-break rate reproduces the published study closely (82.7% here vs
  81.8% quoted for NQ) — but its **correlation with profitability across 44
  windows is +0.07**. Two windows share an 83% single-break rate: one makes
  +1.9 pts/trade, the other loses −7.3.
- Pooled across all 44 windows the family is a **loser after costs**: 2,410
  trades, −2.86 pts/trade, t = −2.20. Only 37% of windows were profitable.
- The best window (07:00–08:00 ET) scored t = 1.73. Picking the best of 44
  windows from pure noise typically scores t = 2.16 — so **nothing in this data
  beats what chance alone produces.**
- With a full-IB stop, targets of 2R and above are never reached inside a
  one-hour window: 2R, 3R and hold-to-close give byte-identical results.

Treat the window inputs as a research tool, not a recommendation. 103 days of
summer tape is far too short to establish any of this.

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

**Selectable ORB / IB time windows.**
The `Time windows - ORB / IB` group makes the clock an input instead of a
hardcoded constant, so the same engine can be pointed at any time of day:

| Input | Default | What it sets |
|---|---|---|
| Trading session | `0930-1600` | hours the two engines may work, and where their day resets |
| ORB range window | `0930-0945` | the candle the ORB measures |
| IB range window | `0930-1030` | the candle the IB measures |
| ORB window shuts when IB completes | on | the original hand-over rule; off runs the two independently |
| ORB entry cutoff | `1500` | no ORB order placed at or after this (HHMM) |
| IB entry cutoff | `1400` | same for IB |
| Cancel unfilled orders at | `1530` | end-of-day mark |

Every time is **New York**, whatever the chart's timezone — so the levels don't
move when you change the chart. The defaults are exactly the values the file
always had, so leaving the group alone changes nothing. Halyard keeps its own
clock (anchored to 10:30 Asia/Kolkata, following the US DST changeover) and is
not in this group.

A window that could never trade fails loudly at compile time rather than quietly
showing no signals — a range finishing after its own entry cutoff, an IB range
that completes before the ORB one while the hand-over rule is on, or a minute
field above 59.

**A Halyard break landing on an open ORB/IB trade.**
With trades held to their target or stop, the ORB/IB trade still sitting there
when Halyard breaks is usually one carried over from yesterday. The two cases are
opposites, and the `When a Halyard break lands on an OPEN ORB/IB trade` input
(Halyard → Trade management) picks the behaviour. The default,
`Flatten opposite, stack same`:

- **Break the other way** → the ORB/IB trade is closed at market and Halyard
  takes its place. There's no third option: netted into one position the two
  would only cancel each other down to a remainder that neither engine owns and
  neither engine's stop covers.
- **Break the same way** → Halyard is added on top and the ORB/IB trade is left
  completely alone. They're separate entry ids with separate exit orders, so each
  keeps its own stop and target and closes on its own terms.
- **Either way**, an ORB/IB entry limit still *resting* unfilled is forfeited the
  moment a Halyard trade opens. That order was placed while the seat looked free;
  left out there it could fill behind the Halyard trade, or silently reverse it.

The other two modes are `Stack same only` (an opposite break is skipped rather
than closing a working trade) and `Skip the Halyard break`, the original
one-trade-at-a-time rule.

**Sizing: a progressive equity ladder (all three setups).**
All three ride one ladder — the same rung, read off the same account equity —
and differ only in the counts they put on it. Contracts climb in $6,000 rungs off
the account's starting capital ($30,000 as shipped, editable in Properties):

| Equity | ORB (first / avg-down) | IB (first / avg-down) | Halyard |
|---|---|---|---|
| below $30,000 | 1 / 1 | 2 / 2 | 4 |
| $30,000 (base) | 1 / 1 | 2 / 2 | 4 |
| $36,000 | 2 / 2 | 4 / 4 | 8 |
| $42,000 | 3 / 3 | 6 / 6 | 12 |
| $48,000 | 4 / 4 | 8 / 8 | 16 |

The ladder runs both ways — give $6,000 back and every unit drops a rung, so
$42k → $36k takes ORB from 3 to 2, IB from 6 to 4 and Halyard from 12 to 8. A
rung is a **full** step in each direction, so $35,999 is still base size and so
is $29,999.

**It never sizes below the base.** Under $30,000 it stays at ORB 1/1, IB 2/2 and
Halyard 4 however deep the drawdown runs — the arithmetic would reach 0 contracts
at $24,000 and go negative below that, and the minimum inputs clamp it at the
base counts instead. So the ladder scales a winning account up, hands back the
same steps on the way down, and never trades smaller than it started or stops on
its own. (Set a minimum to 0 only if you *do* want that engine to switch off in a
deep drawdown.)

**The rung is per trade, not per day.** It reads **closed** equity (starting
capital + net profit), which moves the moment any trade closes, and every order is
sized at the instant it is *placed* — not when its setup armed hours earlier. So a
result from one setup resizes the next order from any other:

- Halyard stops out at 10:20 → the ORB order placed at 10:25 is a rung smaller.
- ORB takes its target at 11:15 → the IB order placed at 11:20 is a rung bigger.

Floating P&L is deliberately not counted (there's an input if you want it), so an
open trade swinging in and out of profit can't keep changing the size of the order
being placed beside it.

The count is fixed when the order is **placed**, not when it fills. For ORB and IB
that leaves a gap, since their limit can rest a while before price returns to it —
but the account is flat during that wait (a condition of placing at all), so the
only thing that can move the rung inside it is a Halyard trade opening *and*
closing while the limit waits. Halyard has no gap: it's a market order, so placed
and filled are the same instant.

Every entry is stamped with its rung (`ORL r+2`, `HAL-L r-1`), so the Strategy
Tester's trade list shows the ladder working trade by trade.

The rung size and base equity are set once, in the `Risk sizing - ORB / IB`
group, and read by all three because they describe one account. Each setup keeps
its own base count, per-rung count and floor — ORB and IB in that same group,
Halyard under `Halyard - Position sizing`, where its ladder switch overrides the
fixed-contract and $-risk sizing it used to run on.

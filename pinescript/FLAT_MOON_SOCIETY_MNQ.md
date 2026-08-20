# FLAT MOON SOCIETY MNQ 1.4.0-rc.1 — Pine port

`pinescript/flat_moon_society_mnq_strategy.pine` ports
`FLAT_MOON_SOCIETY_MNQ_v1_4_0_RC1.cs` (NinjaTrader 8) to Pine Script v6.
The NinjaScript source, not the release README, is the reference: every
constant, threshold, precedence rule and fail-closed branch below was read out
of the C# and reproduced.

## Chart setup

| Setting | Value |
| --- | --- |
| Symbol | `MNQ1!` (or a dated MNQ contract) |
| Timeframe | **1 minute** — the script refuses anything else |
| Session | Extended hours **on** (the 23:00 UTC reference open lives in the overnight session) |
| Slippage | 1 tick (already set in the `strategy()` call) |
| Commission | $1.25 per contract per side, i.e. $2.50 round turn |

Load as much 1-minute history as your plan allows. Data before
`Trading Start Date (YYYYMMDD)` is warm-up: it builds the opening-range,
trend and session-return histories and the direction-model labels, but
submits no orders.

## What is reproduced exactly

* **Opening range** — 15 exact one-minute bars opening 09:30 through 09:44 ET,
  with per-bar sequence checking. A missing, duplicated or misaligned bar
  blocks the cash date.
* **Decision closes** — every quarter hour from 10:00 through 15:45 ET. The
  decision candle is the last 15 completed one-minute bars, validated by
  timestamp contiguity (`TryGetExactSignalState`).
* **Admission** — close beyond the range by >= 1 tick, directional body
  >= 0.15 of the signal range, close location >= 0.60, and >= 3 of the 15
  constituent bars reaching the confirmation level. Geometry rejects keep
  scanning; a touch veto consumes the cash date.
* **Direction** — raw breakout, reversed when `side * priorSessionBps < -300`,
  and/or when the quarterly 15-NN model returns a flip probability above 0.65
  (`(flips + 2) / 19`, so 11 of 15 neighbours).
* **The direction model itself** — all 14 features in source order, the causal
  breakout-vs-fade shadow pair that produces the labels (1-tick entry, stop and
  flat slippage, $2.50 round turn, stop-before-target priority, the same
  managed stop and 15:30 rule, label kept only when exactly one side is
  profitable), the quarterly expanding-window refit with population
  standardisation, and the distance tie-break on training sequence.
* **Entry refinement** — weak signal (body <= 0.20, 1 bar, keep) takes
  precedence over the high-volatility/low-touch vote (3 bars, flip when the
  aligned move <= 0); then prior-session disagreement (aligned prior > 100 bps
  and directional ORB body <= 0, 2 bars, always flip) takes precedence over
  intraday continuation (aligned 30-minute return > 25 bps and signal
  extension <= 0.25 ORB, 1 bar, flip only when the aligned move > 0). A missing
  observation minute blocks the date instead of submitting a late order.
* **Stops, targets, exits** — stop `clamp(1.25 x ORB, 2, 100)` points with
  NinjaTrader's half-up tick rounding; 3R target, or 1.25R in the high-ORB
  regime; managed stop to breakeven at 1.25R (or +0.10R at 0.75R when
  countertrend), applied from the following minute; the 15:30 ET exit outside
  `[0R, 1R)`; the 16:00 ET flatten.
* **Sizing** — all three modes, the defensive one-contract cap, the confidence
  score (50 trend + 50 low volatility, 50 on neutral fallback), the raw-notional
  leverage cap, and per-contract risk = stop distance + `max(stop slippage
  reserve, slippage)` + estimated round-turn cost. Percent equity is
  `Starting Equity + closed net profit`, with no open P&L and no account cash.
* **Data guards** — contiguous 09:30-16:00 one-minute bars, the required 23:00
  UTC reference open, non-negative volume, weekday cash dates, and the embedded
  rollover / degraded-data exclusion lists (now an input, pre-filled with the
  source's dates).

## Deviations forced by the platform

1. **Cash date.** NinjaTrader takes it from `SessionIterator.
   ActualTradingDayExchange`. Pine derives it by rolling at 17:00 ET
   (`Cash-date roll hour`), which gives the same exchange trading day for CME
   ETH data: the Sunday 18:00 ET open and the 23:00 UTC reference bar belong to
   the following cash date.
2. **Early closes are caught late.** NinjaTrader pre-blocks a cash date whose
   Trading Hours session does not cover 09:30-16:00. Pine cannot look ahead, so
   an early-close session is instead flattened and blocked on the exchange
   session's last bar (`Flatten on an early exchange close`), and the missing
   16:00 bar stops that date from entering the history. A trade taken earlier
   that day is exited rather than never opened.
3. **The direction model usually stays in warm-up.** It needs >= 100 labels from
   sessions *before* the current quarter, and predictions only start
   2023-01-01. Each session produces at most one label, and TradingView caps
   1-minute history (roughly 20k bars on lower plans, 200k on the highest —
   about 50 to 500 sessions). Unless you can load several years of 1-minute
   data, the model reports "warm-up" in the summary table and direction comes
   from the breakout plus the prior-session rule. That is the same behaviour
   NinjaTrader shows before its own warm-up completes, not a different
   algorithm. The summary table shows the label count so you can see where you
   stand.
4. **Rollover offsets.** The stored MNQ offset table exists to undo
   NinjaTrader's *Merge back adjusted* prices for the leverage cap only.
   TradingView continuous contracts are unadjusted, so the table is replaced by
   a single `Back-adjustment offset (points)` input, default 0. It affects
   nothing but the raw-notional leverage limit in the two percentage modes.
5. **Order plumbing.** `SetStopLoss` / `SetProfitTarget` in ticks become
   `strategy.exit(loss = …, profit = …)`, which measures from the actual fill
   and is live on the fill bar. The revised managed stop becomes
   `strategy.exit(stop = …, limit = …)` issued on the quarter-hour close, so it
   protects from the next one-minute bar, exactly as the NinjaScript comment
   requires. Market entries fill at the next bar open
   (`process_orders_on_close = false`).
6. **Commission is a constant.** `strategy()` arguments cannot be inputs, so the
   round turn is fixed at $2.50 ($1.25 per side) rather than read from an
   Analyzer template. `Estimated Round-Turn Cost / Contract` remains a sizing
   reserve only, as in the source.
7. **No broker-state handling.** Order rejections, partial fills, disconnects,
   the bounded administrative-exit retries, `StartBehavior.WaitUntilFlat` and
   realtime reconciliation have no Pine equivalent; the port keeps the logical
   fail-closed branches (block the date, flatten, stop trading it) but a
   TradingView backtest cannot model a rejected order.
8. **Diagnostics.** `Print` output becomes the on-chart summary table:
   eligible sessions, admitted signals, geometry rejects, touch vetoes,
   overrides, refinement paths, model labels and predictions, sizing skips,
   blocked dates and fail-closed events.

## Reading the summary table

`direction model` is the one to watch. `warm-up (n/100 labels)` means every
trade so far took its direction from the breakout and the prior-session rule.
`fitted (n rows)` means the quarterly model is live and the
`prior-session / kNN overrides` row shows how often each reversed a breakout.

## Limitations

Same as the source: no guarantee of one trade per day, of an exact next-open
fill, or of a loss limited to the configured risk. Historical results do not
guarantee future performance, and futures can lose more than the planned risk.

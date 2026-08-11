# mojotrader

Day-trading research and indicators for the index futures **NQ** (Nasdaq-100) and **ES** (S&P 500).

## Indicators

### `pinescript/mtf_sweep_fvg_mss_indicator.pine`
MTF Setup — a three-timeframe checklist that highlights the bar where 4h direction,
a 15m liquidity sweep and 1m confirmation all line up. **Run it on a 1-minute chart.**
It only draws the setup; it places no orders.

**1) 4h gives direction**
Judged from the last **closed** 4h candle against the one before it. Default rule,
`Break or failed break`:
- Closes **above** the previous 4h **high** → long. Closes **below** the previous **low** → short.
- Took the previous **low** but **failed to close below** it → long (failed breakdown).
- Took the previous **high** but **failed to close above** it → short (failed breakout).
- Took both sides and closed inside, or an inside candle → no bias.

Simpler comparisons are also available in `4h bias rule`: previous close, previous
high/low, or previous midpoint.

**2) 15m gives the sweep**
A sweep is **two** things, and the second is what separates it from a plain poke:
- Short: the 15m candle trades **above** the previous 15m **high** *and closes back under it*.
- Long: the 15m candle trades **below** the previous 15m **low** *and closes back over it*.

A candle that pokes through and **closes beyond** the level is a *break*, not a sweep, and is
discarded. The 15m candle is aggregated from the 1m bars rather than requested, so the poke is
seen on the 1m bar that makes it — but the level is only drawn faintly until the close confirms
it, and only then is it labelled a sweep.

`Sweep confirmed by` chooses which close counts:
- `15m candle must close back inside` (default) — the strict reading. The setup therefore
  triggers on the first 1m bar **after** that candle closes, the FVG and MSS having already
  formed inside it.
- `Any 1m close back inside (live)` — the first 1m close back inside confirms it. Triggers
  earlier, but the 15m close can still go against it.

With `Only look for setups in the 4h direction` on (default), only the sweep matching the 4h
bias starts a watch.

**3) 1m gives the confirmation, inside that spiking candle**
- A **Fair Value Gap** in the trade direction — bearish `low[2] > high`, bullish `high[2] < low`.
- A **Market Structure Shift** — the first 1m **close** below the last swing low (short)
  or above the last swing high (long). Swings come from a pivot of configurable
  left/right bars (`1/1` = the classic 3-bar swing).
- `Required order` can force *FVG then MSS* or *MSS then FVG*; the default accepts either.
- `Allow 1m confirmation N bars after the sweep candle closes` defaults to `0`, i.e.
  both must land **inside** the spiking 15m candle. Raise it to let confirmation spill over.

Nothing repaints: the trigger bar is fixed the moment it happens, and the 4h bias only ever
reads fully-closed 4h candles.

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

## Strategies

### `pinescript/mtf_1000_sweep_strategy.pine`
MTF 10:00 Sweep — the strategy built on the indicator above, narrowed to one shot per day.
**Run it on a 1-minute chart.**

**Bias — the 4h candle that closes at 10:00 ET**
On CME futures the 4h candles run 18:00 / 22:00 / 02:00 / 06:00 / 10:00 / 14:00 ET, so the
10:00 close ends the 06:00–10:00 candle and opens the one being traded. The bias comes from
that candle against the 4h candle before it, using the break-or-failed-break rule:

| 10:00 candle did this | Bias |
| --- | --- |
| closed above the previous 4h high | long |
| closed below the previous 4h low | short |
| took the previous low, failed to close below it | long |
| took the previous high, failed to close above it | short |
| inside candle, or took both sides and closed inside | no trade (tie-break selectable) |

Both bias candles are **aggregated from the chart's own bars** over a fixed clock span
(06:00–10:00 and 02:00–06:00 ET), not requested from the 4h series. TradingView aligns
intraday higher-timeframe bars to the session start, so a chart whose 4h grid does not land
on 10:00 — RTH-only, a non-futures symbol, a different session — would otherwise compare the
wrong two candles or produce no bias at all.

**This needs extended-hours (ETH) data.** The bias candles are built from 02:00–10:00 ET bars,
which an RTH-only chart does not have. When they are missing the panel says so rather than
sitting silent.

**Diagnostics panel** (bottom right, on by default) — the setup is rare, so the panel shows
today's live state (bias and *why*, window open/closed, sweep, FVG, MSS, position, last skip
reason) plus a cumulative funnel: days → days with a bias → **pokes** → **confirmed sweeps** →
FVG → MSS → triggers. When nothing fires, the funnel shows the exact step it stops at.

**Trade window — 10:00 to 14:00 ET**
The sweep, the confirmation, the entry and the exit all live inside that one 4h candle. New
entries stop at 14:00 and everything is flattened at 14:00. One trade per day by default.

**Setup** — identical to the indicator: a 15m candle that takes the previous 15m high/low in the
bias direction **and closes back inside it**, then a 1m FVG plus a 1m market structure shift
inside that spiking candle. A poke that closes beyond the level is a break, not a sweep, and is
skipped. The funnel counts pokes and confirmed sweeps separately, so the gap between the two
columns shows how often that happens.

**Entry / target / stop**
- Entry: market on the trigger bar, or a limit inside the 1m FVG (near edge / 50% / far edge).
- Target: **HOD** for longs, **LOD** for shorts.
- Stop: the **same distance** on the other side of the entry — a forced **1:1**. The stop is
  mirrored around the *actual fill*, not the trigger price, so the 1:1 is exact.

Because risk is whatever the distance to the day's extreme happens to be, the point stop can be
large. Three controls exist for that: `HOD / LOD measured from` (trading day 18:00 / RTH 09:30 /
the 10:00 4h open — the tighter anchors keep risk smaller), `Minimum` and `Maximum stop distance`,
and `% of account risked` sizing (fixed capital, no compounding). A setup whose stop is too far to
afford one contract is skipped rather than taken undersized.

`Let the target follow a new HOD / LOD` is off by default. Turning it on extends the target as the
day extends while the stop stays put, so the trade is no longer 1:1.

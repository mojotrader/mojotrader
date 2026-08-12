# mojotrader

Day-trading research and indicators for the index futures **NQ** (Nasdaq-100) and **ES** (S&P 500).

## Indicators

### `pinescript/mtf_sweep_fvg_mss_indicator.pine`
MTF Setup — a three-timeframe checklist that highlights the bar where 4h direction,
a 15m liquidity sweep and 1m confirmation all line up. The setup resolves on a
**1-minute chart**, but the drawings adapt to whichever timeframe you open (see
*On the chart* below), so you can step up to the 15m or 4h and still read the bias.
It only draws the setup; it places no orders.

**1) 4h gives direction**
**Every** 4h candle is classified against the candle before it, and each one carries the colour of
the bias *it* creates — so the rule can be read straight off the 4h chart. Default rule,
`Break or failed break`:
- Closes **above** the previous 4h **high** → long. Closes **below** the previous **low** → short.
- Took the previous **low** but **failed to close below** it → long (failed breakdown).
- Took the previous **high** but **failed to close above** it → short (failed breakout).
- **Inside candle**, or took **both sides** and closed inside → the rule has no signal of its own.

`Candles the rule cannot decide` controls those last two so that **no candle is left unmarked**:
`Carry the previous bias` (default) keeps the bias already in force, since nothing happened to
change it; `Bigger sweep wins` resolves two-sided candles by the side rejected harder (a deeper
poke above the high → short); `Leave unmarked` shows the gaps instead.

`Tag each 4h candle with its bias and the reason` puts one small label per closed candle — `L` or
`S` plus the clause that decided it (`closed above prev high`, `swept low, closed back above`,
`carried — inside candle`, …) — so the classification can be checked candle by candle.

A trade acts on the bias from the last **closed** 4h candle, which is one candle behind the shade
on the forming candle; `Shade shows` switches the colouring between the two. Sweeps are always
gated by the bias in force. The 4h candles are aggregated from the chart's own bars, so the
classification does not depend on how TradingView aligns its 4h grid.

Simpler comparisons are also available in `Bias rule`: previous close, previous high/low, or
previous midpoint.

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
  triggers on the first 1m bar **after** that candle closes, the FVG having already formed
  inside it.
- `Any 1m close back inside (live)` — the first 1m close back inside confirms it. Triggers
  earlier, but the 15m close can still go against it.

With `Only look for setups in the 4h direction` on (default), only the sweep matching the 4h
bias starts a watch.

**3) 1m gives the confirmation** — or is skipped entirely
`Signal on` → `The 15m sweep close alone` fires the moment the sweeping candle closes back inside,
without dropping to the faster chart at all. The gaps and MSS are still drawn, they just stop
gating anything. Otherwise:

Once the sweep candle has closed, a **Fair Value Gap** in the trade direction from inside it is
enough — bearish `low[2] > high`, bullish `high[2] < low`. An **inverse FVG** also counts: an
opposite-direction gap that price then *closes through*, flipping it into support (long) or
resistance (short). `Gap that confirms` selects FVG, iFVG, or either (default).

The gaps and the MSS are drawn **only inside a confirmed sweep candle** — a poke that closed
beyond its level gets no annotations at all.

A **Market Structure Shift** (the first 1m **close** below the last swing low for a short, above
the last swing high for a long) is tracked and drawn as well, but only gates the signal when
`Also require a market structure shift` is switched on. Swings come from a pivot of configurable
left/right bars (`1/1` = the classic 3-bar swing), and `Required order` — *FVG then MSS* or
*MSS then FVG* — applies only when the MSS is required.

Several FVGs can form inside one sweep candle. `Which FVG to use` decides which one counts:
`Most recent before the trigger` (default) keeps updating to the latest, so when the MSS is
gating the entry you get the gap in the **displacement leg that broke structure** rather than an
older one further back; `First one after the sweep` locks onto the earliest.

`Allow 1m confirmation N bars after the sweep candle closes` defaults to `0`, i.e. the FVG must
land **inside** the spiking 15m candle. Raise it to let the confirmation form after the close.

Nothing repaints: the trigger bar is fixed the moment it happens, and the 4h bias only ever
reads fully-closed 4h candles.

**On the chart — one shade, plus layers that follow the timeframe**
The **4h bias is the only shading in the script**, and it is drawn on **every timeframe** —
green while long, red while short. It is deliberately not tied to the drawing tier, because the
whole point is to carry the 4h read down onto the 15m and 1m charts. The sweep is marked with
lines and a label rather than a second background, so the shading always means exactly one thing.

Everything else is layered by resolution, on the default `Auto` detail setting:

| chart | drawn on top of the 4h shade |
| --- | --- |
| 4h | nothing — the shade alone |
| 15m | the sweep: swept level, sweep label, previous 15m high/low |
| 1m | the FVG and iFVG boxes, the broken-swing MSS line, and the setup label |

Set `Drawing detail` to `Everything` to draw every layer regardless of chart. The checklist table
reports which layer set is active. Alerts exist for long, short, and either side.

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

**Drawings follow the chart timeframe** — same layering as the indicator: a 4h chart gets the bias
shading only (green long / red short), a 15m chart adds the sweep, a 1m chart adds the FVG, MSS and
the R/R box. `Drawing detail` → `Everything` overrides it. This affects drawings only; the strategy
still has to run on a 1m chart to trade correctly.

**Diagnostics panel** (bottom right, on by default) — the setup is rare, so the panel shows
today's live state (bias and *why*, window open/closed, sweep, FVG, MSS, position, last skip
reason) plus a cumulative funnel: days → days with a bias → **pokes** → **confirmed sweeps** →
FVG → MSS → triggers. When nothing fires, the funnel shows the exact step it stops at.

**Trade window — 10:00 to 14:00 ET**
The sweep, the confirmation, the entry and the exit all live inside that one 4h candle. New
entries stop at 14:00 and everything is flattened at 14:00. One trade per day by default.

**Setup** — identical to the indicator: a 15m candle that takes the previous 15m high/low in the
bias direction **and closes back inside it**, then a 1m FVG from inside that candle. A poke that
closes beyond the level is a break, not a sweep, and is skipped. The market structure shift is
optional and off by default (`Also require a market structure shift`). The funnel counts pokes
and confirmed sweeps in separate columns, so the gap between them shows how often a poke closed
beyond the level instead of rejecting.

**Entry / target / stop**
- Entry: market on the trigger bar, or a limit inside the 1m FVG (near edge / 50% / far edge).
- Target: the **far side of the swept 15m candle** — its **high** for a long, its **low** for a
  short. So a long sweeps candle 1's low and aims at candle 1's high.
- Stop: the **same distance** on the other side of the entry — a forced **1:1**. The stop is
  mirrored around the *actual fill*, not the trigger price, so the 1:1 is exact.

`Target` can be switched to **HOD / LOD** instead, with `HOD / LOD measured from` choosing the
anchor (trading day 18:00 / RTH 09:30 / the 10:00 4h open). That target sits much further away
and, because the stop mirrors it, forces a correspondingly larger stop — `Minimum` and `Maximum
stop distance` and `% of account risked` sizing (fixed capital, no compounding) are the guards.
A setup whose stop is too far to afford one contract is skipped rather than taken undersized.

`Let the target follow a new HOD / LOD` applies only to the HOD/LOD target and is off by default;
a candle extreme is a fixed level and never trails.

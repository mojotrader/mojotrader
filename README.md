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

### `pinescript/anchored_vwap_0930_15m_dots.pine`
Anchored VWAP from the **09:30 NY open** through **16:00**, re-anchored every RTH session —
but **calculated on the 15-minute timeframe** and drawn as **dots**, so it can be read on a
1-minute chart.

- **Calculation timeframe** (default `15`) — the running `sum(price*vol)/sum(vol)` is built
  from bars of this timeframe whatever timeframe the chart is on, so the value matches what
  the same VWAP shows on a 15m chart. Keep the chart at or below it.
- **One dot per closed 15m bar**, placed on the chart bar that closed it (e.g. the 09:44 bar
  of a 1m chart for the 09:30–09:45 15m bar).
- **Dot colour = slope** vs. the previous dot: green up, red down, white flat. The session's
  first dot is white — there is nothing before it to slope from.
- `Flat threshold (ticks)` decides how much movement still counts as flat. It defaults to `0`,
  i.e. only an exactly unchanged VWAP is white; raise it to colour a near-flat VWAP white too.

Like the HTF structure engine, the 15m data is pulled with the canonical
`expr[1]` + `lookahead_on` idiom, so it is **non-repainting** — a 15m bar is only used once it
has fully closed.

## Strategies

### `pinescript/halyard_orb_ib_vwap_entry_strategy.pine`
The Halyard + Break-then-Pullback (ORB + IB) file with **one rule swapped**: ORB and IB no
longer rest their limit on a fib retracement of their own range (the 25%/75% line, with an
average-down unit at the 50% line). Both now enter at the **session VWAP anchored to the 09:30
NY open**.

- **One unit per setup.** The VWAP is a single price, so there is no second level to average
  down into. The avg-down contract/risk inputs stay in the dialog so old settings load, but
  nothing reads them — their titles say so.
- **The limit moves.** A fib level is fixed when the range closes; the VWAP re-prices every bar,
  so the resting order is re-issued each bar at the current VWAP. Fills land where the VWAP
  actually is when price returns to it, and a risk-$ contract count is recomputed until the fill.
- **Invalid entries are refused.** A fib retracement always sits between the break and the stop;
  the VWAP can drift onto or past the far range edge, which would mean entering at or beyond its
  own stop and would send the risk sizer's contract count to infinity. The setup only arms while
  the VWAP is at least `orbibMinStopFrac` (10%) of the range away from the stop.

**Selectable targets and stops** (ORB / IB) live in the *Targets & stops* group. Defaults
reproduce the previous hardcoded behaviour exactly.

- **Target**, per setup *and* per direction: `Range edge` (the broken level, no extension),
  `Ext 10% / 30% / 50%` (that far beyond it, as a fraction of the range), or `HOD/LOD` — the
  session high for a long, low for a short, taken as it stands on the fill bar and frozen there.
- **Stop**, per setup: `50%`, `75%` or `100%` of the range, measured as a retracement from the
  edge that broke — for a long, 100% is the range low, 75% a quarter of the way up from it, 50%
  the midpoint. Same convention the probability table's "75% line" already used.

The stop choice feeds the entry filter: a setup only arms while the VWAP is at least 10% of the
range clear of the stop, so tightening the stop also cuts how many days qualify. Expect
noticeably fewer trades at 50%. Halyard is unaffected — it keeps its own Risk:Reward target and
stop modes.

Everything else is untouched: the same ranges and clocks, direction rule, close-depth /
min-range / weekday / double-break / daily-loss filters, seat precedence (Halyard > ORB > IB),
cutoffs, flattens, the Halyard engine, the probability study and the webhook. Run it on a
**1-minute** chart.

### `pinescript/halyard_orb_ib_fib_strategy.pine`
The original fib-pullback build — ORB/IB enter at the 25%/75% line with an average-down unit
at the 50% line — with **selectable targets and stops** and the webhook payload fixed.
Defaults reproduce the previous hardcoded behaviour exactly.

- **Target**, per setup *and* per direction: `Range edge`, `Ext 10% / 30% / 50%`, or `HOD/LOD`
  (taken on the fill bar and frozen there).
- **Stop**, per setup: `50%`, `75%` or `100%` of the range, as a retracement from the edge that
  broke.

Because the entries here are fixed levels of the *same* range the stop is measured against,
tightening the stop can collide with them. Each unit is checked against the stop independently
and dropped if it is not clear by at least `orbibMinStopFrac` (10%) of the range:

| Stop | Shallow setup | Deep setup |
|---|---|---|
| 100% | trades, with add | trades |
| 75% | trades, with add | trades |
| 50% | trades, **add dropped** | **skipped** — entry sits on the stop |

**Webhook fix:** `str.tostring(na)` renders `NaN`, which strict JSON rejects, so a payload with
an unknown stop or target was thrown out at the far end — the entry stood with no bracket. All
numbers now go through `f_num` (na → `0`), the stop/target chain gained a live-IB branch and a
last-fill fallback, and the buy/sell/close instruction is sent as `data` as well as `action`.
An optional on-chart label prints the last transmitted payload.

### `pinescript/halyard_orb_ib_vwap_ladder_strategy.pine`
The fib build extended into a **three-rung ladder**. Each setup rests three limits in the same
direction at once — the **25% line**, the **50% line** and the **09:30 anchored VWAP** — so
whichever price is reached first is the first entry. There is no primary rung and no fixed
order. The first fill of any rung sets the stop and target for the whole ladder; every later
fill averages down into that same trade, under the same two exit prices, and all three exit
together.

- **Each rung toggles on and off independently** (`Run the 25% / 50% / 09:30 VWAP rung`), across
  ORB and IB at once — the same idea as `Run HALYARD`. Untick the two fib rungs and the Strategy
  Tester shows the VWAP entry on its own equity curve. A rung switched off is inert: never armed,
  never drawn, never filled, and left out of the ladder totals. Leaving ORB or IB on with all
  three rungs off raises a runtime error rather than showing a silently empty tester.
- Each rung has its own contract count, its own risk budget, and is sized on its own stop
  distance. The reported average entry is **size-weighted**.
- The two fib rungs are fixed when the range closes. The **VWAP rung is a moving limit**:
  re-issued every bar at the current VWAP, with fills tested against the price actually resting
  on the book from the previous bar's close.
- A rung not clear of the stop by 10% of the range is dropped; only losing all three kills the
  setup. At a 50% stop that drops the 50% rung.

**Globex trend filter** (off by default): ticked on, a long is only taken when the break bar
closes above a VWAP anchored to the **18:00 ET Globex open**, a short only when it closes below.
One session runs 18:00 ET the previous day → 16:00 ET today, then re-anchors at 18:00 for the
next one, so every RTH morning is read against the VWAP of the overnight that led into it —
unlike the 09:30 VWAP the third rung rides, which starts from nothing each morning. The gate is
decided once, on the break bar, and latched.

**Every rung is its own trade.** Each carries its own target and its own exit order; the stop is
shared and ends the day for all of them. When the VWAP rung reaches its target it closes only
that unit — the average-downs keep running under the same stop.

**The VWAP rung has a separate target**, per setup and direction, with its own options: the
09:30 VWAP's `1 / 2 / 3 sigma` bands (defaulting to 2 sigma), `HOD/LOD`, the range extensions,
or the range edge. Sigma is the anchored VWAP's own standard deviation, read on the fill bar and
frozen there; the bands are plotted so the target is visible. Any target that would land on or
behind its entry falls back to a 1R target.

Stops, filters, seat precedence, cutoffs, flattens, the Halyard engine, the probability study
and the webhook are otherwise as in the previous version.

### `pinescript/halyard_orb_ib_base_strategy.pine`
The base Halyard + Break-then-Pullback build with tuned defaults and the webhook fixed. No
selectable targets/stops — the stop is the far range edge and the targets are the hardcoded
extensions, as in the original.

- **Contracts**: ORB 1 / 1, IB 2 / 2 (first entry / average-down).
- **Close-depth filter off**: both depths default to `100`, so any close passes and only the
  which-extreme-came-first test decides direction. Lower either to switch that side back on.
- **Rules panel and probability table off** by default — both are reference readouts that cover a
  corner of the chart.
- **R/R label opens leftward** (`style_label_right` anchored at the current bar) so it no longer
  sits over the newest candles and the price scale.
- **Webhook**: the average-down sends the *increment* with `pyramid:true` so it stacks instead of
  forcing a flatten-and-reenter, and every price goes through `f_num` (na → `0`, never the `NaN`
  that made strict JSON reject the whole payload and strip the bracket). Direction ships as `data`
  as well as `action`; an on-chart label prints the last transmitted payload.

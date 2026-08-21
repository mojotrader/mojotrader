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

## Backtesting

A dependency-free Python harness for measuring several strategies against the
same data under the same cost assumptions. Standard library only — no pandas,
no install step, `python3` 3.11+ is all it needs.

```
backtest/
  lse.py          LSE vault API client (all schema guesses isolated here)
  bars.py         Bar type, CSV cache, New York session helpers
  engine.py       broker emulator — fills, brackets, costs
  indicators.py   ATR (Wilder), session-anchored VWAP + bands
  metrics.py      performance statistics and the comparison table
  strategy.py     Strategy base class and the bar loop
  strategies/     ports of the Pine strategies
fetch_data.py     pull candles into data/
make_sample_data.py  synthetic bars for testing the pipeline without an API key
run_backtest.py   run strategies and compare them
```

### Quick start

```bash
export LSE_API_KEY='lse_live_...'

python3 fetch_data.py --probe                 # confirm the API shape first
python3 fetch_data.py --symbol MNQ1! --resolution 5m --start 2024-01-01 --end 2024-12-31
python3 run_backtest.py --symbol MNQ1! --resolution 5m --detail
```

No key handy? Exercise the whole pipeline on synthetic bars:

```bash
python3 make_sample_data.py --symbol SAMPLE --days 120
python3 run_backtest.py --symbol SAMPLE --resolution 5m --detail
```

Sample data is a seeded random walk. It proves the plumbing works; it says
nothing about whether a strategy has an edge.

### Read this before trusting the API client

`backtest/lse.py` was written from the dashboard screenshot, not from the
published API reference. The transport is certain — host, `x-api-key` header,
HTTPS. **The endpoint paths and response shapes are inferred and may be wrong.**

Everything uncertain sits in two places: the `ENDPOINTS` dict, and the `_rows`
/ `_to_bar` parsers. Run `python3 fetch_data.py --probe` to print what the API
actually returns, then correct `ENDPOINTS` from real output. The parsers
already accept list-of-dicts, list-of-lists, columnar, and the usual envelope
keys, with epoch-second, epoch-millisecond, or ISO-8601 timestamps, so a shape
mismatch is more likely to be absorbed than to crash.

### What the engine models

Fidelity to TradingView's fill model is the point — a result here should be
comparable to the same strategy's Pine report.

- **Next-bar fills.** An order placed while bar N is evaluated fills from bar
  N+1, never inside bar N. Exit brackets included: a stop/target attached on
  the entry bar's close is live from the *following* bar. Strategies whose Pine
  source sets `process_orders_on_close = true` fill at the signal bar's close
  instead.
- **Intrabar ambiguity resolves to the stop.** When one bar's range covers both
  stop and target, the true path is unknowable, so the engine takes the loss.
  Assuming the target came first is the most common way a backtest inflates.
- **Gaps fill realistically.** A stop jumped over at the open fills at the
  open, which is worse than the level. A limit gapped through fills at the
  open, which is better.
- **Costs are on by default** — commission per contract per side, plus one tick
  of slippage against market and stop fills. Limit exits are not slipped.

Drawdown is measured on closed trades, so the equity curve is a step function
and intrabar heat was always worse than reported.

### Strategies

| Key | Pine source | Rule |
|---|---|---|
| `orb_fade` | `orb_fade_strategy.pine` | Fade failed breakouts of the 09:30–09:45 range; target the far side |
| `pdh_pdl` | `pdh_pdl_breakout_strategy.pine` | Stop orders beyond the prior RTH session's high/low |
| `vwap_reversion` | `vwap_mean_reversion_strategy.pine` | Fade a band touch back toward the session VWAP |

Each Python strategy mirrors a Pine file. **When you change one side, change
the other** — a silent divergence is worse than having no port, because the
backtest keeps producing plausible numbers.

`vwap_reversion` carries one addition the Pine lacks: `min_reward_pts`. Early
in a session the deviation bands sit almost on the VWAP, so a band touch can
target a fraction of a point away, or land on the wrong side of the entry
entirely — paying a full round turn for no reward. The default of `0.0`
reproduces the Pine exactly, degenerate trades and all. Raise it to filter them.

### Sweeping parameters

The runner compares strategies; parameter sweeps are a few lines against the
same API:

```python
from backtest.bars import load_bars
from backtest.strategy import run
from backtest.strategies import ORBFade
from backtest.metrics import compute

bars = load_bars("MNQ1!", "5m")
for rr in (0.5, 1.0, 2.0):
    trades, _ = run(ORBFade(rr=rr), bars)
    stats = compute(trades, point_value=2.0)
    print(rr, stats.trades, round(stats.net_pnl), round(stats.profit_factor, 2))
```

Sweeping is also the fastest way to overfit. Split the data and confirm a
parameter out of sample before believing it.

### Tests

```bash
python3 -m unittest discover -s tests -v
```

28 tests pin the fill model against hand-built bars where the correct answer is
known by inspection — next-bar timing, slippage direction, stop-first
resolution, gap fills, bracket arming, Wilder ATR, drawdown, DST boundaries,
and a no-lookahead check that truncating future bars leaves earlier trades
unchanged. A lookahead bug produces a beautiful equity curve, not an exception,
which is why these are worth having.

## Halyard + ORB + IB (combined)

Port of `halyard_orbib_combined.pine` — three setups sharing one position under
a strict precedence ("the seat"): a live Halyard trade holds it, ORB holds it
against IB, and Halyard skips its break entirely if ORB or IB is live.

```bash
python3 import_parquet.py data.parquet --resolution 1m   # needs pyarrow
python3 run_halyard.py --isolate --by-year
```

**Halyard is not an RTH strategy.** Its range candle is 10:30 Asia/Kolkata
(05:00 UTC), which is 00:00 EST or 01:00 EDT, and its trading day rolls at
13:30/14:30 ET. India has no daylight saving and the US does, so the New York
hour of both anchors moves twice a year — the offset is read off each bar, not
assumed. Backtesting this needs overnight data, roughly 00:00–15:30 ET.
RTH-only bars silently disable Halyard altogether; `run_halyard.py` warns when
the loaded data does not span enough hours.

New engine pieces this required:

- `backtest/portfolio.py` — multi-entry broker. Several named ids open at once,
  each closable on its own, sharing a bracket per setup. The single-position
  `Broker` cannot express `pyramiding > 0`. Netting is equivalent to Pine's
  *provided no opposing ids are ever open together*, which the seat rules
  guarantee; `net_position()` is exposed so a caller can assert it.
- `backtest/tf.py` — builds 15m candles from 1m bars on the fly, so Halyard
  trades exactly the 15m closes it would on a 15m chart while ORB/IB keep the
  1m resolution their limit fills need. Includes the catch-up path, so a bucket
  whose final bar never arrives is still closed rather than swallowed.
- `import_parquet.py` — Parquet ingestion. **pyarrow is an ingestion-only
  dependency**; once bars are cached the backtest runs on the standard library.

### Two deliberate departures from the Pine

Both are marked at their site in the source.

`_ORB_FILL_TIMING` — the Pine's own fill tracker (`or_f1`) marks a unit filled
on the same bar it places the limit, if that bar's range touches the level. An
order placed at a bar's close cannot fill during that bar. This port treats the
broker as authoritative, so a limit placed at bar N's close fills from N+1.

`_HALYARD_SEAT_LAG` — Halyard reads the ORB/IB seat flag as it stood on the
*previous* bar, because those engines run below it in the source. Reproduced
rather than fixed, since changing it would change which trades are taken.

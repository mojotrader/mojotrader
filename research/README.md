# ALN parameter research

`aln_backtest.py` is a bar-by-bar Python replica of `pinescript/aln_strategy.pine`, written so the
parameter grid can be swept off-chart. `aln_sweep.py` runs the sweep and the validation.

```
python3 aln_sweep.py MNQ_5m.csv
```

The CSV is a TradingView export with columns `time,open,high,low,close`, 5-minute bars, extended
hours included (the Asia and London ranges do not exist without overnight data). The data file is
not committed.

## What the replica models

Anchored ALN day (20:00 ET start, 16:00 ET flatten), range build inside the session window, arming
on the first side breached, limits at the 25%/50% retracement fillable only from the bar after
placement, stop at the selected retracement depth, bracket live from the bar after the fill, stop
assumed first when one bar covers both stop and target, EOD flatten. Costs: $0.50/contract/side
commission and one tick of adverse slippage on stop and market exits, none on limit fills.

Validated against two charted days (2026-07-30 and 2026-07-31): direction, entry level, contract
count and the Ext-10% target all reproduce.

## Findings on MNQ, 2026-05-03 .. 2026-08-14 (75 ALN days)

Sample is small — 75 days, and any single configuration gets 30-60 trades. Treat everything below
as a ranking of structure, not as calibrated expectations.

Robust (large effect, stable across the parameter grid and across both halves of the sample):

- **Asia: "All at 25% pullback" beats the alternatives by a wide margin** — mean total over the grid
  $5,045 vs $2,608 (split) and -$1,180 (all at 50%). The 50% entry needs a deeper pullback, fills
  far less often, and forces a tighter stop.
- **Stop at the opposite edge (100%) beats 75% and 50%** for both sessions.
- **Breakeven costs money** even with the entry-bar bug fixed: -$3,752 over the sample.
- **The hand-over rule is the single biggest lever, and its default setting is wrong here.** With
  "cancel an unfilled slot when the other range breaks", Asia's resting limits get pulled at the
  London break and Asia drops from 60 trades to 42. Setting hand-over to *Never* and allowing the
  other slot after a closed trade takes the pair from $6,647 to $11,870.
- **The double-break filter is inert.** The entry always sits between the broken edge and the
  opposite edge, so price must fill the entry before it can double-break. It never fired once.

Not established (within noise on this sample):

- Wick vs close break for Asia — marginal means are $5,842 vs $5,843.
- Every London parameter except the stop. Train-half ranking vs test-half ranking has a Spearman
  correlation of **-0.45**, i.e. optimising London on this sample is worse than not optimising.
  Asia's is +0.52.

## Honest out-of-sample

Parameters chosen on the first half by marginal effect, then run once on the untouched second half:
$63/day, against $222/day in-sample on the fitted half. Slot A held up ($3,092); slot B lost money
(-$695). One instrument, one regime, 3.5 months.

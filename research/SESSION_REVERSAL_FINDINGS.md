# Session reversal — does it have an edge?

**Data:** MNQ 1h, 21,369 bars, 2 Jan 2023 → 14 Aug 2026, 936 trading days (23h/day CME coverage).
**Windows:** the ALN profiler's own — Asia 20:00–02:00, London 02:00–08:00, NY 08:00–16:00 America/New_York.
**Scripts:** `session_reversal_study.py`, `session_edge_detail.py`, `session_target_grid.py`.

## Short version

**No edge was found.** The base rates the session profilers quote are accurate. They are base
rates, not edges — the follow-through they describe is a coin flip, and the one configuration
that showed a positive number was a single outlier trade.

## 1. The base rates are real, and they are useless on their own

| | London vs Asia | NY vs London |
| --- | --- | --- |
| driver took **a** side | 94.1% | **99.8%** |
| ...then reached the **opposite** side | **26.8%** | **49.2%** |
| excluding same-bar-both-sides days | 25.6% | 44.9% |
| pattern: Engulf / Inside / Partial Up / Partial Down | 25 / 6 / 39 / 29 % | 49 / 0 / 28 / 22 % |

The ALN profiler's hardcoded "98% NY takes Lon Hi/Lo" checks out — I measured 99.8%. But NY takes
a side of London on essentially *every* day, so that number predicts nothing. Its "45% NY takes Lon
Lo after Hi first" also checks out — I measured 42–49%. That is the coin flip.

Conditioning NY on what London did to Asia does not rescue it:

| London did | n | NY reached the far side |
| --- | --- | --- |
| Engulf | 227 | 45% |
| Inside | 53 | 49% |
| Partial Up | 354 | 54% |
| Partial Down | 265 | 46% |

Every bucket sits within a few points of 50%.

## 2. The trade

Fade the sweep. Entry at the close of the first bar that trades beyond the level **and closes back
inside** (a bar closing beyond is a break, not a sweep). Stop at that bar's extreme — already
printed, so no lookahead. Target the opposite side. Any bar touching both stop and target scores a
loss.

| | trades | win | expectancy | 95% CI | total |
| --- | --- | --- | --- | --- | --- |
| **London fades the Asia sweep** | 406 | 35.0% | **+0.074 R** | **−0.129 … +0.305** | +30.2 R |
| **NY fades the London sweep** | 415 | 37.3% | **−0.165 R** | −0.291 … −0.031 | −68.7 R |
| London goes *with* the sweep | 401 | 45.4% | −0.301 R | | |
| NY goes *with* the sweep | 453 | 41.9% | −0.309 R | | |

London's positive number does not survive contact:

```
drop the top  1 winner   -> +0.016 R   (that one trade = +23.8 R = 79% of the total)
drop the top  3 winners  -> -0.051 R   (top 3 = 168% of the total)
drop the top  5 winners  -> -0.097 R
drop the top 10 winners  -> -0.183 R
```

One trade out of 406 is four fifths of the profit. The confidence interval straddles zero. NY's
negative number, by contrast, *does* exclude zero — fading the NY sweep loses money reliably, in
all four years and in every window variant.

## 3. It is not a target-selection problem

| target | London exp R | NY exp R |
| --- | --- | --- |
| 25% back across the range | −0.310 | −0.545 |
| midpoint | −0.098 | −0.346 |
| 75% back | +0.050 | −0.222 |
| far side | +0.074 | −0.165 |
| far side + 50% | +0.083 | −0.076 |

Every partial target is *worse*. That is the signature of no directional edge: shrinking the target
shrinks the winners while the stop stays put. Fixed R-multiple targets behaved the same way — a 1R
target won only 42% of the time, below a coin flip.

## 4. The decisive test: strip the mechanics away

Hold from the sweep bar's close to the session close. No stop, no target. If there is no drift
here, nothing built on top can have an edge.

| | n | win | mean | 95% CI |
| --- | --- | --- | --- | --- |
| London after sweeping Asia | 413 | 50.1% | +1.69 pts | −5.09 … +8.37 |
| NY after sweeping London | 457 | 50.1% | −4.68 pts | −20.89 … +11.04 |

50.1% both times. For comparison, doing nothing at all — buying the session open, selling the
session close, every day, no setup:

| session | mean |
| --- | --- |
| Asia | +1.32 pts |
| London | +4.61 pts |
| NY | +5.53 pts |

The setup underperforms sitting on your hands. The only sub-bucket whose CI excluded zero was
"NY sweeps the London high → short", at −29 pts; its mirror, "NY sweeps the low → long", was
+24 pts. Symmetric, so that is the index's upward drift over 2023–2026, not a session effect.

## 5. Robustness

Four window definitions were tested — the ALN windows, ICT killzones (Asia 20–00, London 02–05,
NY 08–11), a wide overnight variant, and ALN with NY cut to 09–12. London's fade landed between
+0.056 and +0.107 R, NY's between −0.067 and −0.165 R, and going with the sweep between −0.26 and
−0.33 R in all four. The conclusion is not sensitive to where the session boundaries are drawn.

## Caveats

- **1h bars.** Intrabar order is unknown. Ambiguities were resolved against the model, so the trade
  numbers are biased pessimistic — but the pure-drift test in §4 does not depend on intrabar order
  at all, and that is the test that settles it.
- One symbol, one 3.5-year regime. `session_reversal_profiler.pine` computes all of §1 and §2 live
  on any chart so the same question can be put to another symbol or period.
- Costs are excluded from §4 and included (0.5/contract + 1 tick slippage) nowhere in §2 — adding
  them makes every row worse, not better.

## What would be worth testing next

The failure mode is that "took a side" fires on ~95–100% of days, so it carries no information. A
version of this idea with any chance would need the sweep to be *selective* — conditioned on
something that is not true almost every day. Candidates the data hints at, all unverified and all
found by slicing an already-null result, so treat them as hypotheses rather than findings:

- Shallow sweeps only (<5% of the range beyond the level) — the only depth quartile that did not
  lose on London, +0.32 R over 93 trades.
- The London range being *small* relative to its recent average, rather than the sweep being
  interesting.

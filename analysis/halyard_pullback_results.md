# Halyard MNQ 15m — pullback entry instead of market-at-close

Data: MNQ1! 15m, 2025-11-02 → 2026-09-25, default Halyard settings
(Mon long-only, RR 0.8 first / 1.0 reversal, reversal after loss or target).
342 trades (208 first, 134 reversals), matched to TradingView's own markers.
Full output: `halyard_pullback_output.txt`. Re-run: `python3 analysis/halyard_pullback.py <export.csv>`.

Pullback % = how far price comes back from the signal close toward the stop,
where close → stop = 100%. R = one unit of risk ($535 with the default sizing).

## 1. How often does price pull back X%?

| pullback | 1% | 3% | 5% | 10% | 20% | 30% | 50% |
|---|---|---|---|---|---|---|---|
| all trades | 83% | 82% | 80% | 77% | 72% | 66% | 54% |
| winners only | 73% | 70% | 67% | 64% | 55% | 46% | 27% |

There is **no 100% pullback level**. About 1 trade in 5 (69 of 342) never comes back
even 5% — and **all 69 of them were winners**. They are the trades that run straight
to target, so any pullback limit misses exactly the best trades.

## 2. Does the limit beat a market order? (total R, over the year)

Market order at the close: **+55.5R** with no slippage, +52.9R at 1 tick, +50.3R at 2 ticks, +45.3R at 4 ticks.

Limit order, stop unchanged, **target kept at the original price**:

| pullback | wait 15 min | wait 30 min | wait 60 min |
|---|---|---|---|
| 0% (limit at the close) | +47.3 | +48.3 | +48.3 |
| 5% | +43.9 | +44.8 | +46.8 |
| 10% | +41.1 | +48.8 | +50.0 |
| 20% | +39.2 | +56.2 | +52.9 |
| 30% | +42.9 | **+59.5** | +50.5 |
| 40% | +35.7 | +53.7 | +53.7 |

If the target is instead re-calculated from the limit price (same R:R), every
pullback is worse than the market order.

## Conclusion

* Small pullbacks (0–5%) **cost 7–11R**. That is like paying 3–4 ticks of slippage on every
  trade. A real MNQ market order at a 15m close usually slips 0–2 ticks, so the market order is better.
* A **20–30% pullback, target kept at the original price, 30-minute expiry** comes out
  about level with the market order (+56 to +60R vs +53R at 1 tick). It is **not reliable**:
  it wins in the second half of the data and loses in the first half, and moving to 15 or 60
  minutes wipes out the edge. Treat it as noise, not an improvement.
* Recommendation: keep the market entry at the close.

Limitations: 15m bars only, so the order of moves inside a bar is estimated with TradingView's
rule (open nearer the low → low first). Limit fills need price to trade 1 tick through. The first
and reversal trades are tested one at a time, using the baseline day's sequence.

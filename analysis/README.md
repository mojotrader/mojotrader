# Performance analysis

Tooling for turning TradingView **List of Trades** CSV exports into a joined
performance report — each strategy measured on its own, then measured again as
a single combined book.

## Usage

```bash
pip install pandas numpy

# Markdown + JSON + equity-curve CSV
python3 analysis/trade_stats.py analysis/data/*.csv

# Standalone HTML report (charts included, no external assets)
python3 analysis/build_report.py analysis/data/*.csv
```

Outputs land in `analysis/out/`.

Options (both scripts):

| Flag | Default | Meaning |
|---|---|---|
| `--commission-per-contract-side` | `1.0` | Re-price every export at one commission rate so exports run with different cost settings stay comparable |
| `--out-dir` / `--out` | `analysis/out` | Where to write |

## How the numbers are derived

- TradingView writes **two rows per trade** (entry order, exit order) carrying the
  same trade-level PnL. Trade figures come from the exit row; the entry row supplies
  entry time, price and its half of the commission.
- **Starting capital** is not in the export. It is recovered by inverting
  `Cumulative PnL USD / Cumulative PnL %`, then rounded to the nearest thousand.
- **Commission normalisation** adds back whatever commission the export charged and
  re-charges `qty × rate × 2` (both sides). Net PnL in every downstream metric is
  net of the normalised figure.
- **Daily curves** book each trade's PnL on its exit date, over a calendar-day index
  spanning all supplied exports, so the strategies share one timeline.
- **Returns are non-compounding** — PnL over a constant capital base, matching fixed
  account sizing. Sharpe and Sortino are daily, annualised by √252, at a zero
  risk-free rate.
- **Drawdown** is measured on the daily closed-trade equity curve, not intrabar, so
  it understates what the account actually saw during open positions.
- **The joined book** sums the two daily PnL streams and the two capital bases. It is
  a combined account, not an average of the two strategies.
- **Exposure** unions overlapping open-position intervals against the wall clock;
  peak concurrent size counts contracts open across both strategies at once.

## Files

| Path | What it is |
|---|---|
| `trade_stats.py` | Loading, metrics, Markdown/JSON/CSV output. `analyse()` is the importable entry point |
| `build_report.py` | Renders the HTML report from `report_template.html` |
| `report_template.html` | Page layout, styles and chart code; placeholders filled by `build_report.py` |
| `data/` | The TradingView exports the current report was built from |
| `out/` | Generated reports |

# Backtesting dashboard

A browser dashboard that backtests your strategies on **Interactive Brokers (IBKR)** data, with no
TradingView needed. The first strategy is **ORBIB** (Halyard + ORB + IB), ported line by line from the
TradingView script.

## 1. Start it

You need **Python 3.10 or newer** ([python.org/downloads](https://www.python.org/downloads/); on Windows tick
*"Add Python to PATH"* during install).

| Computer | Do this |
|---|---|
| Windows | double-click **`start_dashboard.bat`** |
| Mac | double-click **`start_dashboard.command`** (first time: right-click > **Open** > **Open**) - see the Mac steps below |
| Any | `pip install -r requirements.txt` then `streamlit run app.py` |

The first start installs the packages (a few minutes). Your browser then opens
**http://localhost:8501**. To stop it, close the black terminal window.

### Mac, step by step

1. **Install Python.** Go to [python.org/downloads/macos](https://www.python.org/downloads/macos/) and
   download the latest *macOS 64-bit universal2 installer*. Open it and click through. The Python that
   comes with the Mac is often too old.
2. **Download this project.** On GitHub, switch to this branch, click **Code > Download ZIP** and
   double-click the ZIP in Downloads. Move the `mojotrader` folder somewhere easy, such as Documents.
3. **Start it.** Open the folder, **right-click `start_dashboard.command` > Open**, then click **Open** again.
   You only need the right-click the first time, because macOS blocks files downloaded from the internet
   until you approve them. The first start installs packages for a few minutes. After that, a normal
   double-click starts it.
4. Your browser opens the dashboard at http://localhost:8501. Close the Terminal window to stop it.

If the Mac says the file *"can't be opened"* or *"is damaged"*, open **Terminal** (Cmd+Space, type
Terminal), type `bash ` (with a space after it), drag `start_dashboard.command` into the window and press
Enter.

## 2. Get price data (Data tab)

**From IBKR (recommended)**

1. Open TWS or IB Gateway and log in. The paper account is fine.
2. In TWS go to *File > Global Configuration > API > Settings*:
   - tick **Enable ActiveX and Socket Clients**
   - tick **Read-Only API** (the dashboard only reads data)
   - note the port: TWS paper **7497**, live 7496; Gateway paper 4002, live 4001
3. The account needs the real-time **CME futures market data** subscription.
4. In the dashboard's **Data** tab, choose MNQ, **1 min**, 365 days, and click **Download from IBKR**.

Good to know:

- IBKR keeps about **2 years** of history for expired futures.
- IBKR limits how fast history can be requested, so a year of 1-minute bars takes roughly 15-30 minutes.
- Contracts are stitched together automatically. The downloader rolls 8 days before expiry, and prices are
  not adjusted, so every level is a price that really traded.
- Click Download again any day to add only the new bars.

**From a CSV file:** TradingView's *Export chart data*, Databento OHLCV files or any CSV with
time/open/high/low/close columns.

**Demo data:** fake random prices, only for learning the dashboard.

## 3. Run a backtest

1. Sidebar: pick the dataset, the date range and **1-minute bars** (ORBIB is designed for a 1m chart).
2. Adjust the strategy settings. The defaults are the same as the TradingView script's defaults.
3. Click **Run backtest**. One year of 1-minute bars takes about 10-20 seconds.

| Tab | What you see |
|---|---|
| Results | net profit, profit factor, win rate, drawdown, Sharpe; equity curve per setup (Halyard / ORB / IB); drawdown; daily P&L |
| Trades | every trade, filterable, downloadable as CSV |
| Chart | pick a day and see its candles with the ranges, entry / stop / target levels and every fill |
| Breakdown | P&L by weekday, month, setup, entry type, exit reason, hour, long/short |
| Optimize | test many values of one or two settings at once (with a heat map) |
| Data | download from IBKR, import CSV, list datasets |

## 4. Comparing with TradingView

The default costs match the TradingView script: $0.50/contract, 1 tick slippage, limits fill on touch,
orders on bar close and $150,000 starting capital. Run both on the **same dates** with 1-minute bars and the
trade lists should line up closely. Small differences are expected:

- **Different data.** IBKR bars and TradingView's CME feed are not tick-for-tick identical, and the roll
  dates can differ.
- **Brackets start sooner.** A stop/target here is live the moment the entry fills. Pine attaches it one
  bar later, which on a 1m chart only matters in a very fast minute.
- **Positions are tracked per order,** like separate bracket orders at IBKR, instead of Pine's one netted
  position. The seat rules keep setups from overlapping, so this almost never matters.
- **Only the modes the script ships with are ported.** For Halyard that means stop = range edge + buffer and
  target = R:R. The time stop, trend filter, ATR stops and the probability table are not included.

## 5. Make the test stricter

In **Contract & costs**:

- **"Must trade 1 tick through"**: a limit only fills if price trades past it. This is closer to real
  queue behaviour.
- **"Pessimistic"**: when a stop and a target are both inside one bar, assume the stop was hit.
- **Commission**: set it to your real IBKR rate (about $0.62 for micros, $2.25 for minis).

A strategy that is still profitable with these settings is much more trustworthy.

## How the engine works (short version)

- Each bar goes through: market orders, then resting limits and stops along the bar's price path, then
  the strategy runs at the bar close.
- The price path inside a bar is assumed to be open > nearest extreme > other extreme > close. This is the
  same rule TradingView uses.
- The code lives in `backtester/`:
  - `engine.py`: the broker simulation
  - `strategies/orbib.py`: the strategy
  - `data.py`: data storage and CSV import
  - `ibkr.py`: the IBKR downloader
  - `metrics.py`: the statistics
- Tests: `python -m pytest`

## Adding another strategy

Create a class in `backtester/strategies/` with:

- `name`
- `params_spec`: a list of `Param`
- `on_bar(ctx, bar)`

Then register it in `backtester/strategies/__init__.py`, and it appears in the dashboard with its
settings. `ctx` is the simulated broker:

- `place_entry` (limit with stop/target bracket), `market_entry`
- `cancel`, `set_exits`, `close`, `close_all`
- `position()`, `equity`
- `bar_fills` / `bar_exits` for what happened this bar

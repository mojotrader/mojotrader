"""Download historical futures bars from Interactive Brokers (TWS or IB Gateway) into ``data/``.

Usage (TWS / Gateway must be running and logged in, with API access enabled):
    python -m backtester.ibkr --symbol MNQ --bar "1 min" --days 365 --port 7497

Ports: TWS paper 7497, TWS live 7496, Gateway paper 4002, Gateway live 4001.

How it works
  * Finds every quarterly contract (incl. expired ones - IBKR keeps roughly the last 2 years).
  * Stitches them into one continuous series, rolling 8 days before expiry (the Thursday before the
    third-Friday expiry, when volume moves to the next contract). Prices are NOT back-adjusted, so every
    level is a price that really traded - right for intraday range strategies.
  * Downloads in chunks with pauses to respect IBKR's pacing limits (~60 requests per 10 minutes), saving
    after every contract. Re-running only fetches what is missing, so a daily re-run keeps data current.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .data import TZ, dataset_path, load_bars, merge_bars, save_bars

BAR_SIZES = {"1 min": 5, "2 mins": 10, "3 mins": 10, "5 mins": 20, "15 mins": 30, "30 mins": 60, "1 hour": 120}
ROLL_DAYS_BEFORE_EXPIRY = 8


@dataclass
class Window:
    contract: object
    local_symbol: str
    start: pd.Timestamp   # tz-aware (ET)
    end: pd.Timestamp


def dataset_name(symbol: str, bar_size: str) -> str:
    return f"{symbol}_{bar_size.replace(' ', '').replace('mins', 'min')}"


def roll_boundary(expiry: str) -> pd.Timestamp:
    """The moment we switch to the next contract: 18:00 ET the evening before the roll day."""
    exp = pd.Timestamp(datetime.strptime(expiry[:8], "%Y%m%d"))
    roll_day = exp - pd.Timedelta(days=ROLL_DAYS_BEFORE_EXPIRY)
    return (roll_day - pd.Timedelta(days=1)).replace(hour=18).tz_localize(TZ)


def plan_windows(contracts: list[tuple[object, str, str]], start: pd.Timestamp, end: pd.Timestamp) -> list[Window]:
    """``contracts`` = [(contract, local_symbol, expiry 'YYYYMMDD')]. Returns the slice of time each one covers."""
    contracts = sorted(contracts, key=lambda c: c[2])
    out = []
    prev_end = None
    for con, local, expiry in contracts:
        w_end = roll_boundary(expiry)
        w_start = prev_end if prev_end is not None else w_end - pd.Timedelta(days=100)
        prev_end = w_end
        s, e = max(w_start, start), min(w_end, end)
        if s < e:
            out.append(Window(con, local, s, e))
    return out


def _log(msg: str) -> None:
    print(msg, flush=True)


def fetch_window(ib, w: Window, bar_size: str, chunk_days: int, pause: float, now: pd.Timestamp) -> pd.DataFrame:
    """Download one contract's window, newest chunk first."""
    errors: list[tuple[int, str]] = []

    def on_error(req_id, code, msg, contract):
        errors.append((code, msg))

    ib.errorEvent += on_error
    frames = []
    cursor = w.end
    days = chunk_days
    try:
        while cursor > w.start:
            errors.clear()
            end_str = "" if cursor >= now else cursor.tz_convert("UTC").strftime("%Y%m%d-%H:%M:%S")
            bars = ib.reqHistoricalData(w.contract, endDateTime=end_str, durationStr=f"{days} D",
                                        barSizeSetting=bar_size, whatToShow="TRADES", useRTH=False,
                                        formatDate=2, timeout=180)
            text = " ".join(m for _, m in errors).lower()
            if not bars and "pacing" in text:
                _log("  IBKR pacing limit hit - waiting 60s...")
                time.sleep(60)
                continue
            if not bars and errors and days > 1 and "no data" not in text:
                days = max(1, days // 2)
                _log(f"  request refused ({errors[-1][0]}: {errors[-1][1]}) - retrying with {days}-day chunks")
                time.sleep(pause)
                continue
            if bars:
                df = pd.DataFrame({"open": [b.open for b in bars], "high": [b.high for b in bars],
                                   "low": [b.low for b in bars], "close": [b.close for b in bars],
                                   "volume": [float(b.volume) for b in bars]},
                                  index=pd.DatetimeIndex([pd.Timestamp(b.date) for b in bars]))
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                df.index = df.index.tz_convert(TZ)
                frames.append(df)
                _log(f"  {w.local_symbol}: {len(df):>6} bars  {df.index[0]:%Y-%m-%d %H:%M} -> {df.index[-1]:%Y-%m-%d %H:%M}")
            cursor = cursor - pd.Timedelta(days=days)
            time.sleep(pause)
    finally:
        ib.errorEvent -= on_error
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[(df.index >= w.start) & (df.index < w.end)]
    df["contract"] = w.local_symbol
    return df


def download(symbol: str, bar_size: str = "1 min", days: int = 365, host: str = "127.0.0.1", port: int = 7497,
             client_id: int = 17, exchange: str = "CME", pause: float = 10.0, full: bool = False, ib=None) -> str:
    from ib_async import IB, Future

    name = dataset_name(symbol, bar_size)
    now = pd.Timestamp.now(tz=TZ)
    start = now - pd.Timedelta(days=days)
    existing = load_bars(name) if dataset_path(name).exists() and not full else None
    if existing is not None and len(existing):
        start = max(start, existing.index[-1] - pd.Timedelta(hours=6))
        _log(f"Existing data up to {existing.index[-1]:%Y-%m-%d %H:%M} - fetching only newer bars.")

    own = ib is None
    if own:
        ib = IB()
        _log(f"Connecting to TWS/Gateway at {host}:{port} (client id {client_id})...")
        ib.connect(host, port, clientId=client_id, readonly=True, timeout=20)
    try:
        details = ib.reqContractDetails(Future(symbol=symbol, exchange=exchange, includeExpired=True))
        cons = []
        for d in details:
            c = d.contract
            c.includeExpired = True
            cons.append((c, c.localSymbol, c.lastTradeDateOrContractMonth))
        if not cons:
            raise RuntimeError(f"IBKR returned no {symbol} futures contracts on {exchange}.")
        windows = plan_windows(cons, start, now)
        _log(f"{len(windows)} contract(s) to download: " + ", ".join(w.local_symbol for w in windows))
        data = existing
        for n, w in enumerate(windows, 1):
            _log(f"PROGRESS {n - 1}/{len(windows)} {w.local_symbol} {w.start:%Y-%m-%d} -> {w.end:%Y-%m-%d}")
            df = fetch_window(ib, w, bar_size, BAR_SIZES.get(bar_size, 5), pause, now)
            if len(df):
                data = merge_bars(data, df)
                save_bars(data, name)
                _log(f"  saved {len(data):,} bars total to data/{name}.parquet")
        _log(f"PROGRESS {len(windows)}/{len(windows)} done")
        if data is None or data.empty:
            raise RuntimeError("No bars were downloaded. Check your CME market-data subscription.")
        if len(windows) and windows[0].start > start + pd.Timedelta(days=3):
            _log(f"NOTE: IBKR had no older contracts - data starts {windows[0].start:%Y-%m-%d}.")
        return name
    finally:
        if own:
            ib.disconnect()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download IBKR futures bars into data/")
    ap.add_argument("--symbol", default="MNQ")
    ap.add_argument("--bar", default="1 min", choices=list(BAR_SIZES))
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=17)
    ap.add_argument("--exchange", default="CME")
    ap.add_argument("--pause", type=float, default=10.0, help="seconds between requests (pacing)")
    ap.add_argument("--full", action="store_true", help="re-download everything instead of only new bars")
    a = ap.parse_args(argv)
    try:
        name = download(a.symbol, a.bar, a.days, a.host, a.port, a.client_id, a.exchange, a.pause, a.full)
        _log(f"DONE {name}")
    except Exception as e:  # noqa: BLE001 - show any failure to the user
        _log(f"ERROR {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

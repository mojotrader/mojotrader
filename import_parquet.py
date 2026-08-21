#!/usr/bin/env python3
"""Import a Parquet OHLCV file into the local CSV cache.

    python3 import_parquet.py path/to/data.parquet --symbol NQ.F --resolution 1m

Parquet is a columnar binary format, so reading it needs pyarrow:

    pip install pyarrow

That dependency is for INGESTION ONLY. Once the bars are in the cache the
backtest itself runs on the standard library alone, so the harness stays
installable-free on any machine that just wants to run a strategy.

Column names are detected rather than assumed, so exports from ClickHouse,
DuckDB, pandas and the usual vendors all land without a schema flag.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from backtest.bars import Bar, save_bars, cache_path

TS_NAMES = ("ts", "timestamp", "time", "datetime", "date", "t")
OHLCV = {
    "open": ("open", "o", "Open"),
    "high": ("high", "h", "High"),
    "low": ("low", "l", "Low"),
    "close": ("close", "c", "Close"),
    "volume": ("volume", "v", "Volume", "vol"),
}


def pick(available: list[str], candidates: tuple[str, ...], what: str) -> str:
    lower = {c.lower(): c for c in available}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    raise SystemExit(f"error: no {what} column found. Columns present: {available}")


def to_utc(value) -> datetime:
    """Normalise a timestamp cell to timezone-aware UTC."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None \
            else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", help="the .parquet file")
    p.add_argument("--symbol", help="cache symbol (defaults to the file's symbol column, else the filename)")
    p.add_argument("--resolution", default="1m")
    p.add_argument("--symbol-filter", help="keep only rows whose symbol column matches this")
    args = p.parse_args()

    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("error: pyarrow is required to read Parquet.\n    pip install pyarrow", file=sys.stderr)
        return 2

    table = pq.read_table(args.path)
    cols = table.column_names
    ts_col = pick(cols, TS_NAMES, "timestamp")
    mapped = {k: pick(cols, v, k) for k, v in OHLCV.items() if k != "volume"}
    try:
        mapped["volume"] = pick(cols, OHLCV["volume"], "volume")
    except SystemExit:
        mapped["volume"] = None  # volume is optional; VWAP falls back to 1 per bar

    symbol = args.symbol
    if symbol is None and "symbol" in cols:
        distinct = set(table.column("symbol").to_pylist())
        if args.symbol_filter is None and len(distinct) > 1:
            raise SystemExit(f"error: file holds several symbols {sorted(distinct)}; "
                             f"pass --symbol-filter to choose one")
        symbol = args.symbol_filter or next(iter(distinct))
    if symbol is None:
        symbol = args.path.rsplit("/", 1)[-1].split(".")[0]

    ts = table.column(ts_col).to_pylist()
    o = table.column(mapped["open"]).to_pylist()
    h = table.column(mapped["high"]).to_pylist()
    lo = table.column(mapped["low"]).to_pylist()
    c = table.column(mapped["close"]).to_pylist()
    v = table.column(mapped["volume"]).to_pylist() if mapped["volume"] else [0.0] * len(ts)
    sym = table.column("symbol").to_pylist() if ("symbol" in cols and args.symbol_filter) else None

    bars, skipped = [], 0
    for i in range(len(ts)):
        if sym is not None and sym[i] != args.symbol_filter:
            continue
        if None in (ts[i], o[i], h[i], lo[i], c[i]):
            skipped += 1
            continue
        bars.append(Bar(to_utc(ts[i]), float(o[i]), float(h[i]),
                        float(lo[i]), float(c[i]), float(v[i] or 0.0)))

    if not bars:
        print("error: no usable rows.", file=sys.stderr)
        return 1

    save_bars(symbol, args.resolution, bars)
    print(f"imported {len(bars):,} bars as {symbol} {args.resolution}"
          + (f"  ({skipped:,} skipped for null fields)" if skipped else ""))
    print(f"range    {bars[0].ts} .. {bars[-1].ts}")
    print(f"file     {cache_path(symbol, args.resolution)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

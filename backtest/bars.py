"""OHLCV bars, the local CSV cache, and New York session helpers.

Bars are stored one CSV per (symbol, resolution) under ``data/``. CSV keeps the
cache greppable and dependency-free; at intraday resolutions over a few years
the files stay small enough that load time is dominated by parsing, not IO.

Timestamps are the bar's OPEN time, timezone-aware, always stored as UTC epoch
seconds. Session logic converts to the exchange timezone on demand rather than
storing local time, so a cache file is not invalidated by a DST boundary.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV candle. ``ts`` is the bar's open time in UTC."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def hlc3(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    def ny(self) -> datetime:
        """This bar's open time in exchange-local (New York) time."""
        return self.ts.astimezone(NY)

    def ny_minute(self) -> int:
        """Minutes since local midnight, e.g. 09:30 -> 570."""
        t = self.ny()
        return t.hour * 60 + t.minute

    def ny_date(self) -> date:
        return self.ny().date()


def cache_path(symbol: str, resolution: str) -> str:
    safe = symbol.replace("/", "_").replace(":", "_")
    return os.path.join(DATA_DIR, f"{safe}_{resolution}.csv")


def save_bars(symbol: str, resolution: str, bars: list[Bar]) -> str:
    """Write bars to the cache, sorted and de-duplicated by timestamp."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = cache_path(symbol, resolution)
    merged = _dedupe(bars)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for b in merged:
            w.writerow([
                int(b.ts.timestamp()),
                _fmt(b.open), _fmt(b.high), _fmt(b.low), _fmt(b.close), _fmt(b.volume),
            ])
    return path


def load_bars(symbol: str, resolution: str) -> list[Bar]:
    """Read bars from the cache. Raises FileNotFoundError if not fetched yet."""
    path = cache_path(symbol, resolution)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No cached data at {path}. Fetch it first:\n"
            f"    python3 fetch_data.py --symbol {symbol} --resolution {resolution} "
            f"--start YYYY-MM-DD --end YYYY-MM-DD"
        )
    out: list[Bar] = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out.append(Bar(
                ts=datetime.fromtimestamp(int(row["ts"]), tz=timezone.utc),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"] or 0.0),
            ))
    out.sort(key=lambda b: b.ts)
    return out


def merge_into_cache(symbol: str, resolution: str, new_bars: list[Bar]) -> tuple[int, int]:
    """Merge freshly fetched bars into any existing cache file.

    Returns ``(added, total)``. Re-fetching an overlapping window is safe: a
    later fetch of the same timestamp replaces the earlier bar, which is what
    you want when a provider revises recent candles.
    """
    try:
        existing = load_bars(symbol, resolution)
    except FileNotFoundError:
        existing = []
    before = len(existing)
    combined = _dedupe(existing + new_bars)
    save_bars(symbol, resolution, combined)
    return len(combined) - before, len(combined)


def _dedupe(bars: list[Bar]) -> list[Bar]:
    """Sort by timestamp; on collision the later entry in the list wins."""
    by_ts: dict[datetime, Bar] = {}
    for b in bars:
        by_ts[b.ts] = b
    return [by_ts[k] for k in sorted(by_ts)]


def _fmt(x: float) -> str:
    """Trim float noise without losing tick precision."""
    return f"{x:.10g}"


def in_session(bar: Bar, start_min: int, end_min: int) -> bool:
    """True when the bar's open falls in [start, end) local minutes, weekdays only."""
    t = bar.ny()
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return start_min <= m < end_min


def parse_hhmm(s: str) -> int:
    """'0930' or '09:30' -> 570."""
    s = s.replace(":", "").strip()
    return int(s[:2]) * 60 + int(s[2:])

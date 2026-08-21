#!/usr/bin/env python3
"""Generate synthetic RTH bars so the pipeline can be exercised without an API key.

    python3 make_sample_data.py --symbol SAMPLE --days 120

The output is a seeded random walk with a plausible intraday volatility smile.
It is NOT market data and carries no real edge -- use it to verify the plumbing
end to end, then replace it with a real fetch. Results from sample data mean
nothing about a strategy's merit.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

from backtest.bars import Bar, NY, save_bars, cache_path


def session_bars(day: datetime, start_px: float, rng: random.Random,
                 step_min: int = 5) -> list[Bar]:
    """One 09:30-16:00 session of bars."""
    bars = []
    px = start_px
    open_min, close_min = 570, 960
    n = (close_min - open_min) // step_min

    for i in range(n):
        minute = open_min + i * step_min
        # Volatility is highest at the open, tapers midday, lifts into the close.
        frac = i / max(n - 1, 1)
        vol = 0.9 * (1.0 - frac) ** 2 + 0.25 + 0.4 * frac ** 3
        drift = rng.gauss(0, 1) * vol
        o = px
        c = o + drift
        wick = abs(rng.gauss(0, 1)) * vol * 0.8
        h = max(o, c) + wick
        l = min(o, c) - abs(rng.gauss(0, 1)) * vol * 0.8
        v = max(1.0, rng.gauss(1200, 300) * (1.6 - frac))

        # ``day`` is already New York local, so replacing the clock time and
        # converting to UTC handles DST without any offset arithmetic.
        local = day.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)
        bars.append(Bar(ts=local.astimezone(timezone.utc),
                        open=round(o, 2), high=round(h, 2), low=round(l, 2),
                        close=round(c, 2), volume=round(v)))
        px = c
    return bars, px


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="SAMPLE")
    p.add_argument("--resolution", default="5m")
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--start-price", type=float, default=18000.0)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    rng = random.Random(args.seed)
    px = args.start_price
    all_bars: list[Bar] = []

    day = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    day -= timedelta(days=args.days * 2)

    made = 0
    while made < args.days:
        if day.weekday() < 5:
            # Overnight gap between sessions.
            px += rng.gauss(0, 6)
            bars, px = session_bars(day, px, rng)
            all_bars.extend(bars)
            made += 1
        day += timedelta(days=1)

    save_bars(args.symbol, args.resolution, all_bars)
    print(f"wrote {len(all_bars):,} synthetic bars over {args.days} sessions")
    print(f"file  {cache_path(args.symbol, args.resolution)}")
    print("NOTE: synthetic data. Use it to test the pipeline, never to judge a strategy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

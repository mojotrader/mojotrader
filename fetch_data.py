#!/usr/bin/env python3
"""Pull candles from the LSE vault API into the local CSV cache.

    export LSE_API_KEY='lse_live_...'
    python3 fetch_data.py --probe                       # inspect the real API shape
    python3 fetch_data.py --symbol MNQ1! --resolution 5m --start 2024-01-01 --end 2024-12-31

Re-running an overlapping window is safe -- bars merge by timestamp and the
newer fetch wins, so a revised candle replaces the stale one.
"""

from __future__ import annotations

import argparse
import json
import sys

from backtest.bars import merge_into_cache, cache_path
from backtest.lse import LSEClient, LSEError, ENDPOINTS


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="MNQ1!")
    p.add_argument("--resolution", default="5m", help="provider resolution string, e.g. 1m 5m 15m 1h")
    p.add_argument("--start", help="YYYY-MM-DD")
    p.add_argument("--end", help="YYYY-MM-DD")
    p.add_argument("--probe", action="store_true",
                   help="print the raw response for catalog and candles, then exit")
    p.add_argument("--key", help="API key (defaults to $LSE_API_KEY)")
    args = p.parse_args()

    try:
        client = LSEClient(api_key=args.key)
    except LSEError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.probe:
        return probe(client, args)

    try:
        bars = client.candles(args.symbol, args.resolution, args.start, args.end)
    except LSEError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not bars:
        print("No bars returned. Run --probe to see the raw response; the endpoint "
              "path or parameter names in backtest/lse.py may need correcting.", file=sys.stderr)
        return 1

    added, total = merge_into_cache(args.symbol, args.resolution, bars)
    print(f"fetched {len(bars):,} bars  |  {added:,} new  |  {total:,} cached")
    print(f"range   {bars[0].ts.date()} .. {bars[-1].ts.date()}")
    print(f"file    {cache_path(args.symbol, args.resolution)}")
    return 0


def probe(client: LSEClient, args) -> int:
    """Show what the API actually returns, so paths can be fixed from evidence."""
    print("Endpoint paths currently configured (backtest/lse.py ENDPOINTS):")
    for name, path in ENDPOINTS.items():
        print(f"  {name:<8} {client.base_url}{path}")
    print()

    for label, call in (
        ("catalog", lambda: client.request(ENDPOINTS["catalog"])),
        ("candles", lambda: client.request(ENDPOINTS["candles"], {
            "symbol": args.symbol, "resolution": args.resolution,
            "start": args.start, "end": args.end})),
    ):
        print(f"--- {label} ---")
        try:
            raw = call()
            text = json.dumps(raw, indent=2, default=str)
            print(text[:2000] + ("\n... truncated" if len(text) > 2000 else ""))
        except LSEError as e:
            print(f"failed: {e}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

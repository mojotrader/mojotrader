#!/usr/bin/env python3
"""Backtest the combined Halyard + ORB + IB strategy.

    python3 run_halyard.py --symbol NQ.F --resolution 1m
    python3 run_halyard.py --isolate            # each setup on its own, then combined
    python3 run_halyard.py --by-year --trades-csv out/halyard.csv

Halyard needs overnight bars (its range candle is 00:00 EST / 01:00 EDT), so
feed this 24-hour data at 1m. RTH-only bars silently disable Halyard entirely.
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import sys
from datetime import datetime, timezone

from backtest.bars import load_bars, NY
from backtest.contracts import spec
from backtest.metrics import compute, format_table, Stats
from backtest.strategies.halyard_orbib import HalyardORBIB, Config, run_combined

SETUP_OF = {"HAL": "Halyard", "ORL": "ORB", "ORS": "ORB", "IBL": "IB", "IBS": "IB"}


def setup_name(tag: str) -> str:
    if tag.startswith("HAL"):
        return "Halyard"
    return "ORB" if tag.startswith("OR") else "IB"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="NQ.F")
    p.add_argument("--resolution", default="1m")
    p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--point-value", type=float, help="override the contract point value")
    p.add_argument("--isolate", action="store_true", help="also run each setup alone")
    p.add_argument("--by-year", action="store_true")
    p.add_argument("--trades-csv")
    args = p.parse_args()

    bars = load_bars(args.symbol, args.resolution)
    if args.start:
        lo = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        bars = [b for b in bars if b.ts >= lo]
    if args.end:
        hi = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        bars = [b for b in bars if b.ts <= hi]
    if len(bars) < 2:
        print("error: not enough bars.", file=sys.stderr)
        return 2

    pv = args.point_value if args.point_value else spec(args.symbol.split(".")[0])["point_value"]
    sessions = len({b.ny_date() for b in bars})
    hours = len({(b.ny().hour) for b in bars})
    print(f"{args.symbol} {args.resolution} | {len(bars):,} bars | "
          f"{bars[0].ts.date()} .. {bars[-1].ts.date()} | {sessions} sessions")
    print(f"contract: 1 pt = ${pv:g} | overnight coverage: {hours}/24 NY hours"
          + ("" if hours > 20 else "  <-- WARNING: Halyard needs overnight bars"))
    print(f"costs: ${Config().commission_per_contract}/contract/side + "
          f"{Config().slippage_ticks} tick slippage\n")

    runs = [("combined", True, True, True)]
    if args.isolate:
        runs += [("Halyard only", True, False, False),
                 ("ORB only", False, True, False),
                 ("IB only", False, False, True)]

    rows: list[tuple[str, Stats]] = []
    combined_trades = []
    for label, hal, orb, ib in runs:
        cfg = Config(point_value=pv, run_halyard=hal, run_orb=orb, run_ib=ib)
        strat = HalyardORBIB(cfg)
        trades, _ = run_combined(strat, bars)
        rows.append((label, compute(trades, pv)))
        if label == "combined":
            combined_trades = trades

    print(format_table(rows))

    # per-setup split of the combined curve
    print("\nCombined curve, split by setup (interaction included -- these are the")
    print("trades each setup actually got after the seat rules, not standalone):")
    groups: dict[str, list] = collections.defaultdict(list)
    for t in combined_trades:
        groups[setup_name(t.entry_tag)].append(t)
    print(format_table([(k, compute(v, pv)) for k, v in sorted(groups.items())]))

    if args.by_year:
        print("\nBy calendar year (combined):")
        per_year: dict[int, list] = collections.defaultdict(list)
        for t in combined_trades:
            per_year[t.entry_ts.astimezone(NY).year].append(t)
        print(format_table([(str(y), compute(v, pv)) for y, v in sorted(per_year.items())]))

    st = compute(combined_trades, pv)
    print(f"\nExits: {dict(st.exit_breakdown)}")
    print(f"Commission paid: ${st.commission_paid:,.0f} on {st.trades} fills")
    print("\nNet is after commission and slippage. Drawdown is closed-trade only,")
    print("so intrabar heat was worse than shown.")

    if args.trades_csv:
        os.makedirs(os.path.dirname(args.trades_csv) or ".", exist_ok=True)
        with open(args.trades_csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["setup", "entry_id", "side", "qty", "entry_ts", "entry_price",
                        "exit_ts", "exit_price", "exit_reason", "points", "gross", "comm", "net"])
            for t in combined_trades:
                gross = t.points * t.qty * pv
                w.writerow([setup_name(t.entry_tag), t.entry_tag, t.side.name, t.qty,
                            t.entry_ts.isoformat(), f"{t.entry_price:.2f}",
                            t.exit_ts.isoformat(), f"{t.exit_price:.2f}", t.exit_reason,
                            f"{t.points:.2f}", f"{gross:.2f}", f"{t.commission:.2f}",
                            f"{gross-t.commission:.2f}"])
        print(f"\ntrades -> {args.trades_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Run several strategies over the same cached bars and compare them.

    python3 run_backtest.py --symbol MNQ1! --resolution 5m
    python3 run_backtest.py --strategies orb_fade,vwap_reversion --start 2024-06-01
    python3 run_backtest.py --detail --trades-csv out/trades.csv

Every strategy sees identical data and identical cost assumptions, which is the
only way the ranking means anything. Costs are on by default: commission per
contract per side plus one tick of slippage on market and stop fills.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

from backtest.bars import load_bars, Bar
from backtest.contracts import spec
from backtest.metrics import compute, format_table, Stats
from backtest.strategy import run
from backtest.strategies import REGISTRY


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def slice_bars(bars: list[Bar], start: datetime | None, end: datetime | None) -> list[Bar]:
    if start:
        bars = [b for b in bars if b.ts >= start]
    if end:
        bars = [b for b in bars if b.ts <= end]
    return bars


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="MNQ1!")
    p.add_argument("--resolution", default="5m")
    p.add_argument("--strategies", default="all",
                   help=f"comma-separated, or 'all'. Available: {', '.join(REGISTRY)}")
    p.add_argument("--start", help="YYYY-MM-DD")
    p.add_argument("--end", help="YYYY-MM-DD")
    p.add_argument("--detail", action="store_true", help="per-strategy breakdown")
    p.add_argument("--trades-csv", help="write every trade to this file")
    p.add_argument("--json", dest="json_out", help="write stats as JSON to this file")
    args = p.parse_args()

    names = list(REGISTRY) if args.strategies == "all" else \
        [n.strip() for n in args.strategies.split(",") if n.strip()]
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        print(f"error: unknown strategy {unknown}. Available: {', '.join(REGISTRY)}", file=sys.stderr)
        return 2

    try:
        bars = load_bars(args.symbol, args.resolution)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    bars = slice_bars(bars, parse_date(args.start), parse_date(args.end))
    if len(bars) < 2:
        print("error: not enough bars in the selected range.", file=sys.stderr)
        return 2

    contract = spec(args.symbol)
    sessions = len({b.ny_date() for b in bars})
    print(f"{args.symbol} {args.resolution}  |  {len(bars):,} bars  |  "
          f"{bars[0].ts.date()} .. {bars[-1].ts.date()}  |  {sessions} sessions")
    print(f"contract: {contract['desc']}  (1 pt = ${contract['point_value']:g}, "
          f"tick {contract['tick_size']})\n")

    rows: list[tuple[str, Stats]] = []
    all_trades = []
    for name in names:
        strat = REGISTRY[name]()
        strat.point_value = contract["point_value"]
        strat.tick_size = contract["tick_size"]
        trades, _ = run(strat, bars)
        stats = compute(trades, contract["point_value"])
        rows.append((strat.describe(), stats))
        all_trades.extend((name, t) for t in trades)
        if args.detail:
            print_detail(strat.describe(), stats)

    print(format_table(rows))
    print("\nNet P&L is after commission and slippage. Drawdown is measured on "
          "closed trades, so intrabar heat was worse than shown.")

    if args.trades_csv:
        write_trades(args.trades_csv, all_trades, contract["point_value"])
        print(f"\ntrades -> {args.trades_csv}")
    if args.json_out:
        os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
        with open(args.json_out, "w") as fh:
            json.dump({name: s.as_dict() for name, s in rows}, fh, indent=2, default=str)
        print(f"stats  -> {args.json_out}")
    return 0


def print_detail(name: str, s: Stats) -> None:
    pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
    print(f"── {name}")
    if s.trades == 0:
        print("   no trades\n")
        return
    print(f"   trades {s.trades}   wins {s.wins}   losses {s.losses}   win rate {s.win_rate:.1f}%")
    print(f"   net ${s.net_pnl:,.2f}   gross +${s.gross_profit:,.0f} / -${s.gross_loss:,.0f}   PF {pf}")
    print(f"   expectancy ${s.expectancy:.2f}/trade   avg win ${s.avg_win:,.0f}   avg loss ${s.avg_loss:,.0f}")
    print(f"   max DD ${s.max_drawdown:,.0f}   max consec losses {s.max_consec_losses}   Sharpe {s.sharpe:.2f}")
    print(f"   best ${s.largest_win:,.0f}   worst ${s.largest_loss:,.0f}   avg hold {s.avg_bars_held:.1f} bars")
    print(f"   commission ${s.commission_paid:,.0f}   exits {s.exit_breakdown}\n")


def write_trades(path: str, tagged, point_value: float) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["strategy", "side", "qty", "entry_ts", "entry_price",
                    "exit_ts", "exit_price", "exit_reason", "points",
                    "gross_pnl", "commission", "net_pnl", "bars_held"])
        for name, t in tagged:
            gross = t.points * t.qty * point_value
            w.writerow([
                name, t.side.name, t.qty,
                t.entry_ts.isoformat(), f"{t.entry_price:.4f}",
                t.exit_ts.isoformat(), f"{t.exit_price:.4f}",
                t.exit_reason, f"{t.points:.4f}",
                f"{gross:.2f}", f"{t.commission:.2f}", f"{gross - t.commission:.2f}",
                t.bars_held,
            ])


if __name__ == "__main__":
    sys.exit(main())

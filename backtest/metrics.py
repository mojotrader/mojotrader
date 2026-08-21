"""Performance statistics for a list of closed trades.

Everything here is computed from closed trades only, so the equity curve is a
step function and drawdown is measured trade-to-trade rather than intrabar.
Intrabar drawdown is always worse; treat these figures as the optimistic bound
on how much heat a strategy put you through.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, asdict

from .engine import Trade


@dataclass(slots=True)
class Stats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    max_drawdown: float = 0.0
    max_consec_losses: int = 0
    avg_bars_held: float = 0.0
    total_points: float = 0.0
    commission_paid: float = 0.0
    sharpe: float = 0.0
    exit_breakdown: dict[str, int] | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def compute(trades: list[Trade], point_value: float) -> Stats:
    """Roll a trade list into a :class:`Stats`.

    ``point_value`` converts points into currency, so it must match the
    contract actually traded (MNQ 2, NQ 20, MES 5, ES 50).
    """
    s = Stats()
    s.exit_breakdown = {}
    if not trades:
        return s

    pnls = []
    for t in trades:
        pnl = t.points * t.qty * point_value - t.commission
        pnls.append(pnl)
        s.total_points += t.points * t.qty
        s.commission_paid += t.commission
        s.exit_breakdown[t.exit_reason] = s.exit_breakdown.get(t.exit_reason, 0) + 1

    s.trades = len(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    s.wins, s.losses = len(wins), len(losses)
    s.win_rate = s.wins / s.trades * 100.0
    s.net_pnl = sum(pnls)
    s.gross_profit = sum(wins)
    s.gross_loss = abs(sum(losses))
    # An undefined profit factor (no losing trades) reads better as infinity
    # than as a divide-by-zero crash, but flag it rather than printing 0.
    s.profit_factor = (s.gross_profit / s.gross_loss) if s.gross_loss > 0 else float("inf")
    s.expectancy = s.net_pnl / s.trades
    s.avg_win = (sum(wins) / len(wins)) if wins else 0.0
    s.avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    s.largest_win = max(pnls)
    s.largest_loss = min(pnls)
    s.avg_bars_held = sum(t.bars_held for t in trades) / len(trades)

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    s.max_drawdown = max_dd

    streak = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        s.max_consec_losses = max(s.max_consec_losses, streak)

    s.sharpe = _sharpe(trades, pnls)
    return s


def _sharpe(trades: list[Trade], pnls: list[float]) -> float:
    """Annualised Sharpe from daily P&L, zero risk-free rate.

    Daily rather than per-trade: a per-trade Sharpe is not comparable between
    strategies that trade at different frequencies.
    """
    by_day: dict = defaultdict(float)
    for t, p in zip(trades, pnls):
        by_day[t.exit_ts.date()] += p
    daily = list(by_day.values())
    if len(daily) < 2:
        return 0.0
    mean = sum(daily) / len(daily)
    var = sum((d - mean) ** 2 for d in daily) / (len(daily) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return (mean / sd) * math.sqrt(252)


def format_table(rows: list[tuple[str, Stats]]) -> str:
    """Render one row per strategy, ranked by net P&L."""
    if not rows:
        return "No results."

    headers = ["Strategy", "Trades", "Win%", "Net P&L", "PF", "Expect", "MaxDD", "Sharpe", "MaxLoss"]
    body = []
    for name, s in sorted(rows, key=lambda r: r[1].net_pnl, reverse=True):
        pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
        body.append([
            name,
            str(s.trades),
            f"{s.win_rate:.1f}",
            f"{s.net_pnl:,.0f}",
            pf,
            f"{s.expectancy:.2f}",
            f"{s.max_drawdown:,.0f}",
            f"{s.sharpe:.2f}",
            f"{s.largest_loss:,.0f}",
        ])

    widths = [max(len(headers[i]), max((len(r[i]) for r in body), default=0)) for i in range(len(headers))]
    sep = "  "
    out = [sep.join(h.ljust(widths[i]) if i == 0 else h.rjust(widths[i]) for i, h in enumerate(headers))]
    out.append(sep.join("-" * w for w in widths))
    for r in body:
        out.append(sep.join(c.ljust(widths[i]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(r)))
    return "\n".join(out)

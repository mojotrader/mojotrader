"""Performance statistics for a backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def summarize(trades: pd.DataFrame, equity: pd.Series, initial_capital: float) -> dict:
    """Headline numbers. P&L figures are net of commission and slippage."""
    out = {"trades": 0, "net_pnl": 0.0, "win_rate": np.nan, "profit_factor": np.nan, "avg_trade": np.nan,
           "avg_win": np.nan, "avg_loss": np.nan, "largest_win": np.nan, "largest_loss": np.nan,
           "max_drawdown": 0.0, "max_drawdown_pct": 0.0, "return_pct": 0.0, "sharpe": np.nan,
           "avg_r": np.nan, "commission": 0.0, "days_traded": 0, "win_days_pct": np.nan,
           "max_consec_losses": 0, "recovery_factor": np.nan}
    if not equity.empty:
        dd = equity - equity.cummax()
        out["max_drawdown"] = float(dd.min())
        out["max_drawdown_pct"] = float((dd / equity.cummax()).min() * 100)
    if trades is None or trades.empty:
        return out
    pnl = trades["net_pnl"]
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    daily = daily_pnl(trades)
    streak = best = 0
    for v in pnl:
        streak = streak + 1 if v <= 0 else 0
        best = max(best, streak)
    out.update({
        "trades": int(len(trades)),
        "net_pnl": float(pnl.sum()),
        "win_rate": float(len(wins) / len(pnl) * 100),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else np.inf,
        "avg_trade": float(pnl.mean()),
        "avg_win": float(wins.mean()) if len(wins) else np.nan,
        "avg_loss": float(losses.mean()) if len(losses) else np.nan,
        "largest_win": float(pnl.max()),
        "largest_loss": float(pnl.min()),
        "return_pct": float(pnl.sum() / initial_capital * 100),
        "avg_r": float(trades["r_multiple"].dropna().mean()) if trades["r_multiple"].notna().any() else np.nan,
        "commission": float(trades["commission"].sum()),
        "days_traded": int(len(daily)),
        "win_days_pct": float((daily > 0).mean() * 100) if len(daily) else np.nan,
        "sharpe": float(daily.mean() / daily.std() * np.sqrt(252)) if len(daily) > 1 and daily.std() > 0 else np.nan,
        "max_consec_losses": int(best),
    })
    if out["max_drawdown"] < 0:
        out["recovery_factor"] = out["net_pnl"] / -out["max_drawdown"]
    return out


def daily_pnl(trades: pd.DataFrame) -> pd.Series:
    if trades is None or trades.empty:
        return pd.Series(dtype=float)
    d = pd.to_datetime(trades["exit_time"]).dt.date
    return trades.groupby(d)["net_pnl"].sum()


def breakdown(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    """P&L table grouped by 'group', 'weekday', 'month', 'hour', 'exit_reason', 'direction' or 'note'."""
    if trades is None or trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    et = pd.to_datetime(t["entry_time"])
    if by == "weekday":
        key = pd.Categorical(et.dt.dayofweek.map(dict(enumerate(WEEKDAYS))), WEEKDAYS, ordered=True)
    elif by == "month":
        key = et.dt.strftime("%Y-%m")
    elif by == "hour":
        key = et.dt.hour
    elif by == "direction":
        key = t["side"].map({1: "Long", -1: "Short"})
    else:
        key = t[by]
    g = t.groupby(key, observed=True)["net_pnl"]
    out = pd.DataFrame({
        "Trades": g.count(),
        "Net P&L $": g.sum(),
        "Win rate %": g.apply(lambda s: (s > 0).mean() * 100),
        "Avg trade $": g.mean(),
        "Profit factor": g.apply(lambda s: s[s > 0].sum() / -s[s <= 0].sum() if (s <= 0).any() and s[s <= 0].sum() < 0 else np.inf),
    })
    out.index.name = by.replace("_", " ").title()
    return out


def equity_by_group(trades: pd.DataFrame) -> pd.DataFrame:
    """Cumulative closed-trade P&L per setup (columns = groups), indexed by exit time."""
    if trades is None or trades.empty:
        return pd.DataFrame()
    t = trades.sort_values("exit_time")
    out = t.pivot_table(index="exit_time", columns="group", values="net_pnl", aggfunc="sum").fillna(0).cumsum()
    out["All"] = out.sum(axis=1)
    return out

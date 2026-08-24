#!/usr/bin/env python3
"""Joined performance report for TradingView "List of Trades" CSV exports.

Reads one or more TradingView strategy-tester trade exports, computes a full
metric set per strategy, and builds a joined (portfolio) view that treats the
strategies as one book: daily PnL is summed, capital is summed, and the
diversification effect is measured against the standalone curves.

Usage:
    python3 analysis/trade_stats.py FILE.csv [FILE.csv ...] \
        [--commission-per-contract-side 1.0] [--out-dir analysis/out]

TradingView exports two rows per trade (an entry order and an exit order) that
repeat the same trade-level PnL. Trade-level figures are taken from the exit
row; entry rows supply the entry timestamp, price and commission.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

TRADING_DAYS = 252
POINT_VALUE = 2.0  # MNQ: $2 per index point


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def strategy_name(path: str) -> str:
    """Derive a readable strategy name from a TradingView export filename."""
    base = os.path.basename(path)
    base = re.sub(r"\.csv$", "", base)
    base = re.sub(r"^[0-9a-f]{6,}-", "", base)               # upload hash prefix
    base = re.sub(r"_CME_MINI_[A-Z0-9!]+_\d{8,}$", "", base)  # symbol + export-date suffix
    base = base.replace("___", " - ").replace("__", " ").replace("_", " ")
    return re.sub(r"\s+", " ", base).strip(" -")


def load_trades(path: str) -> pd.DataFrame:
    """Return one row per round-turn trade."""
    raw = pd.read_csv(path, encoding="utf-8-sig")
    raw["Date and time"] = pd.to_datetime(raw["Date and time"])

    is_exit = raw["Type"].str.startswith("Exit")
    exits = raw[is_exit].set_index("Trade number").sort_index()
    entries = raw[~is_exit].set_index("Trade number").sort_index()

    t = pd.DataFrame(index=exits.index)
    t["direction"] = np.where(exits["Type"].str.endswith("long"), "long", "short")
    t["entry_time"] = entries["Date and time"]
    t["exit_time"] = exits["Date and time"]
    t["entry_price"] = entries["Price USD"]
    t["exit_price"] = exits["Price USD"]
    t["entry_signal"] = entries["Signal"]
    t["exit_signal"] = exits["Signal"]
    t["qty"] = exits["Size (qty)"]
    t["value"] = exits["Size (value)"]
    t["pnl"] = exits["Net PnL USD"].astype(float)
    t["return_pct"] = exits["Return %"].astype(float)
    t["commission"] = (entries["Commission USD"] + exits["Commission USD"]).astype(float)
    t["mfe"] = exits["Favorable excursion USD"].astype(float)
    t["mae"] = exits["Adverse excursion USD"].abs().astype(float)
    t["bars"] = exits["Duration (bars)"].astype(float)
    t["hold_min"] = (t["exit_time"] - t["entry_time"]).dt.total_seconds() / 60.0

    # TradingView reports cumulative PnL as a % of the tester's initial capital;
    # invert that to recover the capital base the export was produced with.
    cum_usd = exits["Cumulative PnL USD"].astype(float)
    cum_pct = exits["Cumulative PnL %"].astype(float)
    implied = (cum_usd / cum_pct * 100.0).replace([np.inf, -np.inf], np.nan).dropna()
    capital = float(np.round(implied.median(), -3)) if len(implied) else np.nan

    t.attrs["capital"] = capital
    t.attrs["name"] = strategy_name(path)
    t.attrs["source"] = os.path.basename(path)
    return t.sort_values("exit_time").reset_index(drop=True)


def apply_commission(t: pd.DataFrame, per_contract_side: float) -> pd.DataFrame:
    """Re-price every trade at a common commission rate (round turn = 2 sides)."""
    t = t.copy()
    target = t["qty"] * per_contract_side * 2.0
    t["pnl"] = t["pnl"] + t["commission"] - target
    t["commission"] = target
    t.attrs = dict(t.attrs)
    return t


# --------------------------------------------------------------------------- #
# Curves
# --------------------------------------------------------------------------- #

def daily_pnl(t: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Trade PnL booked on the exit date, reindexed onto a common calendar."""
    s = t.groupby(t["exit_time"].dt.normalize())["pnl"].sum()
    return s.reindex(index, fill_value=0.0)


def drawdown(equity: pd.Series) -> pd.Series:
    return equity - equity.cummax()


def max_dd_duration_days(equity: pd.Series) -> int:
    """Longest stretch (calendar days) spent below a prior equity peak."""
    peak_time, longest = equity.index[0], 0
    running_max = -np.inf
    for ts, val in equity.items():
        if val >= running_max:
            running_max, peak_time = val, ts
        else:
            longest = max(longest, (ts - peak_time).days)
    return longest


def streaks(wins: pd.Series) -> tuple[int, int]:
    best = worst = cur_w = cur_l = 0
    for w in wins:
        if w:
            cur_w, cur_l = cur_w + 1, 0
            best = max(best, cur_w)
        else:
            cur_l, cur_w = cur_l + 1, 0
            worst = max(worst, cur_l)
    return best, worst


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

def metrics(t: pd.DataFrame, capital: float, index: pd.DatetimeIndex, label: str) -> dict:
    pnl = t["pnl"]
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_profit, gross_loss = wins.sum(), -losses.sum()

    dpnl = daily_pnl(t, index)
    equity = capital + dpnl.cumsum()
    dd = drawdown(equity)
    max_dd = -dd.min()
    max_dd_pct = max_dd / equity.cummax().loc[dd.idxmin()] * 100 if max_dd > 0 else 0.0

    # Fixed-capital (non-compounding) sizing: returns are PnL over the constant base.
    rets = dpnl / capital
    years = (index[-1] - index[0]).days / 365.25
    ann_ret = pnl.sum() / capital / years * 100 if years else np.nan
    sharpe = rets.mean() / rets.std(ddof=1) * np.sqrt(TRADING_DAYS) if rets.std(ddof=1) else np.nan
    downside = rets[rets < 0]
    dd_std = np.sqrt((downside ** 2).sum() / len(rets)) if len(downside) else np.nan
    sortino = rets.mean() / dd_std * np.sqrt(TRADING_DAYS) if dd_std else np.nan

    best_streak, worst_streak = streaks(pnl > 0)
    monthly = dpnl.resample("ME").sum()

    # Exposure: union of open-position intervals, against the wall clock.
    iv = t[["entry_time", "exit_time"]].sort_values("entry_time").to_numpy()
    covered, cur_s, cur_e = pd.Timedelta(0), None, None
    for s, e in iv:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        covered += cur_e - cur_s

    longs, shorts = t[t["direction"] == "long"], t[t["direction"] == "short"]

    def side(x: pd.DataFrame) -> dict:
        if x.empty:
            return {}
        w = x["pnl"] > 0
        return {
            "trades": len(x),
            "net_pnl": x["pnl"].sum(),
            "win_rate": w.mean() * 100,
            "avg_trade": x["pnl"].mean(),
            "profit_factor": x.loc[w, "pnl"].sum() / -x.loc[~w & (x["pnl"] < 0), "pnl"].sum()
            if (x["pnl"] < 0).any() else np.inf,
        }

    return {
        "label": label,
        "capital": capital,
        "start": index[0].date().isoformat(),
        "end": index[-1].date().isoformat(),
        "years": years,
        "trading_days": int((dpnl != 0).sum()),
        "calendar_days": len(index),

        "trades": len(t),
        "trades_per_month": len(t) / (years * 12) if years else np.nan,
        "contracts_traded": int(t["qty"].sum() * 2),
        "avg_size": t["qty"].mean(),
        "max_size": int(t["qty"].max()),
        # One MNQ tick is 0.25 index points = $0.50/contract, so a tick of slippage
        # on entry and exit costs $1 per contract per round turn.
        "slippage_1tick": float(t["qty"].sum() * 2 * POINT_VALUE * 0.25),

        "net_pnl": pnl.sum(),
        "net_pnl_pct": pnl.sum() / capital * 100,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "commission": t["commission"].sum(),
        "commission_pct_of_gross": t["commission"].sum() / gross_profit * 100 if gross_profit else np.nan,
        "ann_return_pct": ann_ret,

        "profit_factor": gross_profit / gross_loss if gross_loss else np.inf,
        "win_rate": (pnl > 0).mean() * 100,
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "scratches": int((pnl == 0).sum()),
        "avg_trade": pnl.mean(),
        "median_trade": pnl.median(),
        "avg_win": wins.mean() if len(wins) else np.nan,
        "avg_loss": losses.mean() if len(losses) else np.nan,
        "payoff_ratio": wins.mean() / -losses.mean() if len(losses) and losses.mean() else np.nan,
        "expectancy": pnl.mean(),
        "expectancy_pct_of_risk": pnl.mean() / -losses.mean() if len(losses) and losses.mean() else np.nan,
        "largest_win": pnl.max(),
        "largest_loss": pnl.min(),
        "largest_loss_pct_capital": pnl.min() / capital * 100,
        "std_trade": pnl.std(ddof=1),
        "max_consec_wins": best_streak,
        "max_consec_losses": worst_streak,

        "max_dd": max_dd,
        "max_dd_pct": max_dd_pct,
        "max_dd_date": dd.idxmin().date().isoformat(),
        "max_dd_days": max_dd_duration_days(equity),
        "recovery_factor": pnl.sum() / max_dd if max_dd else np.inf,
        "calmar": ann_ret / max_dd_pct if max_dd_pct else np.inf,
        "sharpe": sharpe,
        "sortino": sortino,
        "best_day": dpnl.max(),
        "worst_day": dpnl.min(),
        "daily_win_rate": (dpnl[dpnl != 0] > 0).mean() * 100,

        "best_month": monthly.max(),
        "worst_month": monthly.min(),
        "positive_months": int((monthly > 0).sum()),
        "total_months": int(len(monthly)),

        "avg_mfe": t["mfe"].mean(),
        "avg_mae": t["mae"].mean(),
        "edge_ratio": t["mfe"].mean() / t["mae"].mean() if t["mae"].mean() else np.nan,
        "avg_hold_min": t["hold_min"].mean(),
        "median_hold_min": t["hold_min"].median(),
        "max_hold_min": t["hold_min"].max(),
        "exposure_pct": covered / (index[-1] - index[0]) * 100,
        "overnight_trades": int((t["entry_time"].dt.normalize() != t["exit_time"].dt.normalize()).sum()),
        "worst_day_pct": dpnl.min() / capital * 100,
        "best_day_pct": dpnl.max() / capital * 100,

        "long": side(longs),
        "short": side(shorts),

        "_daily": dpnl,
        "_equity": equity,
        "_dd": dd,
        "_monthly": monthly,
    }


def signal_table(t: pd.DataFrame) -> pd.DataFrame:
    """PnL attribution by entry signal."""
    def agg(g: pd.DataFrame) -> pd.Series:
        w = g["pnl"] > 0
        gl = -g.loc[g["pnl"] < 0, "pnl"].sum()
        return pd.Series({
            "trades": len(g),
            "net_pnl": g["pnl"].sum(),
            "win_rate": w.mean() * 100,
            "avg_trade": g["pnl"].mean(),
            "profit_factor": (g.loc[w, "pnl"].sum() / gl) if gl else np.inf,
            "avg_hold_min": g["hold_min"].mean(),
        })

    out = t.groupby("entry_signal", group_keys=False)[t.columns].apply(agg)
    return out.sort_values("net_pnl", ascending=False)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def money(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def pct(x: float, d: int = 1) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{d}f}%"


def num(x: float, d: int = 2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return "∞" if np.isinf(x) else f"{x:,.{d}f}"


ROWS = [
    ("Period", lambda m: f"{m['start']} → {m['end']}"),
    ("Starting capital", lambda m: money(m["capital"])),
    ("Trades", lambda m: f"{m['trades']:,}"),
    ("Trades / month", lambda m: num(m["trades_per_month"], 1)),
    ("Avg position size", lambda m: f"{num(m['avg_size'], 2)} contracts"),
    ("Max position size", lambda m: f"{m['max_size']} contracts"),
    ("Net profit", lambda m: money(m["net_pnl"])),
    ("Net profit % of capital", lambda m: pct(m["net_pnl_pct"])),
    ("Annualised return", lambda m: pct(m["ann_return_pct"])),
    ("Gross profit", lambda m: money(m["gross_profit"])),
    ("Gross loss", lambda m: money(-m["gross_loss"])),
    ("Commission paid", lambda m: money(m["commission"])),
    ("Commission % of gross profit", lambda m: pct(m["commission_pct_of_gross"])),
    ("Profit factor", lambda m: num(m["profit_factor"])),
    ("Win rate", lambda m: pct(m["win_rate"])),
    ("Wins / losses / scratches", lambda m: f"{m['wins']} / {m['losses']} / {m['scratches']}"),
    ("Avg trade (expectancy)", lambda m: money(m["avg_trade"])),
    ("Median trade", lambda m: money(m["median_trade"])),
    ("Avg win", lambda m: money(m["avg_win"])),
    ("Avg loss", lambda m: money(m["avg_loss"])),
    ("Payoff ratio (avg win / avg loss)", lambda m: num(m["payoff_ratio"])),
    ("Expectancy in R (avg loss = 1R)", lambda m: num(m["expectancy_pct_of_risk"])),
    ("Largest win", lambda m: money(m["largest_win"])),
    ("Largest loss", lambda m: f"{money(m['largest_loss'])} ({pct(m['largest_loss_pct_capital'])} of capital)"),
    ("Trade PnL std dev", lambda m: money(m["std_trade"])),
    ("Max consecutive wins", lambda m: str(m["max_consec_wins"])),
    ("Max consecutive losses", lambda m: str(m["max_consec_losses"])),
    ("Max drawdown", lambda m: f"{money(m['max_dd'])} ({pct(m['max_dd_pct'])})"),
    ("Max drawdown trough", lambda m: m["max_dd_date"]),
    ("Longest drawdown", lambda m: f"{m['max_dd_days']} days"),
    ("Recovery factor (profit / max DD)", lambda m: num(m["recovery_factor"])),
    ("Calmar ratio", lambda m: num(m["calmar"])),
    ("Sharpe ratio (daily, ann.)", lambda m: num(m["sharpe"])),
    ("Sortino ratio (daily, ann.)", lambda m: num(m["sortino"])),
    ("Best day / worst day", lambda m: f"{money(m['best_day'])} / {money(m['worst_day'])}"),
    ("Worst day % of capital", lambda m: pct(m["worst_day_pct"])),
    ("Winning days", lambda m: pct(m["daily_win_rate"])),
    ("Best month / worst month", lambda m: f"{money(m['best_month'])} / {money(m['worst_month'])}"),
    ("Positive months", lambda m: f"{m['positive_months']} / {m['total_months']}"),
    ("Avg MFE (max favourable excursion)", lambda m: money(m["avg_mfe"])),
    ("Avg MAE (max adverse excursion)", lambda m: money(m["avg_mae"])),
    ("Edge ratio (MFE / MAE)", lambda m: num(m["edge_ratio"])),
    ("Avg hold time", lambda m: f"{num(m['avg_hold_min'], 0)} min"),
    ("Median hold time", lambda m: f"{num(m['median_hold_min'], 0)} min"),
    ("Time in market", lambda m: pct(m["exposure_pct"])),
    ("Trades held past the session", lambda m: f"{m['overnight_trades']} ({pct(m['overnight_trades'] / m['trades'] * 100)})"),
]


def markdown_report(mets: list[dict], joined: dict, extra: dict, sig: dict[str, pd.DataFrame]) -> str:
    cols = [m["label"] for m in mets] + [joined["label"]]
    all_m = mets + [joined]

    out = ["# Joined Performance Report", ""]
    out.append(f"**Instrument:** CME MINI MNQ1! · **Window:** {joined['start']} → {joined['end']} "
               f"({joined['years']:.2f} years)")
    out.append("")
    out.append(f"**Commission basis:** all strategies re-priced at "
               f"${extra['commission_rate']:.2f} per contract per side "
               f"(${extra['commission_rate'] * 2:.2f} round turn).")
    out.append("")

    out.append("## Headline")
    out.append("")
    out.append("| Metric | " + " | ".join(cols) + " |")
    out.append("|---|" + "---|" * len(cols))
    head = ["Net profit", "Annualised return", "Profit factor", "Win rate", "Max drawdown",
            "Sharpe ratio (daily, ann.)", "Recovery factor (profit / max DD)", "Trades"]
    for name, fn in ROWS:
        if name in head:
            out.append(f"| {name} | " + " | ".join(fn(m) for m in all_m) + " |")
    out.append("")

    out.append("## Full metric set")
    out.append("")
    out.append("| Metric | " + " | ".join(cols) + " |")
    out.append("|---|" + "---|" * len(cols))
    for name, fn in ROWS:
        out.append(f"| {name} | " + " | ".join(fn(m) for m in all_m) + " |")
    out.append("")

    out.append("## Long vs short")
    out.append("")
    out.append("| Strategy | Side | Trades | Net PnL | Win rate | Avg trade | Profit factor |")
    out.append("|---|---|---|---|---|---|---|")
    for m in all_m:
        for s in ("long", "short"):
            d = m[s]
            if d:
                out.append(f"| {m['label']} | {s} | {d['trades']} | {money(d['net_pnl'])} | "
                           f"{pct(d['win_rate'])} | {money(d['avg_trade'])} | {num(d['profit_factor'])} |")
    out.append("")

    out.append("## Correlation & diversification")
    out.append("")
    out.append(f"- Daily-PnL correlation between the two strategies: **{extra['corr']:.3f}**")
    out.append(f"- Days both strategies traded: **{extra['both_days']}** of {extra['any_days']} active days")
    out.append(f"- Sum of standalone max drawdowns: **{money(extra['dd_sum'])}** · "
               f"joined max drawdown: **{money(joined['max_dd'])}** "
               f"(**{money(extra['dd_sum'] - joined['max_dd'])}** saved by non-overlapping drawdowns)")
    out.append(f"- Weighted-average standalone Sharpe: **{extra['sharpe_wavg']:.2f}** · "
               f"joined Sharpe: **{joined['sharpe']:.2f}**")
    out.append(f"- Peak concurrent exposure: **{extra['max_concurrent_contracts']} contracts** "
               f"({money(extra['max_concurrent_notional'])} notional)")
    out.append("")

    out.append("## Monthly PnL")
    out.append("")
    mt = extra["monthly_table"]
    out.append("| Month | " + " | ".join(mt.columns) + " |")
    out.append("|---|" + "---|" * len(mt.columns))
    for idx, row in mt.iterrows():
        out.append(f"| {idx} | " + " | ".join(money(v) for v in row) + " |")
    out.append("")

    out.append("## PnL attribution by entry signal")
    out.append("")
    for label, tbl in sig.items():
        out.append(f"### {label}")
        out.append("")
        out.append("| Signal | Trades | Net PnL | Win rate | Avg trade | Profit factor | Avg hold |")
        out.append("|---|---|---|---|---|---|---|")
        for name, r in tbl.iterrows():
            out.append(f"| {name} | {int(r['trades'])} | {money(r['net_pnl'])} | {pct(r['win_rate'])} | "
                       f"{money(r['avg_trade'])} | {num(r['profit_factor'])} | {num(r['avg_hold_min'], 0)} min |")
        out.append("")

    return "\n".join(out)


# --------------------------------------------------------------------------- #

def analyse(files: list[str], commission_per_contract_side: float = 1.0,
            joined_capital: float | None = None) -> dict:
    """Run the full analysis and return every piece the reporters need.

    By default the joined book is priced as separately funded accounts (each
    strategy keeps its own tester capital, summed). Pass `joined_capital` to
    price it instead as every strategy trading out of one shared account of
    that size — e.g. running both strategies' signals on a single $30k account.
    Only the capital denominator changes: the same trades, in the same size,
    are assumed to fire regardless of account structure.
    """
    trades = [load_trades(f) for f in files]
    raw_comm = {t.attrs["name"]: float(t["commission"].sum()) for t in trades}
    trades = [apply_commission(t, commission_per_contract_side) for t in trades]

    start = min(t["entry_time"].min() for t in trades).normalize()
    end = max(t["exit_time"].max() for t in trades).normalize()
    index = pd.date_range(start, end, freq="D")

    mets = [metrics(t, t.attrs["capital"], index, t.attrs["name"]) for t in trades]

    joined_trades = pd.concat(trades, ignore_index=True).sort_values("exit_time")
    shared_account = joined_capital is not None
    if joined_capital is None:
        joined_capital = sum(t.attrs["capital"] for t in trades)
    joined = metrics(joined_trades, joined_capital, index, "JOINED")

    # Diversification / concurrency
    d0, d1 = mets[0]["_daily"], mets[1]["_daily"]
    active = (d0 != 0) | (d1 != 0)
    corr = float(d0[active].corr(d1[active]))

    ev = []
    for t in trades:
        for _, r in t.iterrows():
            per_contract = r["value"] / r["qty"]
            ev.append((r["entry_time"], r["qty"], per_contract))
            ev.append((r["exit_time"], -r["qty"], per_contract))
    ev.sort(key=lambda x: x[0])
    cq = cn = mq = mn = 0.0
    for _, q, per in ev:
        cq += q
        cn += q * per
        mq, mn = max(mq, cq), max(mn, cn)

    monthly_table = pd.DataFrame({m["label"]: m["_monthly"] for m in mets})
    monthly_table["JOINED"] = joined["_monthly"]
    monthly_table.index = monthly_table.index.strftime("%Y-%m")

    extra = {
        "shared_account": shared_account,
        "commission_rate": commission_per_contract_side,
        "raw_commission": raw_comm,
        "corr": corr,
        "both_days": int(((d0 != 0) & (d1 != 0)).sum()),
        "any_days": int(active.sum()),
        "dd_sum": sum(m["max_dd"] for m in mets),
        "sharpe_wavg": float(np.average([m["sharpe"] for m in mets],
                                        weights=[m["capital"] for m in mets])),
        "max_concurrent_contracts": int(mq),
        "max_concurrent_notional": mn,
        "monthly_table": monthly_table,
    }

    sig = {m["label"]: signal_table(t) for m, t in zip(mets, trades)}

    return {"trades": trades, "metrics": mets, "joined": joined, "extra": extra,
            "signals": sig, "index": index}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--commission-per-contract-side", type=float, default=1.0,
                    help="normalise every export to this commission rate (default $1.00)")
    ap.add_argument("--out-dir", default="analysis/out")
    ap.add_argument("--joined-capital", type=float, default=None,
                    help="price the joined book on one shared account of this size "
                         "instead of the strategies' summed tester capital")
    args = ap.parse_args(argv)

    res = analyse(args.files, args.commission_per_contract_side, args.joined_capital)
    mets, joined, extra, sig = res["metrics"], res["joined"], res["extra"], res["signals"]

    os.makedirs(args.out_dir, exist_ok=True)
    md = markdown_report(mets, joined, extra, sig)
    with open(os.path.join(args.out_dir, "joined_performance.md"), "w") as fh:
        fh.write(md)

    clean = [{k: (None if isinstance(v, float) and (np.isnan(v) or np.isinf(v)) else v)
              for k, v in m.items() if not k.startswith("_")} for m in mets + [joined]]
    with open(os.path.join(args.out_dir, "joined_performance.json"), "w") as fh:
        json.dump({"strategies": clean, "diversification":
                   {k: v for k, v in extra.items() if k != "monthly_table"}}, fh, indent=2, default=str)

    curves = pd.DataFrame({m["label"]: m["_equity"] for m in mets})
    curves["JOINED"] = joined["_equity"]
    curves.to_csv(os.path.join(args.out_dir, "equity_curves.csv"))

    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

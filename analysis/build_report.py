#!/usr/bin/env python3
"""Render the joined-performance HTML report from TradingView trade exports.

    python3 analysis/build_report.py FILE.csv FILE.csv [--out analysis/out/report.html]

Metrics come from trade_stats.analyse(); this module only lays them out.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trade_stats as ts  # noqa: E402

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_template.html")

# The metric matrix, grouped so ~45 rows stay scannable. Names index ts.ROWS.
GROUPS = [
    ("Return", [
        "Net profit", "Net profit % of capital", "Annualised return",
        "Gross profit", "Gross loss", "Profit factor",
        "Commission paid", "Commission % of gross profit",
    ]),
    ("Risk", [
        "Max drawdown", "Max drawdown trough", "Longest drawdown",
        "Recovery factor (profit / max DD)", "Calmar ratio",
        "Sharpe ratio (daily, ann.)", "Sortino ratio (daily, ann.)",
        "Largest loss", "Worst day % of capital", "Max consecutive losses",
        "Trade PnL std dev",
    ]),
    ("Trade quality", [
        "Trades", "Trades / month", "Win rate", "Wins / losses / scratches",
        "Avg trade (expectancy)", "Median trade", "Avg win", "Avg loss",
        "Payoff ratio (avg win / avg loss)", "Expectancy in R (avg loss = 1R)",
        "Largest win", "Max consecutive wins",
    ]),
    ("Consistency", [
        "Best day / worst day", "Winning days",
        "Best month / worst month", "Positive months",
    ]),
    ("Execution & exposure", [
        "Starting capital", "Avg position size", "Avg MFE (max favourable excursion)",
        "Avg MAE (max adverse excursion)", "Edge ratio (MFE / MAE)",
        "Avg hold time", "Median hold time", "Time in market",
        "Trades held past the session",
    ]),
]

# Rows where a bigger number is better / worse, for the "best column" tick.
# Only scale-free metrics get a "better" mark. Dollar profit, percentage return and
# average trade all depend on the tester's capital base and position size, which
# differ between these exports — marking a winner there would compare denominators.
HIGHER_IS_BETTER = {
    "Profit factor", "Recovery factor (profit / max DD)", "Calmar ratio",
    "Sharpe ratio (daily, ann.)", "Sortino ratio (daily, ann.)", "Win rate",
    "Payoff ratio (avg win / avg loss)", "Expectancy in R (avg loss = 1R)",
    "Edge ratio (MFE / MAE)", "Winning days",
}

KEY = {  # metric-name -> raw key, for numeric comparison
    "Profit factor": "profit_factor",
    "Recovery factor (profit / max DD)": "recovery_factor", "Calmar ratio": "calmar",
    "Sharpe ratio (daily, ann.)": "sharpe", "Sortino ratio (daily, ann.)": "sortino",
    "Win rate": "win_rate",
    "Payoff ratio (avg win / avg loss)": "payoff_ratio",
    "Expectancy in R (avg loss = 1R)": "expectancy_pct_of_risk",
    "Edge ratio (MFE / MAE)": "edge_ratio", "Winning days": "daily_win_rate",
}

SHORT = {"Halyard BreakthenPullback ORB IB": "Halyard",
         "QuantFlow V3 - 8 Contract Risk - Aggressive Risk": "QuantFlow V3"}


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def short(label: str) -> str:
    return SHORT.get(label, label)


def signed(v: float, fmt=ts.money) -> str:
    cls = "neg" if v < 0 else ("pos" if v > 0 else "flat")
    return f'<span class="{cls}">{esc(fmt(v))}</span>'


def matrix_html(all_m: list[dict]) -> str:
    fns = dict(ts.ROWS)
    out = ['<div class="scroll"><table class="matrix">',
           "<thead><tr><th>Metric</th>"]
    for m in all_m:
        cls = ' class="joined-col"' if m["label"] == "JOINED" else ""
        out.append(f'<th{cls}>{esc(short(m["label"]))}</th>')
    out.append("</tr></thead><tbody>")

    for group, names in GROUPS:
        out.append(f'<tr class="grp"><th colspan="{len(all_m) + 1}">{esc(group)}</th></tr>')
        for name in names:
            fn = fns[name]
            best = None
            if name in KEY:
                vals = [m.get(KEY[name]) for m in all_m[:-1]]  # compare strategies only
                vals = [v if isinstance(v, (int, float)) and np.isfinite(v) else None for v in vals]
                if all(v is not None for v in vals) and len(set(vals)) > 1:
                    best = int(np.argmax(vals) if name in HIGHER_IS_BETTER else np.argmin(vals))
            out.append(f"<tr><th>{esc(name)}</th>")
            for i, m in enumerate(all_m):
                cls = ["num"]
                if m["label"] == "JOINED":
                    cls.append("joined-col")
                if best is not None and i == best:
                    cls.append("best")
                out.append(f'<td class="{" ".join(cls)}">{esc(fn(m))}</td>')
            out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def side_html(all_m: list[dict]) -> str:
    out = ['<div class="scroll"><table class="grid">',
           "<thead><tr><th>Book</th><th>Side</th><th>Trades</th><th>Net PnL</th>"
           "<th>Win rate</th><th>Avg trade</th><th>Profit factor</th></tr></thead><tbody>"]
    for m in all_m:
        for s in ("long", "short"):
            d = m[s]
            if not d:
                continue
            cls = ' class="joined-row"' if m["label"] == "JOINED" else ""
            out.append(
                f'<tr{cls}><th>{esc(short(m["label"]))}</th>'
                f'<td><span class="side side-{s}">{s}</span></td>'
                f'<td class="num">{d["trades"]}</td>'
                f'<td class="num">{signed(d["net_pnl"])}</td>'
                f'<td class="num">{esc(ts.pct(d["win_rate"]))}</td>'
                f'<td class="num">{signed(d["avg_trade"])}</td>'
                f'<td class="num">{esc(ts.num(d["profit_factor"]))}</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def signal_html(tbl: pd.DataFrame, net: float) -> str:
    top = tbl["net_pnl"].abs().max() or 1.0
    out = ['<div class="scroll"><table class="grid sig">',
           "<thead><tr><th>Entry signal</th><th>Net PnL</th><th>Share</th><th>Trades</th>"
           "<th>Win rate</th><th>Avg trade</th><th>PF</th><th>Avg hold</th></tr></thead><tbody>"]
    for name, r in tbl.iterrows():
        w = abs(r["net_pnl"]) / top * 100
        cls = "bar-neg" if r["net_pnl"] < 0 else "bar-pos"
        out.append(
            f'<tr><th>{esc(name)}</th>'
            f'<td class="num">{signed(r["net_pnl"])}</td>'
            f'<td class="barcell"><span class="bar {cls}" style="width:{w:.1f}%"></span>'
            f'<span class="barpct">{r["net_pnl"] / net * 100:.0f}%</span></td>'
            f'<td class="num">{int(r["trades"])}</td>'
            f'<td class="num">{esc(ts.pct(r["win_rate"]))}</td>'
            f'<td class="num">{signed(r["avg_trade"])}</td>'
            f'<td class="num">{esc(ts.num(r["profit_factor"]))}</td>'
            f'<td class="num">{ts.num(r["avg_hold_min"], 0)} min</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def monthly_html(mt: pd.DataFrame) -> str:
    out = ['<div class="scroll"><table class="grid months"><thead><tr><th>Month</th>']
    for c in mt.columns:
        cls = ' class="joined-col"' if c == "JOINED" else ""
        out.append(f'<th{cls}>{esc(short(c))}</th>')
    out.append("</tr></thead><tbody>")
    for idx, row in mt.iterrows():
        out.append(f"<tr><th>{esc(idx)}</th>")
        for c in mt.columns:
            cls = "num joined-col" if c == "JOINED" else "num"
            out.append(f'<td class="{cls}">{signed(row[c])}</td>')
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def cards_html(all_m: list[dict]) -> str:
    out = []
    for i, m in enumerate(all_m):
        kind = "joined" if m["label"] == "JOINED" else f"s{i + 1}"
        name = "Joined book" if m["label"] == "JOINED" else short(m["label"])
        sub = ("both strategies, one account" if m["label"] == "JOINED"
               else f'{m["trades"]} trades · {ts.money(m["capital"])} base')
        out.append(f'''<article class="card {kind}">
  <header><span class="swatch"></span><h3>{esc(name)}</h3></header>
  <p class="sub">{esc(sub)}</p>
  <p class="hero">{esc(ts.money(m["net_pnl"]))}</p>
  <p class="heroSub">net profit · {esc(ts.pct(m["ann_return_pct"]))} annualised</p>
  <dl>
    <div><dt>Profit factor</dt><dd>{esc(ts.num(m["profit_factor"]))}</dd></div>
    <div><dt>Max drawdown</dt><dd>{esc(ts.money(m["max_dd"]))} <span class="dim">({esc(ts.pct(m["max_dd_pct"]))})</span></dd></div>
    <div><dt>Sharpe</dt><dd>{esc(ts.num(m["sharpe"]))}</dd></div>
    <div><dt>Win rate</dt><dd>{esc(ts.pct(m["win_rate"]))}</dd></div>
  </dl>
</article>''')
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--commission-per-contract-side", type=float, default=1.0)
    ap.add_argument("--out", default="analysis/out/joined_performance.html")
    args = ap.parse_args(argv)

    res = ts.analyse(args.files, args.commission_per_contract_side)
    mets, joined, extra, sig = res["metrics"], res["joined"], res["extra"], res["signals"]
    all_m = mets + [joined]

    idx = res["index"]
    step = max(1, len(idx) // 400)
    dates = [d.strftime("%Y-%m-%d") for d in idx[::step]]
    series = [{"name": short(m["label"]),
               "equity": [round(v - m["capital"], 1) for v in m["_equity"].values[::step]],
               "dd": [round(v, 1) for v in m["_dd"].values[::step]],
               "capital": m["capital"],
               "maxdd": m["max_dd"], "maxddpct": m["max_dd_pct"]}
              for m in all_m]

    mt = extra["monthly_table"]
    chart = {
        "dates": dates,
        "series": series,
        "months": list(mt.index),
        "monthly": {short(c): [float(v) for v in mt[c].values] for c in mt.columns},
        "monthNames": [short(c) for c in mt.columns],
    }

    div = {
        "corr": extra["corr"],
        "both_days": extra["both_days"], "any_days": extra["any_days"],
        "dd_sum": extra["dd_sum"], "joined_dd": joined["max_dd"],
        "dd_saved": extra["dd_sum"] - joined["max_dd"],
        "sharpe_wavg": extra["sharpe_wavg"], "joined_sharpe": joined["sharpe"],
        "contracts": extra["max_concurrent_contracts"],
        "notional": extra["max_concurrent_notional"],
    }

    html = open(TEMPLATE).read()
    repl = {
        "__CHART_DATA__": json.dumps(chart),
        "__CARDS__": cards_html(all_m),
        "__MATRIX__": matrix_html(all_m),
        "__SIDES__": side_html(all_m),
        "__MONTHLY__": monthly_html(mt),
        "__SIG_A__": signal_html(sig[mets[0]["label"]], mets[0]["net_pnl"]),
        "__SIG_B__": signal_html(sig[mets[1]["label"]], mets[1]["net_pnl"]),
        "__NAME_A__": esc(short(mets[0]["label"])),
        "__NAME_B__": esc(short(mets[1]["label"])),
        "__WINDOW__": f'{joined["start"]} → {joined["end"]}',
        "__YEARS__": f'{joined["years"]:.2f}',
        "__COMM__": f'${extra["commission_rate"]:.2f}',
        "__COMM_RT__": f'${extra["commission_rate"] * 2:.2f}',
        "__RAW_COMM_A__": ts.money(extra["raw_commission"][mets[0]["label"]]),
        "__RAW_COMM_B__": ts.money(extra["raw_commission"][mets[1]["label"]]),
        "__CORR__": f'{div["corr"]:.2f}',
        "__BOTH_DAYS__": str(div["both_days"]),
        "__ANY_DAYS__": str(div["any_days"]),
        "__DD_SUM__": ts.money(div["dd_sum"]),
        "__JOINED_DD__": ts.money(div["joined_dd"]),
        "__DD_SAVED__": ts.money(div["dd_saved"]),
        "__SHARPE_WAVG__": f'{div["sharpe_wavg"]:.2f}',
        "__JOINED_SHARPE__": f'{div["joined_sharpe"]:.2f}',
        "__CONTRACTS__": str(div["contracts"]),
        "__NOTIONAL__": ts.money(div["notional"]),
        "__JOINED_CAP__": ts.money(joined["capital"]),
        "__JOINED_NET__": ts.money(joined["net_pnl"]),
        "__JOINED_ANN__": ts.pct(joined["ann_return_pct"]),
        "__JOINED_DDPCT__": ts.pct(joined["max_dd_pct"]),
        "__WORST_DAY_A__": ts.money(mets[0]["worst_day"]),
        "__WORST_DAY_A_PCT__": ts.pct(mets[0]["worst_day_pct"]),
        "__CAP_A__": ts.money(mets[0]["capital"]),
        "__CAP_B__": ts.money(mets[1]["capital"]),
        "__ANN_A__": ts.pct(mets[0]["ann_return_pct"]),
        "__ANN_B__": ts.pct(mets[1]["ann_return_pct"]),
        "__TRADES__": f'{joined["trades"]:,}',
    }
    for k, v in repl.items():
        html = html.replace(k, v)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        fh.write(html)
    print(f"wrote {args.out} ({os.path.getsize(args.out):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

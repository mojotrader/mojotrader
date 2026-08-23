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
        "Starting capital", "Avg position size", "Max position size", "Avg MFE (max favourable excursion)",
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

# Prefix -> display name, so a re-exported file with a slightly different suffix
# still gets the short label.
SHORT = {"Halyard": "Halyard", "QuantFlow V3": "QuantFlow V3"}


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def short(label: str) -> str:
    for prefix, name in SHORT.items():
        if label.startswith(prefix):
            return name
    return label


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
    n_books = len(all_m) - 1
    for i, m in enumerate(all_m):
        kind = "joined" if m["label"] == "JOINED" else f"s{i + 1}"
        name = "Joined book" if m["label"] == "JOINED" else short(m["label"])
        sub = (f'{n_books} funded accounts · {ts.money(m["capital"])} total' if m["label"] == "JOINED"
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



WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
         8: "Eight", 9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve", 13: "Thirteen",
         14: "Fourteen", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen", 18: "Eighteen",
         19: "Nineteen", 20: "Twenty"}


def word(n: int) -> str:
    return WORDS.get(n, f"{n:,}")


def note_commission(mets: list[dict], extra: dict, joined: dict) -> str:
    """Say whether the exports agreed on cost, then price the un-modelled part."""
    rate, raw = extra["commission_rate"], extra["raw_commission"]
    charged = [(short(m["label"]), raw[m["label"]], m["commission"]) for m in mets]
    same = len({round(r, 2) for _, r, _ in charged}) == 1
    slip = joined["slippage_1tick"]
    tail = (f' Slippage is not modelled anywhere. At {joined["trades"]:,} round turns and '
            f'{ts.num(joined["avg_size"], 2)} contracts a trade, one MNQ tick of slippage per side is '
            f'{ts.money(slip)} — {slip / joined["net_pnl"] * 100:.0f}% of the joined net profit.')
    if same:
        return (f'Both exports charged the same commission, and every figure here re-prices them at '
                f'<strong>{ts.money(rate)} per contract per side</strong> ({ts.money(rate * 2)} round turn).'
                + tail)
    parts = " and ".join(f'{n} charged <strong>{ts.money(r)}</strong>' for n, r, _ in charged)
    worst = max(charged, key=lambda c: abs(c[2] - c[1]))
    return (f'The exports were not run on the same cost basis: over the year, {parts}. Every figure on this '
            f'page re-prices both at <strong>{ts.money(rate)} per contract per side</strong> '
            f'({ts.money(rate * 2)} round turn), which moves {worst[0]} by '
            f'<strong>{ts.money(abs(worst[2] - worst[1]))}</strong> against its raw net profit.' + tail)


def note_capital(mets: list[dict], joined: dict) -> str:
    a, b = mets
    if a["capital"] == b["capital"]:
        return (f'Both testers ran the same <strong>{ts.money(a["capital"])}</strong> account, so the '
                f'percentage returns are directly comparable — {short(a["label"])}\'s {ts.pct(a["ann_return_pct"])} '
                f'against {short(b["label"])}\'s {ts.pct(b["ann_return_pct"])} is a real difference, not a '
                f'difference of denominators. The joined column assumes <strong>two separately funded '
                f'{ts.money(a["capital"])} accounts</strong> totalling {ts.money(joined["capital"])}. Running '
                f'both strategies inside a single {ts.money(a["capital"])} account is a different, more '
                f'leveraged proposition than what is shown here.')
    return (f'The two testers ran different accounts — <strong>{ts.money(a["capital"])}</strong> for '
            f'{short(a["label"])} against <strong>{ts.money(b["capital"])}</strong> for {short(b["label"])} — '
            f'so the percentage returns are not like for like. {short(a["label"])}\'s '
            f'{ts.pct(a["ann_return_pct"])} and {short(b["label"])}\'s {ts.pct(b["ann_return_pct"])} measure '
            f'similar dollar engines against different denominators. Compare profit factor, expectancy in R '
            f'and Sharpe across the two; compare the percentages only against their own capital.')


def note_tail(mets: list[dict], joined: dict, extra: dict) -> str:
    w = min(mets, key=lambda m: m["worst_day_pct"])
    return (f'{short(w["label"])}\'s worst single day was <strong>{ts.money(w["worst_day"])}</strong>, '
            f'{ts.pct(abs(w["worst_day_pct"]))} of its stated capital, and it runs up to {w["max_size"]} '
            f'contracts. A book that can lose that much of its account in one session is sized for the '
            f'backtest\'s best case, not for a gap. The joined book\'s peak of '
            f'<strong>{extra["max_concurrent_contracts"]} concurrent contracts</strong> '
            f'({ts.money(extra["max_concurrent_notional"])} notional) is the number to check margin against, '
            f'not the {ts.num(joined["avg_size"], 2)} average. Drawdowns here are measured on closed-trade '
            f'equity, so they understate what the account saw while positions were open.')


def note_sample(joined: dict) -> str:
    return (f'One year, one instrument, one regime — a strongly trending Nasdaq. {joined["trades"]:,} trades '
            f'is a healthy sample for trade-level statistics, but the equity curve rests on only '
            f'{joined["total_months"]} months, so the Sharpe, Calmar and drawdown figures carry far less '
            f'certainty than their decimal places suggest. No walk-forward or out-of-sample split is present '
            f'in these exports, and both strategies were tuned on this same window.')


def lede_sides(mets: list[dict]) -> str:
    weaker = [m for m in mets if m["short"]["profit_factor"] < m["long"]["profit_factor"]]
    if len(weaker) == len(mets):
        return ("Both books lean long, and both are weaker on the short side — the same asymmetry in each, "
                "which means joining them does not diversify it away.")
    if not weaker:
        return "Both books are stronger short than long."
    m = weaker[0]
    return (f'{short(m["label"])} is weaker on the short side; '
            f'{short([x for x in mets if x is not m][0]["label"])} is not.')


def standfirst_tail(extra: dict, joined: dict, mets: list[dict]) -> str:
    better = joined["max_dd_pct"] < min(m["max_dd_pct"] for m in mets)
    if better and extra["corr"] < 0.4:
        return ("That is a shallower drawdown than either book runs on its own, because at a "
                f'{extra["corr"]:.2f} daily correlation their losing days rarely line up.')
    if extra["corr"] < 0.4:
        return (f'Their daily results are near-independent at a {extra["corr"]:.2f} correlation, so the '
                "combined curve is steadier than either alone.")
    return (f'Their daily results move together at a {extra["corr"]:.2f} correlation, so combining them adds '
            "size more than it adds diversification.")


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
        "__NOTE_COMMISSION__": note_commission(mets, extra, joined),
        "__NOTE_CAPITAL__": note_capital(mets, joined),
        "__NOTE_TAIL__": note_tail(mets, joined, extra),
        "__NOTE_SAMPLE__": note_sample(joined),
        "__LEDE_SIDES__": lede_sides(mets),
        "__STANDFIRST_TAIL__": standfirst_tail(extra, joined, mets),
        "__MONTHS_N__": word(joined["total_months"]),
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

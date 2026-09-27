"""mojotrader backtesting dashboard.

Start it with:   streamlit run app.py
Then open the address it prints (normally http://localhost:8501) in your browser.
"""
from __future__ import annotations

import itertools
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import backtester
from backtester import data as dstore
from backtester.contracts import CONTRACTS
from backtester.engine import BacktestConfig, run_backtest
from backtester.ibkr import BAR_SIZES
from backtester.metrics import breakdown, daily_pnl, equity_by_group, summarize
from backtester.strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent

# Chart colours (validated categorical slots 1-3 + neutral + status good/critical)
SERIES = {"HAL": "#2a78d6", "ORB": "#eb6834", "IB": "#1baf7a"}
NEUTRAL = "#898781"
GOOD, BAD = "#0ca30c", "#d03b3b"
GROUP_NAMES = {"HAL": "Halyard", "ORB": "ORB", "IB": "IB"}

st.set_page_config(page_title="mojotrader backtester", layout="wide")


# ====================================================================== helpers
@st.cache_data(show_spinner=False)
def _load(name: str, mtime: float) -> pd.DataFrame:
    return dstore.load_bars(name)


def load_dataset(name: str) -> pd.DataFrame:
    return _load(name, dstore.dataset_path(name).stat().st_mtime)


def money(v: float, md: bool = False) -> str:
    """$ amount. ``md=True`` escapes the $ for widgets that render Markdown (otherwise $..$ becomes maths)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    d = "\\$" if md else "$"
    return f"-{d}{abs(v):,.0f}" if v < 0 else f"{d}{v:,.0f}"


def num(v: float, fmt: str = "{:.2f}") -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v))):
        return "-"
    if isinstance(v, float) and np.isinf(v):
        return "∞"
    return fmt.format(v)


def param_widget(p, key_prefix="p_"):
    key = key_prefix + p.name
    if p.kind == "bool":
        return st.checkbox(p.label, value=p.default, key=key, help=p.help or None)
    if p.kind == "choice":
        return st.selectbox(p.label, p.options, index=p.options.index(p.default), key=key, help=p.help or None)
    if p.kind == "int":
        return int(st.number_input(p.label, value=int(p.default), step=1,
                                   min_value=None if p.min is None else int(p.min), key=key, help=p.help or None))
    if p.kind == "float":
        return float(st.number_input(p.label, value=float(p.default), step=float(p.step or 0.05),
                                     min_value=None if p.min is None else float(p.min),
                                     max_value=None if p.max is None else float(p.max),
                                     key=key, help=p.help or None))
    return st.text_input(p.label, value=str(p.default), key=key, help=p.help or None)


def style_fig(fig: go.Figure, height=360, y_title=None):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(137,135,129,0.2)", zeroline=False, title=y_title)
    return fig


def run(bars, strategy_cls, params, cfg):
    return run_backtest(bars, strategy_cls(params), cfg)


# ====================================================================== sidebar
st.sidebar.title("mojotrader")
st.sidebar.caption(f"Backtesting on IBKR data  \nVersion: {backtester.__version__}")

datasets = dstore.list_datasets()
if not datasets:
    st.sidebar.warning("No price data yet. Download from IBKR or import a CSV in the **Data** tab, "
                       "or create demo data to try the dashboard.")
    if st.sidebar.button("Create demo data (fake prices)"):
        with st.spinner("Generating demo data..."):
            dstore.save_bars(dstore.make_demo_data(days=260), "DEMO_MNQ_1min")
        st.rerun()

ds_name = st.sidebar.selectbox("Dataset", datasets) if datasets else None
bars_all = load_dataset(ds_name) if ds_name else None

strategy_name = st.sidebar.selectbox("Strategy", list(STRATEGIES))
strategy_cls = STRATEGIES[strategy_name]

if bars_all is not None and len(bars_all):
    if ds_name.startswith("DEMO"):
        st.sidebar.error("DEMO data = random fake prices. Use it only to learn the dashboard.")
    first, last = bars_all.index[0].date(), bars_all.index[-1].date()
    dr = st.sidebar.date_input("Date range", value=(first, last), min_value=first, max_value=last)
    d0, d1 = (dr if isinstance(dr, tuple) and len(dr) == 2 else (first, last))
    tf = st.sidebar.selectbox("Bar size (minutes)", [1, 3, 5, 15],
                              index=[1, 3, 5, 15].index(getattr(strategy_cls, "recommended_timeframe", 1)),
                              help="ORBIB is built for 1-minute bars (like running it on a 1m TradingView chart). "
                                   "Bigger bars run faster but limit fills get less precise.")

with st.sidebar.expander("Contract & costs", expanded=False):
    sym = st.selectbox("Contract ($ per point)", list(CONTRACTS),
                       format_func=lambda k: f"{k} - ${CONTRACTS[k].point_value:g}/pt")
    spec = CONTRACTS[sym]
    commission = st.number_input("Commission $ per contract per side", value=0.50, step=0.05,
                                 help="0.50 matches the TradingView script. IBKR is about $0.62 (micro) / $2.25 (mini).")
    slippage = st.number_input("Slippage (ticks) on stops & market orders", value=1, step=1, min_value=0)
    fill_rule = st.radio("Limit order fills", ["Touch (same as TradingView)", "Must trade 1 tick through (stricter)"],
                         help="Touch = a limit fills the moment price reaches it. Real exchanges often need price "
                              "to trade through your level because other orders are ahead of you.")
    pessimistic = st.checkbox("Pessimistic: if stop and target are in the same bar, assume the stop",
                              value=False)
    capital = st.number_input("Starting capital $", value=150000.0, step=5000.0)

cfg = BacktestConfig(point_value=spec.point_value, tick_size=spec.tick_size, commission=commission,
                     slippage_ticks=int(slippage), limit_through_ticks=0 if fill_rule.startswith("Touch") else 1,
                     pessimistic=pessimistic, orders_on_close=True, initial_capital=capital)

st.sidebar.subheader("Strategy settings")
params = {}
groups: dict[str, list] = {}
for p in strategy_cls.params_spec:
    groups.setdefault(p.group, []).append(p)
for gname, plist in groups.items():
    with st.sidebar.expander(gname, expanded=(gname == "Strategies")):
        for p in plist:
            params[p.name] = param_widget(p)

run_clicked = st.sidebar.button("Run backtest", type="primary", use_container_width=True,
                                disabled=bars_all is None)


def selected_bars():
    b = bars_all[(bars_all.index.date >= d0) & (bars_all.index.date <= d1)]
    return dstore.resample(b, tf)


if run_clicked:
    bars = selected_bars()
    t0 = time.time()
    try:
        with st.spinner(f"Backtesting {len(bars):,} bars..."):
            res = run(bars, strategy_cls, params, cfg)
        st.session_state["result"] = res
        st.session_state["result_meta"] = dict(dataset=ds_name, tf=tf, d0=d0, d1=d1, secs=time.time() - t0,
                                               contract=sym, fill=fill_rule)
    except ValueError as e:
        st.sidebar.error(str(e))

res = st.session_state.get("result")
meta = st.session_state.get("result_meta", {})

tabs = st.tabs(["Results", "Trades", "Chart", "Breakdown", "Optimize", "Data"])

# ====================================================================== Results
with tabs[0]:
    if res is None:
        st.header("Backtest dashboard")
        st.markdown(
            "1. Pick a **dataset** in the sidebar (or get data in the **Data** tab).\n"
            "2. Choose the **strategy settings** - they match your TradingView inputs.\n"
            "3. Click **Run backtest**.\n\n"
            "Default costs match the TradingView script ($0.50/contract, 1 tick slippage, limits fill on touch), "
            "so results can be compared with the TradingView Strategy Tester on the same dates.")
    else:
        t = res.trades
        s = summarize(t, res.equity, res.config.initial_capital)
        st.caption(f"{meta.get('dataset')} | {meta.get('d0')} -> {meta.get('d1')} | {meta.get('tf')}-min bars | "
                   f"{meta.get('contract')} | {meta.get('fill')} | ran in {meta.get('secs', 0):.1f}s")
        if str(meta.get("dataset", "")).startswith("DEMO"):
            st.error("These results are on FAKE demo prices - they say nothing about the strategy.")
        c = st.columns(4)
        c[0].metric("Net profit", money(s["net_pnl"], True), f"{s['return_pct']:.1f}% of capital")
        c[1].metric("Profit factor", num(s["profit_factor"]))
        c[2].metric("Win rate", num(s["win_rate"], "{:.1f}%"), f"{s['trades']} trades")
        c[3].metric("Max drawdown", money(s["max_drawdown"], True), f"{s['max_drawdown_pct']:.1f}%", delta_color="off")
        c = st.columns(4)
        c[0].metric("Avg trade", money(s["avg_trade"], True))
        c[1].metric("Avg win / avg loss", f"{money(s['avg_win'], True)} / {money(s['avg_loss'], True)}")
        c[2].metric("Sharpe (daily)", num(s["sharpe"]))
        c[3].metric("Winning days", num(s["win_days_pct"], "{:.0f}%"), f"{s['days_traded']} days traded")

        if t.empty:
            st.info("No trades in this period with these settings.")
        else:
            eq = equity_by_group(t)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=eq.index, y=eq["All"], name="All setups", line=dict(color=NEUTRAL, width=3)))
            for g in [g for g in ("HAL", "ORB", "IB") if g in eq.columns]:
                fig.add_trace(go.Scatter(x=eq.index, y=eq[g], name=GROUP_NAMES[g], line=dict(color=SERIES[g], width=2)))
            st.subheader("Equity curve (closed-trade P&L)")
            st.plotly_chart(style_fig(fig, 380, "P&L $"), use_container_width=True, theme="streamlit")

            dd = res.equity - res.equity.cummax()
            dd_daily = dd.groupby(dd.index.date).min()
            fig = go.Figure(go.Scatter(x=list(dd_daily.index), y=dd_daily.values, fill="tozeroy", name="Drawdown",
                                       line=dict(color=BAD, width=2)))
            st.subheader("Drawdown")
            st.plotly_chart(style_fig(fig, 220, "$"), use_container_width=True, theme="streamlit")

            dp = daily_pnl(t)
            fig = go.Figure(go.Bar(x=list(dp.index), y=dp.values, name="Daily P&L",
                                   marker_color=[GOOD if v > 0 else BAD for v in dp.values]))
            st.subheader("Daily P&L")
            st.plotly_chart(style_fig(fig, 240, "$"), use_container_width=True, theme="streamlit")

            st.subheader("By setup")
            st.dataframe(breakdown(t, "group").rename(index=GROUP_NAMES).style.format(
                {"Net P&L $": "{:,.0f}", "Win rate %": "{:.1f}", "Avg trade $": "{:,.1f}", "Profit factor": "{:.2f}"}),
                use_container_width=True)
            st.caption(f"Commission paid: {money(s['commission'], True)} | Largest win {money(s['largest_win'], True)} | "
                       f"Largest loss {money(s['largest_loss'], True)} | Max losing streak {s['max_consec_losses']} trades")

# ====================================================================== Trades
with tabs[1]:
    if res is None or res.trades.empty:
        st.info("Run a backtest to see the trade list.")
    else:
        t = res.trades.copy()
        pick = st.multiselect("Setups", sorted(t["group"].unique()), default=sorted(t["group"].unique()),
                              format_func=lambda g: GROUP_NAMES.get(g, g))
        t = t[t["group"].isin(pick)]
        view = pd.DataFrame({
            "Setup": t["group"].map(GROUP_NAMES), "Entry": t["note"], "Side": t["side"].map({1: "Long", -1: "Short"}),
            "Qty": t["qty"], "Entry time": t["entry_time"].dt.strftime("%Y-%m-%d %H:%M"),
            "Entry price": t["entry_price"], "Exit time": t["exit_time"].dt.strftime("%Y-%m-%d %H:%M"),
            "Exit price": t["exit_price"], "Exit": t["exit_reason"], "Points": t["pnl_points"],
            "Net P&L $": t["net_pnl"], "R": t["r_multiple"]})
        st.dataframe(view.style.format({"Entry price": "{:.2f}", "Exit price": "{:.2f}", "Points": "{:.2f}",
                                        "Net P&L $": "{:,.2f}", "R": "{:.2f}"}, na_rep="-"),
                     use_container_width=True, height=560, hide_index=True)
        st.download_button("Download trades (CSV)", view.to_csv(index=False).encode(), "trades.csv", "text/csv")

# ====================================================================== Chart
with tabs[2]:
    notes = [a for a in (res.annotations if res is not None else []) if a["kind"] == "note"]
    if res is None or (res.trades.empty and not notes):
        st.info("Run a backtest, then pick a day here to see its trades on the chart.")
    else:
        t = res.trades
        if t.empty:
            t = pd.DataFrame(columns=["group", "note", "side", "qty", "entry_time", "entry_price", "exit_time",
                                      "exit_price", "exit_reason", "net_pnl"]).astype(
                {"entry_time": f"datetime64[ns, {dstore.TZ}]", "exit_time": f"datetime64[ns, {dstore.TZ}]"})
        trade_days = set(t["entry_time"].dt.date)
        days = sorted(trade_days | {a["time"].date() for a in notes}, reverse=True)
        cc = st.columns([2, 1, 1])
        day = cc[0].selectbox("Day", days, format_func=lambda d: f"{d} ({d:%a})   " + (
            f"P&L {money(t[t['entry_time'].dt.date == d]['net_pnl'].sum())}" if d in trade_days else "no trades"))
        ctf = cc[1].selectbox("Candle size", [1, 5, 15], index=1)
        window = cc[2].selectbox("Show", ["Trades only", "Full day"], index=0)
        tday = t[t["entry_time"].dt.date == day]
        if window == "Trades only" and not tday.empty:
            lo = tday["entry_time"].min() - pd.Timedelta(minutes=90)
            hi = tday["exit_time"].max() + pd.Timedelta(minutes=45)
        else:
            lo = pd.Timestamp(day).tz_localize(dstore.TZ) - pd.Timedelta(hours=6)
            hi = pd.Timestamp(day).tz_localize(dstore.TZ) + pd.Timedelta(hours=17)
        b = res.bars[(res.bars.index >= lo) & (res.bars.index <= hi)]
        b = dstore.resample(b, ctf) if ctf > 1 else b
        fig = go.Figure(go.Candlestick(x=b.index, open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                                       name="Price", increasing_line_color=GOOD, decreasing_line_color=BAD))
        for a in res.annotations:
            at = a["time"]
            if not (lo <= at <= hi):
                continue
            col = SERIES.get(a["group"], NEUTRAL)
            if a["kind"] == "hal_range":
                fig.add_shape(type="rect", x0=at, x1=at + pd.Timedelta(minutes=15), y0=a["rL"], y1=a["rH"],
                              line=dict(color=col, width=1), fillcolor=col, opacity=0.15)
                for y in (a["rH"], a["rL"]):
                    fig.add_shape(type="line", x0=at, x1=hi, y0=y, y1=y, line=dict(color=col, width=1, dash="dot"))
            elif a["kind"] == "hal_order":
                end = min(hi, at + pd.Timedelta(hours=2))
                for y, dash in ((a["entry"], "solid"), (a["stop"], "dot"), (a["tp"], "dot")):
                    fig.add_shape(type="line", x0=at, x1=end, y0=y, y1=y, line=dict(color=col, width=1, dash=dash))
            elif a["kind"] == "setup":
                end = min(hi, at.normalize() + pd.Timedelta(hours=15, minutes=30))
                for y, dash, w in ((a["e1"], "solid", 1), (a["e2"], "dash", 1), (a["stop"], "dot", 1), (a["tp"], "dot", 1)):
                    if y is not None:
                        fig.add_shape(type="line", x0=at, x1=end, y0=y, y1=y, line=dict(color=col, width=w, dash=dash))
                fig.add_shape(type="rect", x0=at, x1=end, y0=a["rL"], y1=a["rH"], line=dict(width=0),
                              fillcolor=col, opacity=0.06)
        for _, r in tday.iterrows():
            col = SERIES.get(r["group"], NEUTRAL)
            fig.add_trace(go.Scatter(
                x=[r["entry_time"]], y=[r["entry_price"]], mode="markers", showlegend=False,
                marker=dict(symbol="triangle-up" if r["side"] == 1 else "triangle-down", size=13, color=col,
                            line=dict(width=2, color="white")),
                hovertemplate=f"{GROUP_NAMES[r['group']]} {r['note']}<br>{r['qty']} @ {r['entry_price']:.2f}<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=[r["exit_time"]], y=[r["exit_price"]], mode="markers", showlegend=False,
                marker=dict(symbol="x", size=11, color=col, line=dict(width=1, color="white")),
                hovertemplate=f"{r['exit_reason']} @ {r['exit_price']:.2f}<br>{money(r['net_pnl'])}<extra></extra>"))
            fig.add_trace(go.Scatter(x=[r["entry_time"], r["exit_time"]], y=[r["entry_price"], r["exit_price"]],
                                     mode="lines", showlegend=False, hoverinfo="skip",
                                     line=dict(color=col, width=1, dash="dot")))
        for g in sorted(tday["group"].unique()):
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=GROUP_NAMES[g],
                                     marker=dict(color=SERIES[g], size=10, symbol="triangle-up")))
        fig.update_layout(xaxis_rangeslider_visible=False)
        st.plotly_chart(style_fig(fig, 560), use_container_width=True, theme="streamlit")
        st.caption("Triangles = entries (up long, down short), x = exits. Solid line = first entry, dashed = "
                   "average-down entry, dotted = stop and target. Shaded box = the setup's range.")
        if not tday.empty:
            st.dataframe(tday[["group", "note", "qty", "entry_time", "entry_price", "exit_time", "exit_price",
                               "exit_reason", "net_pnl"]], hide_index=True, use_container_width=True)
        # ---- decision log: every step each setup took, including trades that did NOT happen and why
        st.subheader("What happened")
        st.caption("Every decision the strategy made from the previous evening to the close of this day - "
                   "use it to see why a trade was or was not taken. Times are New York time.")
        d0_ = pd.Timestamp(day).tz_localize(dstore.TZ) - pd.Timedelta(hours=6)
        d1_ = pd.Timestamp(day).tz_localize(dstore.TZ) + pd.Timedelta(hours=17)
        log = [a for a in notes if d0_ <= a["time"] <= d1_]
        if log:
            st.dataframe(pd.DataFrame({
                "Time (ET)": [a["time"].strftime("%a %H:%M") for a in log],
                "Setup": [GROUP_NAMES.get(a["group"], a["group"]) for a in log],
                "What happened": [a["text"] for a in log]}), hide_index=True, use_container_width=True,
                height=min(38 * (len(log) + 1), 600))
        else:
            st.write("Nothing happened in this window.")

# ====================================================================== Breakdown
with tabs[3]:
    if res is None or res.trades.empty:
        st.info("Run a backtest to see the breakdowns.")
    else:
        t = res.trades
        opts = {"Weekday": "weekday", "Month": "month", "Setup": "group", "Entry type": "note",
                "Exit reason": "exit_reason", "Hour of entry (ET)": "hour", "Long / short": "direction"}
        cc = st.columns([1, 1])
        by = cc[0].selectbox("Group trades by", list(opts))
        grp = cc[1].selectbox("Setup filter", ["All"] + sorted(t["group"].unique()),
                              format_func=lambda g: GROUP_NAMES.get(g, g))
        tt = t if grp == "All" else t[t["group"] == grp]
        tb = breakdown(tt, opts[by])
        if opts[by] == "group":
            tb = tb.rename(index=GROUP_NAMES)
        fig = go.Figure(go.Bar(x=[str(i) for i in tb.index], y=tb["Net P&L $"], name="Net P&L",
                               marker_color=[GOOD if v > 0 else BAD for v in tb["Net P&L $"]],
                               hovertemplate="%{x}: $%{y:,.0f}<extra></extra>"))
        st.plotly_chart(style_fig(fig, 320, "Net P&L $"), use_container_width=True, theme="streamlit")
        st.dataframe(tb.style.format({"Net P&L $": "{:,.0f}", "Win rate %": "{:.1f}", "Avg trade $": "{:,.1f}",
                                      "Profit factor": "{:.2f}"}), use_container_width=True)

# ====================================================================== Optimize
with tabs[4]:
    st.subheader("Parameter sweep")
    st.markdown("Test many values of one or two settings in one go. All other settings stay as in the sidebar.  \n"
                "**Warning:** the best row on past data is often luck (over-fitting). Prefer settings where the "
                "*neighbouring* values are also good, and confirm on a date range you did not optimise on.")
    if bars_all is None:
        st.info("Load a dataset first.")
    else:
        spec_by_name = {p.name: p for p in strategy_cls.params_spec if p.kind in ("int", "float", "choice", "bool")}
        labels = {n: f"{p.label}  [{p.group}]" for n, p in spec_by_name.items()}
        cc = st.columns(2)
        sel = []
        for k, col in enumerate(cc):
            with col:
                pn = st.selectbox(f"Setting {k + 1}", ["(none)"] + list(spec_by_name), key=f"opt_p{k}",
                                  format_func=lambda n: labels.get(n, n))
                if pn != "(none)":
                    p = spec_by_name[pn]
                    if p.kind == "choice":
                        vals = st.multiselect("Values", p.options, default=p.options, key=f"opt_v{k}")
                    elif p.kind == "bool":
                        vals = st.multiselect("Values", [True, False], default=[True, False], key=f"opt_v{k}")
                    else:
                        base = p.default
                        sug = sorted({round(base * m, 2) for m in (0.5, 0.75, 1, 1.25, 1.5)}) if base else [0, 1, 2]
                        txt = st.text_input("Values (comma separated)", ", ".join(str(v) for v in sug), key=f"opt_v{k}")
                        try:
                            vals = [int(float(x)) if p.kind == "int" else float(x) for x in txt.split(",") if x.strip()]
                        except ValueError:
                            st.error("Use numbers separated by commas.")
                            vals = []
                    sel.append((pn, vals))
        combos = list(itertools.product(*[v for _, v in sel])) if sel else []
        metric = st.selectbox("Rank by", ["Net profit", "Profit factor", "Sharpe", "Recovery factor (profit / max DD)"])
        st.caption(f"{len(combos)} combinations. Each one is a full backtest.")
        if st.button("Run sweep", disabled=not combos or len(combos) > 100):
            bars = selected_bars()
            rows = []
            prog = st.progress(0.0)
            for n, combo in enumerate(combos, 1):
                pp = dict(params)
                pp.update({name: v for (name, _), v in zip(sel, combo)})
                try:
                    r = run(bars, strategy_cls, pp, cfg)
                except ValueError:            # an impossible combination (e.g. pullback beyond the stop)
                    prog.progress(n / len(combos), text=f"{n}/{len(combos)}")
                    continue
                s = summarize(r.trades, r.equity, cfg.initial_capital)
                row = {spec_by_name[name].label: v for (name, _), v in zip(sel, combo)}
                row.update({"Net profit": s["net_pnl"], "Trades": s["trades"], "Win rate %": s["win_rate"],
                            "Profit factor": s["profit_factor"], "Max DD": s["max_drawdown"], "Sharpe": s["sharpe"],
                            "Recovery factor (profit / max DD)": s["recovery_factor"]})
                rows.append(row)
                prog.progress(n / len(combos), text=f"{n}/{len(combos)}")
            st.session_state["sweep"] = (pd.DataFrame(rows), [spec_by_name[n].label for n, _ in sel])
        if len(combos) > 100:
            st.warning("More than 100 combinations - narrow the values.")
        if "sweep" in st.session_state:
            df, cols = st.session_state["sweep"]
            if metric in df:
                df = df.sort_values(metric, ascending=False)
                st.dataframe(df.style.format({"Net profit": "{:,.0f}", "Max DD": "{:,.0f}", "Win rate %": "{:.1f}",
                                              "Profit factor": "{:.2f}", "Sharpe": "{:.2f}",
                                              "Recovery factor (profit / max DD)": "{:.2f}"}),
                             use_container_width=True, hide_index=True)
                if len(cols) == 2:
                    hm = df.pivot_table(index=cols[0], columns=cols[1], values=metric)
                    fig = go.Figure(go.Heatmap(z=hm.values, x=[str(c) for c in hm.columns], y=[str(i) for i in hm.index],
                                               colorscale=[[0, "#e34948"], [0.5, "#f0efec"], [1, "#2a78d6"]],
                                               zmid=0 if metric == "Net profit" else None,
                                               hovertemplate=f"{cols[0]}=%{{y}}<br>{cols[1]}=%{{x}}<br>{metric}=%{{z:,.2f}}<extra></extra>"))
                    fig.update_layout(xaxis_title=cols[1], yaxis_title=cols[0])
                    st.plotly_chart(style_fig(fig, 420), use_container_width=True, theme="streamlit")

# ====================================================================== Data
with tabs[5]:
    st.subheader("Your datasets")
    if datasets:
        info = []
        for n in datasets:
            i = dstore.dataset_info(n)
            info.append({"Dataset": n, "Bars": f"{i['bars']:,}", "From": f"{i['start']:%Y-%m-%d}",
                         "To": f"{i['end']:%Y-%m-%d %H:%M}", "Bar size (min)": i["bar_minutes"],
                         "Type": "FAKE demo" if i["demo"] else "real"})
        st.dataframe(pd.DataFrame(info), hide_index=True, use_container_width=True)
    else:
        st.info("No datasets yet.")

    st.subheader("Download from Interactive Brokers")
    with st.expander("First time? Set up TWS / IB Gateway (2 minutes)"):
        st.markdown(
            "1. Open **TWS** (or **IB Gateway**) and log in - the paper account works.\n"
            "2. TWS: **File > Global Configuration > API > Settings**\n"
            "   - tick **Enable ActiveX and Socket Clients**\n"
            "   - note the **Socket port** (TWS paper 7497, live 7496; Gateway paper 4002, live 4001)\n"
            "   - tick **Read-Only API** (this tool only reads data - it never trades)\n"
            "   - make sure **127.0.0.1** is in *Trusted IPs*\n"
            "3. You need the real-time **CME futures market data** subscription on the account.\n"
            "4. IBKR keeps about **2 years** of expired futures history. 1-minute bars for a year take roughly "
            "15-30 minutes to download (IBKR limits the request rate). Later runs only fetch new bars.")
    c = st.columns(4)
    dl_sym = c[0].selectbox("Symbol", list(CONTRACTS), key="dl_sym")
    dl_bar = c[1].selectbox("Bar size", list(BAR_SIZES), key="dl_bar")
    dl_days = c[2].number_input("Days of history", value=365, min_value=5, max_value=800, step=30, key="dl_days")
    dl_port = c[3].number_input("Port", value=7497, step=1, key="dl_port")
    c = st.columns(4)
    dl_host = c[0].text_input("Host", "127.0.0.1", key="dl_host")
    dl_cid = c[1].number_input("Client ID", value=17, step=1, key="dl_cid")
    dl_pause = c[2].number_input("Seconds between requests", value=10.0, step=1.0, min_value=1.0, key="dl_pause",
                                 help="IBKR allows about 60 history requests per 10 minutes. 10s is safe.")
    dl_full = c[3].checkbox("Re-download everything", value=False, key="dl_full")
    if st.button("Download from IBKR", type="primary"):
        cmd = [sys.executable, "-u", "-m", "backtester.ibkr", "--symbol", dl_sym, "--bar", dl_bar,
               "--days", str(int(dl_days)), "--host", dl_host, "--port", str(int(dl_port)),
               "--client-id", str(int(dl_cid)), "--exchange", CONTRACTS[dl_sym].exchange, "--pause", str(dl_pause)]
        if dl_full:
            cmd.append("--full")
        prog = st.progress(0.0, text="Connecting...")
        box = st.empty()
        lines = []
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("PROGRESS"):
                a, b = line.split()[1].split("/")
                prog.progress(int(a) / max(int(b), 1), text=line.split(" ", 2)[-1])
            lines.append(line)
            box.code("\n".join(lines[-25:]))
        proc.wait()
        if proc.returncode == 0:
            st.success("Download finished. Pick the dataset in the sidebar.")
            st.cache_data.clear()
        else:
            st.error("Download failed - see the log above. Is TWS/Gateway running with the API enabled?")

    st.subheader("Import a CSV")
    st.markdown("Works with **TradingView** chart exports (chart menu > *Export chart data*), **Databento** OHLCV "
                "files and most CSVs with a time column plus open / high / low / close.")
    up = st.file_uploader("CSV file", type=["csv"])
    if up is not None:
        c = st.columns(3)
        name = c[0].text_input("Save as", Path(up.name).stem.replace(" ", "_"))
        naive_tz = c[1].selectbox("Times without a timezone are in", [dstore.TZ, "UTC", "Europe/London", "Asia/Kolkata"])
        merge = c[2].checkbox("Add to an existing dataset with this name", value=True)
        try:
            df = dstore.read_csv_bars(up.getvalue(), naive_tz)
            st.write(f"{len(df):,} bars, {df.index[0]} -> {df.index[-1]}")
            st.dataframe(df.head(), use_container_width=True)
            if st.button("Save dataset"):
                old = dstore.load_bars(name) if merge and dstore.dataset_path(name).exists() else None
                dstore.save_bars(dstore.merge_bars(old, df), name)
                st.cache_data.clear()
                st.success(f"Saved data/{name}.parquet")
                st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read this file: {e}")

    st.subheader("Demo data")
    if st.button("Create / refresh demo data (fake prices)"):
        dstore.save_bars(dstore.make_demo_data(days=260), "DEMO_MNQ_1min")
        st.cache_data.clear()
        st.rerun()

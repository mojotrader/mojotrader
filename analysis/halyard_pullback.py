"""Halyard MNQ 15m -- how far does price pull back after the entry signal?

Replays the Halyard strategy (pinescript logic, default settings) on a
TradingView 15-minute CSV export, then asks, for every trade it takes:

  * Pullback depth: after the signal candle closes, how far does price move
    AGAINST the trade before the trade is decided (target hit, stop hit or
    day-flat)?  Measured as a % of the distance signal-close -> stop (= 100%).
  * What if the entry were a LIMIT order X% back into that distance instead of
    a market order at the close?  Fill rate, win rate and P&L for each X.

Timing (fixed in UTC, which is what the script's IST anchoring works out to):
  range candle     = the 15m bar opening 05:00 UTC
  session roll     = 18:30 UTC (13:30 EST / 14:30 EDT); flat on the bar before it

Intrabar order is unknown on 15m data, so the limit test is deliberately
conservative:
  * a limit fills only if price TRADES THROUGH it by at least 1 tick
  * fills are only possible from the bar AFTER the signal candle
  * on the bar that fills, a touch of the stop counts as a loss; a target on
    that same bar only counts if TradingView's bar-path rule (open nearer the
    low -> low first) says the dip came before it
  * if price reaches the original target before the limit fills, the order is
    cancelled (the move left without us) -> counted as a missed trade

Usage:  python3 analysis/halyard_pullback.py path/to/export.csv
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

TICK = 0.25
PT_VAL = 2.0          # MNQ $ per point
RISK_USD = 535.0      # the script's default $ risk per trade
RR_FIRST = 0.8
RR_REV = 1.0
DAY_MODE = {0: "Long", 1: "Both", 2: "Both", 3: "Both", 4: "Both"}  # Mon..Fri
ORB_UTC_MIN = 5 * 60
ROLL_UTC_MIN = 18 * 60 + 30


def load(path):
    bars = []
    with open(path) as f:
        for r in csv.DictReader(f):
            t = datetime.fromtimestamp(int(r["time"]), timezone.utc)
            bars.append(dict(t=t, o=float(r["open"]), h=float(r["high"]),
                             l=float(r["low"]), c=float(r["close"])))
    for b in bars:
        b["key"] = (b["t"] + timedelta(minutes=24 * 60 - ROLL_UTC_MIN)).date()
    return bars


def tv_exit(b, d, stop, tp):
    """TradingView's bar-path rule for a bar that can hit stop and/or target.
    Returns ('sl'|'tp', price) or None."""
    hit_sl = b["l"] <= stop if d == 1 else b["h"] >= stop
    hit_tp = b["h"] >= tp if d == 1 else b["l"] <= tp
    if hit_sl and hit_tp:
        high_first = abs(b["o"] - b["h"]) < abs(b["o"] - b["l"])
        up_first = high_first
        # long: tp is up; short: tp is down
        tp_first = up_first if d == 1 else not up_first
        return ("tp", tp) if tp_first else ("sl", stop)
    if hit_sl:
        return ("sl", stop)
    if hit_tp:
        return ("tp", tp)
    return None


def replay(bars):
    """Baseline Halyard: market entry at the signal close. Returns trades."""
    trades = []
    by_day = defaultdict(list)
    for i, b in enumerate(bars):
        by_day[b["key"]].append(i)

    for key, idx in by_day.items():
        dow = key.weekday()
        mode = DAY_MODE.get(dow, "Off")
        if mode == "Off":
            continue
        long_ok = mode in ("Both", "Long")
        short_ok = mode in ("Both", "Short")
        orb = next((i for i in idx if bars[i]["t"].hour * 60 + bars[i]["t"].minute == ORB_UTC_MIN), None)
        if orb is None:
            continue
        hi, lo = bars[orb]["h"], bars[orb]["l"]
        last = idx[-1]
        state, first_dir = 0, 0
        pos = None
        for i in range(orb + 1, last + 1):
            b = bars[i]
            closed_now = False
            if pos:  # exits for an open position
                ex = tv_exit(b, pos["d"], pos["sl"], pos["tp"])
                if ex:
                    pos.update(exit_i=i, result=ex[0], exit_px=ex[1])
                    trades.append(pos)
                    pos = None
                    closed_now = True
                elif i == last:
                    pos.update(exit_i=i, result="flat", exit_px=b["c"])
                    trades.append(pos)
                    pos = None
                    closed_now = True
                if closed_now and state == 1:
                    state = 2  # Loss or Target -> reversal arms either way
            c = b["c"]
            if i == last:
                continue  # a fill at the last bar's close would be flattened at once
            if state == 0 and (c > hi or c < lo):
                d = 1 if c > hi else -1
                state = -1  # break consumed; becomes 1 if it trades
                if (d == 1 and long_ok) or (d == -1 and short_ok):
                    pos = make_trade(bars, i, d, c, lo if d == 1 else hi, RR_FIRST, "first", last)
                    state, first_dir = 1, d
            elif state == 2 and pos is None:
                if first_dir == -1 and c > hi and long_ok:
                    pos = make_trade(bars, i, 1, c, lo, RR_REV, "reversal", last)
                    state = 3
                elif first_dir == 1 and c < lo and short_ok:
                    pos = make_trade(bars, i, -1, c, hi, RR_REV, "reversal", last)
                    state = 3
    return trades


def make_trade(bars, i, d, entry, stop, rr, kind, last):
    risk = abs(entry - stop)
    return dict(i=i, d=d, entry=entry, sl=stop, tp=entry + d * risk * rr,
                risk=risk, rr=rr, kind=kind, last_i=last)


def pullback_depth(bars, t):
    """Deepest adverse move after the signal close, as % of risk, measured
    until the baseline trade is decided. A stopped trade is by definition 100%."""
    worst = 0.0
    for j in range(t["i"] + 1, t["exit_i"] + 1):
        b = bars[j]
        if j == t["exit_i"] and t["result"] == "tp":
            break  # target bar: the dip may have come AFTER the target, so skip it
        adverse = (t["entry"] - b["l"]) if t["d"] == 1 else (b["h"] - t["entry"])
        worst = max(worst, adverse)
    return min(worst / t["risk"], 1.0) * 100 if t["risk"] > 0 else 0.0


def market(t, slip_ticks=0):
    """Baseline market fill at the signal close, minus slip_ticks of slippage."""
    slip = slip_ticks * TICK
    L = t["entry"] - t["d"] * (-slip)          # worse price by `slip`
    risk = abs(L - t["sl"])
    pts = t["d"] * (t["exit_px"] - L)
    return dict(r=pts / risk, pts=pts)


def limit_test(bars, t, p, keep_tp_price, wait_bars):
    """Simulate a limit entry p (0..1) of the way from signal close to stop.
    Returns None (no fill) or dict(r=R-multiple at constant $ risk, pts=points/1ct)."""
    d, stop = t["d"], t["sl"]
    L = t["entry"] - d * p * t["risk"]
    L = round(L / TICK) * TICK
    risk = abs(L - stop)
    if risk <= 0:
        return None
    tp = t["tp"] if keep_tp_price else L + d * risk * t["rr"]
    orig_tp = t["tp"]
    last = t["last_i"]
    filled_at = None
    j = t["i"] + 1
    if filled_at is None:
        while j <= last and j <= t["i"] + wait_bars:
            b = bars[j]
            if j == last:
                return None
            through = b["l"] <= L - TICK if d == 1 else b["h"] >= L + TICK
            # did price run to the original target on this bar before us?
            ran = b["h"] >= orig_tp if d == 1 else b["l"] <= orig_tp
            if through:
                if ran and not through_first(b, d):
                    return None
                filled_at = j
                break
            if ran:
                return None  # target reached without a pullback -> cancelled
            j += 1
        if filled_at is None:
            return None
        b = bars[filled_at]
        hit_sl = b["l"] <= stop if d == 1 else b["h"] >= stop
        if hit_sl:
            return dict(r=-1.0, pts=-risk, fill_i=filled_at)
        # dip came first on this bar (TradingView path rule), so a target
        # printed later in the same bar counts
        hit_tp = b["h"] >= tp if d == 1 else b["l"] <= tp
        if hit_tp and through_first(b, d):
            pts = d * (tp - L)
            return dict(r=pts / risk, pts=pts, fill_i=filled_at)
        if filled_at == last:
            pts = d * (b["c"] - L)
            return dict(r=pts / risk, pts=pts, fill_i=filled_at)
    for k in range(filled_at + 1, last + 1):
        ex = tv_exit(bars[k], d, stop, tp)
        if ex:
            pts = d * (ex[1] - L)
            return dict(r=pts / risk, pts=pts, fill_i=filled_at)
    pts = d * (bars[last]["c"] - L)
    return dict(r=pts / risk, pts=pts, fill_i=filled_at)


def through_first(b, d):
    """On a bar that both reaches the target and trades through the limit,
    TradingView's path rule decides which came first."""
    high_first = abs(b["o"] - b["h"]) < abs(b["o"] - b["l"])
    return (not high_first) if d == 1 else high_first


def dollars(r_mult, risk_pts):
    qty = max(int(RISK_USD // (risk_pts * PT_VAL)), 1)
    return r_mult * risk_pts * PT_VAL * qty


def main(path):
    bars = load(path)
    trades = replay(bars)
    # If the export carries TradingView's own reversal markers, keep only the
    # reversals TradingView actually took (the replay adds a handful of late-day
    # after-target reversals that the chart did not show).
    with open(path) as f:
        rows = list(csv.DictReader(f))
    if rows and "Reversal Long" in rows[0]:
        tv = {i for i, r in enumerate(rows)
              if float(r["Reversal Long"] or 0) + float(r["Reversal Short"] or 0) > 0}
        dropped = [t for t in trades if t["kind"] == "reversal" and t["i"] not in tv]
        trades = [t for t in trades if t not in dropped]
        print(f"Matched TradingView markers; dropped {len(dropped)} reversal(s) TV did not take")
    n = len(trades)
    print(f"Data: {bars[0]['t']:%Y-%m-%d} .. {bars[-1]['t']:%Y-%m-%d}   bars={len(bars)}")
    for kind in ("first", "reversal"):
        ts = [t for t in trades if t["kind"] == kind]
        w = sum(t["result"] == "tp" for t in ts)
        print(f"  {kind:9s} trades={len(ts):4d}  targets={w}  stops={sum(t['result']=='sl' for t in ts)}  flat={sum(t['result']=='flat' for t in ts)}")

    # ---- 1. Pullback depth distribution ------------------------------------
    for t in trades:
        t["pb"] = pullback_depth(bars, t)
    groups = [("ALL trades", trades),
              ("Winners (target hit)", [t for t in trades if t["result"] == "tp"]),
              ("First trades", [t for t in trades if t["kind"] == "first"]),
              ("Reversals", [t for t in trades if t["kind"] == "reversal"])]
    levels = [1, 2, 3, 5, 10, 15, 20, 25, 30, 40, 50]
    print("\n1) HOW OFTEN DOES PRICE PULL BACK AT LEAST X% OF (close -> stop) AFTER THE SIGNAL?")
    print("   (until the trade is decided; a stop-out counts as 100%; the target bar is ignored)")
    print("   group".ljust(26) + "".join(f"{x:>5d}%" for x in levels))
    for name, g in groups:
        row = "".join(f"{100*sum(t['pb'] >= x for t in g)/len(g):5.0f}%" for x in levels)
        print(f"   {name:23s}{row}   (n={len(g)})")
    no5 = [t for t in trades if t["pb"] < 5]
    print(f"   Trades that never pulled back 5%: {len(no5)}, of which winners: "
          f"{sum(t['result'] == 'tp' for t in no5)}")
    win = sorted(t["pb"] for t in trades if t["result"] == "tp")
    print(f"   Median pullback of a winner: {win[len(win)//2]:.0f}%")
    avg_risk = sum(t["risk"] for t in trades) / len(trades)
    print(f"   Average close->stop distance: {avg_risk:.1f} pts ({avg_risk/TICK:.0f} ticks)")

    def summary(res):
        f = [(t, r) for t, r in zip(trades, res) if r]
        totR = sum(r["r"] for _, r in f)
        wins = sum(r["r"] > 0 for _, r in f)
        return len(f), wins, totR, sum(r["pts"] for _, r in f)

    # ---- 2. Market order with slippage (the thing a limit would replace) ---
    print("\n2) MARKET ORDER AT THE CLOSE, WITH SLIPPAGE (R = risk units at constant $ risk)")
    print("   slippage   trades  win%   totalR   avgR   pts(1ct)")
    for k in (0, 1, 2, 4, 8, 12):
        n_, w, R, pts = summary([market(t, k) for t in trades])
        print(f"   {k:3d} ticks  {n_:6d}  {100*w/n_:4.0f}%  {R:+7.1f}  {R/n_:+.3f}  {pts:+8.0f}")

    # ---- 3. Limit-entry backtest -------------------------------------------
    pulls = (0, 1, 2, 3, 5, 10, 15, 20, 25, 30, 40, 50)
    for keep in (True, False):
        label = ("TARGET KEPT AT THE ORIGINAL PRICE" if keep
                 else "TARGET RE-CALCULATED FROM THE LIMIT PRICE (same R:R)")
        for wait in (1, 2, 4, 999):
            wtxt = "until the trade would have ended" if wait == 999 else f"{wait} bar(s) = {15*wait} min"
            print(f"\n3) LIMIT ENTRY -- {label}; order waits {wtxt}")
            print("   pullback  fills  fill%  win%   totalR   avgR/fill  pts(1ct)")
            for p in pulls:
                n_, w, R, pts = summary([limit_test(bars, t, p / 100, keep, wait) for t in trades])
                if n_:
                    print(f"   {p:5d}%   {n_:5d}  {100*n_/n:4.0f}%  {100*w/n_:4.0f}%  {R:+7.1f}  {R/n_:+9.3f}  {pts:+8.0f}")


if __name__ == "__main__":
    main(sys.argv[1])

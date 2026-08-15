"""
Second pass on the session reversal idea: is the London fade's positive expectancy real,
or is it a handful of outliers?

The first pass found:
    London fading the Asia sweep   +0.07 R over ~400 trades, stable across 4 window sets
    NY fading the London sweep     negative everywhere
    going WITH the sweep           clearly negative everywhere
    capping the target at 1-3R     turns London negative too

That last line is the warning: if the edge only exists with an uncapped target, it lives in
the right tail, and a right-tail edge measured over 400 trades needs a much harder look than
a mean does. This script provides it:

    1. the R distribution and what the top handful of trades contribute
    2. a bootstrap confidence interval on expectancy
    3. conditioning tables — sweep timing, range size, sweep depth, weekday
    4. the same for NY, to confirm it is not just underpowered

Usage:  python3 research/session_edge_detail.py <csv> [--preset aln]
"""

import argparse
from datetime import timedelta

import numpy as np
import pandas as pd

from session_reversal_study import ET, PRESETS, hours_in, load, walk


def build(df, preset, tick=0.25, buffer_ticks=0.0):
    """One row per sweep-and-fade opportunity, with the context needed to condition on."""
    cfg = PRESETS[preset]
    hset = {k: set(hours_in(cfg[k])) for k in ("asia", "london", "ny")}
    need = {k: max(2, len(hours_in(cfg[k])) - 1) for k in ("asia", "london", "ny")}

    rows = []
    for day, g in df.groupby("day", sort=True):
        s = {k: g[g["hour"].isin(hset[k])] for k in hset}
        if any(len(s[k]) < need[k] for k in s):
            continue
        aH, aL = s["asia"]["high"].max(), s["asia"]["low"].min()
        lH, lL = s["london"]["high"].max(), s["london"]["low"].min()

        for pair, rng, drv in (("London/Asia", (aH, aL), s["london"]),
                               ("NY/London", (lH, lL), s["ny"])):
            hi, lo = rng
            w = walk(drv, hi, lo)
            if w["first"] is None:
                continue
            i0 = w["first_i"]
            H, L, C = drv["high"].to_numpy(), drv["low"].to_numpy(), drv["close"].to_numpy()
            swept_hi = w["first"] == 1

            # rejection entry: the sweep bar must close back inside the range
            if (C[i0] > hi) if swept_hi else (C[i0] < lo):
                continue
            if i0 + 1 >= len(drv):
                continue

            entry = C[i0]
            ext = H[i0] if swept_hi else L[i0]
            stop = ext + buffer_ticks * tick if swept_hi else ext - buffer_ticks * tick
            target = lo if swept_hi else hi
            d = -1 if swept_hi else 1
            risk = abs(entry - stop)
            reward = (target - entry) * d
            if risk <= 0 or reward <= 0:
                continue

            r, how, mfe = None, None, 0.0
            for i in range(i0 + 1, len(drv)):
                mfe = max(mfe, (H[i] - entry) * d if d == 1 else (entry - L[i]) * d * -1)
                mfe = max(mfe, ((H[i] if d == 1 else L[i]) - entry) * d)
                if (H[i] >= stop) if d == -1 else (L[i] <= stop):
                    r, how = -1.0, "stop"
                    break
                if (L[i] <= target) if d == -1 else (H[i] >= target):
                    r, how = reward / risk, "target"
                    break
            if r is None:
                r, how = (C[-1] - entry) * d / risk, "session end"

            rows.append({
                "day": day, "pair": pair, "dir": d, "r": r, "how": how,
                "risk": risk, "reward_r": reward / risk,
                "range_pts": hi - lo,
                "sweep_bar": i0, "bars_left": len(drv) - 1 - i0,
                "sweep_depth": abs(ext - (hi if swept_hi else lo)),
                "sweep_depth_pct": 100 * abs(ext - (hi if swept_hi else lo)) / (hi - lo),
                "close_back_pct": 100 * abs(entry - (hi if swept_hi else lo)) / (hi - lo),
                "mfe_r": mfe / risk,
                "swept_hi": swept_hi,
                "dow": pd.Timestamp(day).dayofweek,
            })
    return pd.DataFrame(rows)


def bootstrap(x, n=20000, seed=7):
    rng = np.random.default_rng(seed)
    x = np.asarray(x)
    m = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return np.percentile(m, [2.5, 50, 97.5])


def tail_check(d, name):
    r = d["r"].to_numpy()
    order = np.sort(r)[::-1]
    lo, mid, hi = bootstrap(r)
    print(f"\n{name}   n={len(r)}   exp {r.mean():+.3f} R   total {r.sum():+.1f} R")
    print(f"  bootstrap 95% CI on expectancy : [{lo:+.3f}, {hi:+.3f}]  (median {mid:+.3f})")
    print(f"  {'is it distinguishable from 0?':<33}" +
          ("NO — the interval straddles zero" if lo < 0 < hi else "yes"))
    for k in (1, 3, 5, 10, 20):
        if k < len(r):
            trimmed = np.sort(r)[:-k]
            print(f"  drop the top {k:>2} winners        : exp {trimmed.mean():+.3f} R"
                  f"   (top {k} alone = {order[:k].sum():+.1f} R, "
                  f"{100 * order[:k].sum() / max(r.sum(), 1e-9):.0f}% of the total)")
    print(f"  best trade {order[0]:+.1f} R   worst {order[-1]:+.1f} R"
          f"   winners avg {r[r > 0].mean():+.2f} R ({100 * (r > 0).mean():.0f}%)")


def cond(d, col, bins, labels, name):
    d = d.copy()
    d["b"] = pd.cut(d[col], bins=bins, labels=labels, include_lowest=True)
    print(f"\n  by {name}:")
    print(f"    {'bucket':<18}{'n':>5}{'win%':>8}{'exp R':>9}{'total R':>10}{'tgt%':>7}")
    for b, g in d.groupby("b", observed=True):
        if len(g) < 20:
            continue
        print(f"    {str(b):<18}{len(g):>5}{100 * (g['r'] > 0).mean():>7.1f}%"
              f"{g['r'].mean():>9.3f}{g['r'].sum():>10.1f}"
              f"{100 * (g['how'] == 'target').mean():>6.0f}%")


def by_cat(d, col, name, names=None):
    print(f"\n  by {name}:")
    print(f"    {'bucket':<18}{'n':>5}{'win%':>8}{'exp R':>9}{'total R':>10}{'tgt%':>7}")
    for b, g in d.groupby(col, observed=True):
        if len(g) < 20:
            continue
        lab = names[b] if names else str(b)
        print(f"    {lab:<18}{len(g):>5}{100 * (g['r'] > 0).mean():>7.1f}%"
              f"{g['r'].mean():>9.3f}{g['r'].sum():>10.1f}"
              f"{100 * (g['how'] == 'target').mean():>6.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--preset", default="aln")
    args = ap.parse_args()

    df = load(args.csv)
    d = build(df, args.preset)
    DOW = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 6: "Sun"}

    for pair in ("London/Asia", "NY/London"):
        p = d[d["pair"] == pair]
        print("\n" + "=" * 78)
        print(f"{pair}  —  fade the sweep, target the opposite side")
        print("=" * 78)
        tail_check(p, "all trades")

        print(f"\n  reward available at entry: median {p['reward_r'].median():.2f} R"
              f"   (25th {p['reward_r'].quantile(.25):.2f} / 75th {p['reward_r'].quantile(.75):.2f})")
        print(f"  max favourable excursion : median {p['mfe_r'].median():.2f} R"
              f"   reached 1R on {100 * (p['mfe_r'] >= 1).mean():.0f}%"
              f"   2R on {100 * (p['mfe_r'] >= 2).mean():.0f}%"
              f"   3R on {100 * (p['mfe_r'] >= 3).mean():.0f}%")

        cond(p, "sweep_bar", [-0.5, 0.5, 1.5, 2.5, 3.5, 99],
             ["bar 1", "bar 2", "bar 3", "bar 4", "bar 5+"], "which bar of the session swept")
        cond(p, "reward_r", [0, 1, 2, 3, 5, 999],
             ["<1R", "1-2R", "2-3R", "3-5R", "5R+"], "reward available at entry")
        cond(p, "sweep_depth_pct", [0, 5, 10, 20, 999],
             ["<5% of range", "5-10%", "10-20%", "20%+"], "how deep the sweep went")
        cond(p, "range_pts", list(p["range_pts"].quantile([0, .25, .5, .75, 1.0])),
             ["Q1 small", "Q2", "Q3", "Q4 large"], "range size")
        by_cat(p, "dow", "weekday", DOW)
        by_cat(p, "swept_hi", "which side was swept", {True: "high (short)", False: "low (long)"})


if __name__ == "__main__":
    main()

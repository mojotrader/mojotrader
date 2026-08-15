"""
Third pass: give the session reversal concept every reasonable chance before calling it.

The full-range target is greedy and the fixed R-multiple targets were worse. Two variants
are still untested and both are plausible readings of "travels to the other side":

    RETRACEMENT   target a fraction of the range instead of the whole thing — 25%, 50%,
                  75% of the way back across, plus the far side (100%) and the midpoint.
    NO STOP       hold to the session close with no stop at all. This strips the trade
                  mechanics away and measures the only thing that matters underneath:
                  is there directional drift after a session sweeps the previous range?

If the no-stop version has no drift, nothing built on top of it can have an edge, and the
question is settled regardless of how the stop and target are arranged.

Usage:  python3 research/session_target_grid.py <csv> [--preset aln]
"""

import argparse

import numpy as np
import pandas as pd

from session_reversal_study import PRESETS, hours_in, load, walk


def opportunities(df, preset):
    """Every rejection-entry sweep, with the raw path that follows it."""
    cfg = PRESETS[preset]
    hset = {k: set(hours_in(cfg[k])) for k in ("asia", "london", "ny")}
    need = {k: max(2, len(hours_in(cfg[k])) - 1) for k in ("asia", "london", "ny")}

    out = []
    for day, g in df.groupby("day", sort=True):
        s = {k: g[g["hour"].isin(hset[k])] for k in hset}
        if any(len(s[k]) < need[k] for k in s):
            continue
        aH, aL = s["asia"]["high"].max(), s["asia"]["low"].min()
        lH, lL = s["london"]["high"].max(), s["london"]["low"].min()

        for pair, (hi, lo), drv in (("London/Asia", (aH, aL), s["london"]),
                                    ("NY/London", (lH, lL), s["ny"])):
            w = walk(drv, hi, lo)
            if w["first"] is None:
                continue
            i0 = w["first_i"]
            H, L, C = drv["high"].to_numpy(), drv["low"].to_numpy(), drv["close"].to_numpy()
            swept_hi = w["first"] == 1
            if (C[i0] > hi) if swept_hi else (C[i0] < lo):
                continue                                   # a break, not a rejection
            if i0 + 1 >= len(drv):
                continue
            entry = C[i0]
            ext = H[i0] if swept_hi else L[i0]
            d = -1 if swept_hi else 1
            risk = abs(entry - ext)
            if risk <= 0:
                continue
            out.append({
                "day": day, "pair": pair, "d": d, "entry": entry, "stop": ext, "risk": risk,
                "hi": hi, "lo": lo, "swept_hi": swept_hi,
                "H": H[i0 + 1:], "L": L[i0 + 1:], "C": C[i0 + 1:],
            })
    return out


def run(ops, frac, use_stop=True):
    """frac = how far across the range to target. 1.0 = the far side."""
    rs = []
    for o in ops:
        d, entry, stop, risk = o["d"], o["entry"], o["stop"], o["risk"]
        span = o["hi"] - o["lo"]
        level = o["hi"] if o["swept_hi"] else o["lo"]
        target = level - d * -1 * frac * span if False else level + d * frac * span
        reward = (target - entry) * d
        if reward <= 0:
            continue
        r = None
        for i in range(len(o["H"])):
            if use_stop and ((o["H"][i] >= stop) if d == -1 else (o["L"][i] <= stop)):
                r = -1.0
                break
            if (o["L"][i] <= target) if d == -1 else (o["H"][i] >= target):
                r = reward / risk
                break
        if r is None:
            r = (o["C"][-1] - entry) * d / risk if len(o["C"]) else 0.0
        rs.append(r)
    return np.array(rs)


def drift(ops):
    """No stop, no target — just hold to the session close. Pure directional drift."""
    rows = []
    for o in ops:
        if not len(o["C"]):
            continue
        pts = (o["C"][-1] - o["entry"]) * o["d"]
        rows.append({"pair": o["pair"], "pts": pts, "r": pts / o["risk"],
                     "swept_hi": o["swept_hi"]})
    return pd.DataFrame(rows)


def boot(x, n=20000, seed=11):
    rng = np.random.default_rng(seed)
    m = rng.choice(np.asarray(x), size=(n, len(x)), replace=True).mean(axis=1)
    return np.percentile(m, [2.5, 97.5])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--preset", default="aln")
    args = ap.parse_args()

    ops = opportunities(load(args.csv), args.preset)

    for pair in ("London/Asia", "NY/London"):
        sub = [o for o in ops if o["pair"] == pair]
        print("\n" + "=" * 78)
        print(f"{pair}   {len(sub)} rejection sweeps")
        print("=" * 78)

        print("\n  TARGET = a fraction of the way back across the range (stop at the sweep wick)")
        print(f"    {'target':<14}{'n':>5}{'win%':>8}{'exp R':>9}{'total R':>10}{'95% CI':>22}")
        for frac, name in ((0.25, "25% back"), (0.5, "midpoint"), (0.75, "75% back"),
                           (1.0, "far side"), (1.5, "far side +50%")):
            r = run(sub, frac)
            if len(r) < 30:
                continue
            lo, hi = boot(r)
            print(f"    {name:<14}{len(r):>5}{100 * (r > 0).mean():>7.1f}%"
                  f"{r.mean():>9.3f}{r.sum():>10.1f}   [{lo:+.3f}, {hi:+.3f}]")

        print("\n  SAME TARGETS, NO STOP (hold to the session close)")
        print(f"    {'target':<14}{'n':>5}{'win%':>8}{'exp R':>9}{'total R':>10}{'95% CI':>22}")
        for frac, name in ((0.5, "midpoint"), (1.0, "far side")):
            r = run(sub, frac, use_stop=False)
            if len(r) < 30:
                continue
            lo, hi = boot(r)
            print(f"    {name:<14}{len(r):>5}{100 * (r > 0).mean():>7.1f}%"
                  f"{r.mean():>9.3f}{r.sum():>10.1f}   [{lo:+.3f}, {hi:+.3f}]")

    print("\n" + "=" * 78)
    print("PURE DRIFT — no stop, no target, hold from the sweep bar's close to the")
    print("session close. If this is zero, there is nothing underneath the model.")
    print("=" * 78)
    dd = drift(ops)
    print(f"\n  {'pair':<14}{'side swept':<14}{'n':>5}{'win%':>8}{'mean pts':>11}{'95% CI (pts)':>24}")
    for pair in ("London/Asia", "NY/London"):
        for side, lab in ((None, "either"), (True, "high -> short"), (False, "low -> long")):
            g = dd[dd["pair"] == pair]
            if side is not None:
                g = g[g["swept_hi"] == side]
            if len(g) < 30:
                continue
            lo, hi = boot(g["pts"].to_numpy())
            flag = "" if lo < 0 < hi else "   <-- excludes zero"
            print(f"  {pair:<14}{lab:<14}{len(g):>5}{100 * (g['pts'] > 0).mean():>7.1f}%"
                  f"{g['pts'].mean():>11.2f}   [{lo:+7.2f}, {hi:+7.2f}]{flag}")


if __name__ == "__main__":
    main()

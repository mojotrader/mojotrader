"""
Session reversal study — does one session take one side of the previous session's range
and then travel to the other side?

    Asia range   -> London is the driver
    London range -> New York is the driver

Session windows follow the ALN profiler (America/New_York):

    Asia    20:00 - 02:00
    London  02:00 - 08:00
    NY      08:00 - 16:00

The trading day runs 18:00 ET -> 17:00 ET, so an evening Asia session is grouped with the
London and New York sessions that follow it.

Three things are measured.

1. RANGE RELATIONSHIP — the ALN profiler's four patterns:
       Engulf        the driver took BOTH sides of the range
       Inside        the driver took NEITHER side
       Partial Up    took the high only
       Partial Down  took the low only

2. SWEEP AND REVERSE — the actual claim. Given the driver takes a side FIRST, how often
   does it then reach the OPPOSITE side before the session ends? Compared against the
   unconditional base rate, because "98% of the time NY takes the London high or low" is
   not an edge if NY takes a side almost every day regardless.

3. THE TRADE — fade the sweep, target the opposite side. This is a separate question from
   (2): the stop sits beyond the sweep extreme and can be hit before the target.

Bars are 1h, so intrabar order is unknown. Every ambiguity resolves AGAINST the model: a
bar that touches both the stop and the target is scored a loss, and a bar that pierces
both sides of the range at once is scored as having reached the far side immediately (so
it counts as a "reverse" for the statistic but the trade is judged on the bar's own path).
These numbers are biased pessimistic on the trade and optimistic on the raw statistic —
which is the point: the gap between them is where the 1h resolution hides.

Usage:  python3 research/session_reversal_study.py <csv> [--preset aln] [--json out.json]
"""

import argparse
import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")

# (start_hour, end_hour) in ET. A bar belongs to a session if its OPEN hour falls in the
# window — TradingView stamps bars with their open time.
PRESETS = {
    "aln": {
        "label": "ALN profiler (the script's own windows)",
        "asia": (20, 2), "london": (2, 8), "ny": (8, 16),
    },
    "ict": {
        "label": "ICT killzones — tighter driver windows",
        "asia": (20, 0), "london": (2, 5), "ny": (8, 11),
    },
    "wide": {
        "label": "Full overnight into the RTH day",
        "asia": (18, 2), "london": (2, 8), "ny": (8, 16),
    },
    "nyam": {
        "label": "ALN windows, NY cut to the AM session",
        "asia": (20, 2), "london": (2, 8), "ny": (9, 12),
    },
}

PATTERNS = ["Engulf", "Inside", "Partial Up", "Partial Down"]


def hours_in(window):
    start, end = window[0] % 24, window[1] % 24
    out, h = [], start
    while h != end:
        out.append(h)
        h = (h + 1) % 24
    return out


def load(path):
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["et"] = df["ts"].dt.tz_convert(ET)
    df["hour"] = df["et"].dt.hour
    df["day"] = (df["et"] + timedelta(hours=6)).dt.date   # 18:00 ET rolls into the next date
    return df.sort_values("ts").reset_index(drop=True)


def walk(driver, hi, lo):
    """Walk the driver session against a range and record what it did to it."""
    H = driver["high"].to_numpy()
    L = driver["low"].to_numpy()
    O = driver["open"].to_numpy()
    C = driver["close"].to_numpy()

    first, first_i, both_same_bar = None, None, False
    reached_other = False
    took_hi = bool((H > hi).any())
    took_lo = bool((L < lo).any())

    for i in range(len(driver)):
        up, dn = H[i] > hi, L[i] < lo
        if first is None:
            if up and dn:
                both_same_bar = True
                # unknowable at 1h; a down-closing bar is assumed to have gone up first
                first = 1 if C[i] < O[i] else -1
                first_i = i
                reached_other = True
            elif up:
                first, first_i = 1, i
            elif dn:
                first, first_i = -1, i
        elif not reached_other:
            if (first == 1 and L[i] < lo) or (first == -1 and H[i] > hi):
                reached_other = True

    if took_hi and took_lo:
        pattern = "Engulf"
    elif took_hi:
        pattern = "Partial Up"
    elif took_lo:
        pattern = "Partial Down"
    else:
        pattern = "Inside"

    return {
        "first": first, "first_i": first_i, "both_same_bar": both_same_bar,
        "reached_other": reached_other, "took_hi": took_hi, "took_lo": took_lo,
        "pattern": pattern,
    }


def reversal_trade(driver, hi, lo, w, entry_mode, stop_ticks, tick, buffer_ticks,
                   rr_cap, side=-1):
    """
    Trade the sweep. side = -1 fades it (the model), side = +1 goes with it (the control).

    NO LOOKAHEAD. Everything the trade uses must be knowable at the moment of the fill:

      entry_mode 'close'   — fill at the CLOSE of the first bar that traded beyond the level.
                             The stop is that bar's extreme (already printed) plus a buffer.
      entry_mode 'reject'  — the same, but only if that bar CLOSED BACK INSIDE the range.
                             A bar that closes beyond the level is a break, not a sweep, and
                             is skipped. This is the honest hourly version of the setup.
      entry_mode 'limit'   — fill at the level itself with a FIXED stop N ticks beyond it.
                             Optimistic on the fill, but the stop is still knowable.

    Resolution starts on the bar AFTER the fill for the close modes (the fill bar is spent).
    On any bar touching both the stop and the target, the STOP is taken.
    """
    if w["first"] is None:
        return None
    i0 = w["first_i"]
    H, L, C = driver["high"].to_numpy(), driver["low"].to_numpy(), driver["close"].to_numpy()
    swept_hi = w["first"] == 1

    if entry_mode == "limit":
        entry = hi if swept_hi else lo
        stop = entry + stop_ticks * tick if swept_hi else entry - stop_ticks * tick
        start = i0
    else:
        if entry_mode == "reject":
            back_inside = C[i0] <= hi if swept_hi else C[i0] >= lo
            if not back_inside:
                return None
        entry = C[i0]
        ext = H[i0] if swept_hi else L[i0]
        stop = ext + buffer_ticks * tick if swept_hi else ext - buffer_ticks * tick
        start = i0 + 1

    d = side * (1 if swept_hi else -1)               # side -1 fades: a high sweep -> short
    if side == 1:                                    # control: trade WITH the sweep
        stop = entry - (stop - entry)

    risk = abs(entry - stop)
    if risk <= 0:
        return None

    target = (lo if swept_hi else hi) if side == -1 else (hi if swept_hi else lo)
    if rr_cap:
        capped = entry + d * rr_cap * risk
        target = max(target, capped) if d == -1 else min(target, capped)
    reward = (target - entry) * d
    if reward <= 0:
        return None                                  # target already behind the fill

    for i in range(start, len(driver)):
        hit_stop = (H[i] >= stop) if d == -1 else (L[i] <= stop)
        hit_tgt = (L[i] <= target) if d == -1 else (H[i] >= target)
        if hit_stop:
            return {"r": -1.0, "how": "stop", "bars": i - i0, "risk": risk}
        if hit_tgt:
            return {"r": reward / risk, "how": "target", "bars": i - i0, "risk": risk}

    if start >= len(driver):
        return None                                  # swept on the session's last bar
    pts = (C[-1] - entry) * d
    return {"r": pts / risk, "how": "session end", "bars": len(driver) - 1 - i0, "risk": risk}


def analyse(df, preset, tick, entry_mode, stop_ticks, buffer_ticks, rr_cap):
    cfg = PRESETS[preset]
    hset = {k: set(hours_in(cfg[k])) for k in ("asia", "london", "ny")}
    need = {k: max(2, len(hours_in(cfg[k])) - 1) for k in ("asia", "london", "ny")}

    rows = []
    for day, g in df.groupby("day", sort=True):
        s = {k: g[g["hour"].isin(hset[k])] for k in hset}
        if any(len(s[k]) < need[k] for k in s):
            continue                                   # holiday / early close / partial day

        aH, aL = s["asia"]["high"].max(), s["asia"]["low"].min()
        lH, lL = s["london"]["high"].max(), s["london"]["low"].min()

        wl = walk(s["london"], aH, aL)
        wn = walk(s["ny"], lH, lL)
        args = (entry_mode, stop_ticks, tick, buffer_ticks, rr_cap)
        tl = reversal_trade(s["london"], aH, aL, wl, *args)
        tn = reversal_trade(s["ny"], lH, lL, wn, *args)
        cl = reversal_trade(s["london"], aH, aL, wl, *args, side=1)
        cn = reversal_trade(s["ny"], lH, lL, wn, *args, side=1)

        # "NY engulfs overnight" — NY takes out both extremes of Asia AND London combined
        oH, oL = max(aH, lH), min(aL, lL)
        ny_engulf_on = bool((s["ny"]["high"] > oH).any() and (s["ny"]["low"] < oL).any())

        for pair, w, t, c, rng in (("London/Asia", wl, tl, cl, aH - aL),
                                   ("NY/London", wn, tn, cn, lH - lL)):
            rows.append({
                "day": day, "pair": pair, "range_pts": rng,
                "pattern": w["pattern"], "first": w["first"],
                "took_hi": w["took_hi"], "took_lo": w["took_lo"],
                "both_same_bar": w["both_same_bar"], "reached_other": w["reached_other"],
                "lon_pattern": wl["pattern"], "lon_first": wl["first"],
                "ny_engulf_overnight": ny_engulf_on,
                "r": t["r"] if t else np.nan, "how": t["how"] if t else None,
                "risk": t["risk"] if t else np.nan,
                "ctrl_r": c["r"] if c else np.nan,
            })
    return pd.DataFrame(rows)


def block(d, title):
    n = len(d)
    if n == 0:
        return f"\n{title}\n  (no days)\n"
    took = d[d["first"].notna()]
    got = took[took["reached_other"]]
    tr = took.dropna(subset=["r"])
    amb = int(took["both_same_bar"].sum())
    clean = took[~took["both_same_bar"]]
    clean_got = clean[clean["reached_other"]]

    out = [f"\n{title}   n = {n}"]
    out.append("  pattern mix:      " + "   ".join(
        f"{p} {100 * (d['pattern'] == p).mean():.0f}%" for p in PATTERNS))
    out.append(f"  took a side:      {100 * len(took) / n:.1f}%"
               f"   (high first {100 * (took['first'] == 1).mean():.0f}% / "
               f"low first {100 * (took['first'] == -1).mean():.0f}%)")
    out.append(f"  -> reached the OTHER side: {len(got)}/{len(took)} = "
               f"{100 * len(got) / max(len(took), 1):.1f}%")
    out.append(f"     excluding {amb} same-bar-both days: "
               f"{len(clean_got)}/{len(clean)} = {100 * len(clean_got) / max(len(clean), 1):.1f}%")
    if len(tr):
        out.append(f"  fade trade:       {len(tr)} trades   win {100 * (tr['r'] > 0).mean():.1f}%"
                   f"   exp {tr['r'].mean():+.3f} R   total {tr['r'].sum():+.1f} R")
        out.append(f"     target {100 * (tr['how'] == 'target').mean():.0f}%"
                   f" / stop {100 * (tr['how'] == 'stop').mean():.0f}%"
                   f" / session end {100 * (tr['how'] == 'session end').mean():.0f}%"
                   f"   median stop {tr['risk'].median():.0f} pts"
                   f"   median range {took['range_pts'].median():.0f} pts")
        ct = took.dropna(subset=["ctrl_r"])
        if len(ct):
            out.append(f"  CONTROL (go WITH the sweep instead): {len(ct)} trades"
                       f"   win {100 * (ct['ctrl_r'] > 0).mean():.1f}%"
                       f"   exp {ct['ctrl_r'].mean():+.3f} R")
    return "\n".join(out) + "\n"


def conditional_table(res):
    """NY behaviour conditioned on what London did to Asia — the ALN profiler's premise."""
    ny = res[res["pair"] == "NY/London"]
    lines = ["\n" + "-" * 78,
             "NY conditioned on the London/Asia pattern",
             "-" * 78,
             f"{'London did':<16}{'n':>5}{'NY took a side':>16}{'then other side':>18}"
             f"{'engulfed o/n':>15}{'fade exp R':>12}"]
    for p in PATTERNS:
        d = ny[ny["lon_pattern"] == p]
        if len(d) == 0:
            continue
        took = d[d["first"].notna()]
        got = took[took["reached_other"]]
        tr = took.dropna(subset=["r"])
        lines.append(
            f"{p:<16}{len(d):>5}{100 * len(took) / len(d):>15.0f}%"
            f"{100 * len(got) / max(len(took), 1):>17.0f}%"
            f"{100 * d['ny_engulf_overnight'].mean():>14.0f}%"
            f"{tr['r'].mean() if len(tr) else np.nan:>12.3f}")

    lines.append("")
    lines.append("Split further by which side London took first:")
    lines.append(f"{'London did':<16}{'first':<8}{'n':>5}{'NY other side':>16}{'fade exp R':>12}")
    for p in PATTERNS:
        for f, fname in ((1.0, "high"), (-1.0, "low")):
            d = ny[(ny["lon_pattern"] == p) & (ny["lon_first"] == f)]
            took = d[d["first"].notna()]
            if len(took) < 15:
                continue
            got = took[took["reached_other"]]
            tr = took.dropna(subset=["r"])
            lines.append(f"{p:<16}{fname:<8}{len(took):>5}"
                         f"{100 * len(got) / len(took):>15.0f}%"
                         f"{tr['r'].mean() if len(tr) else np.nan:>12.3f}")
    return "\n".join(lines) + "\n"


def yearly(res, pair):
    d = res[(res["pair"] == pair) & res["r"].notna()].copy()
    d["yr"] = pd.to_datetime(d["day"]).dt.year
    lines = [f"\n{pair} by year:"]
    for yr, g in d.groupby("yr"):
        lines.append(f"  {yr}   {len(g):>4} trades   win {100 * (g['r'] > 0).mean():>5.1f}%"
                     f"   exp {g['r'].mean():+.3f} R   total {g['r'].sum():+7.1f} R")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--preset", default="aln", help="aln | ict | wide | nyam | all")
    ap.add_argument("--entry", default="reject", choices=["close", "reject", "limit"])
    ap.add_argument("--stop-ticks", type=float, default=40)
    ap.add_argument("--buffer-ticks", type=float, default=0)
    ap.add_argument("--rr-cap", type=float, default=0, help="cap the target at N x risk (0 = off)")
    ap.add_argument("--tick", type=float, default=0.25)
    ap.add_argument("--json")
    args = ap.parse_args()

    df = load(args.csv)
    print(f"{len(df)} bars   {df['et'].min():%Y-%m-%d} -> {df['et'].max():%Y-%m-%d}   "
          f"{df['day'].nunique()} calendar trading days")
    print(f"entry = {args.entry}"
          + (f", fixed {args.stop_ticks:g}-tick stop" if args.entry == "limit"
             else f", stop at the sweep bar's extreme + {args.buffer_ticks:g} ticks")
          + (f"   target capped at {args.rr_cap:g}R" if args.rr_cap else "   target = opposite side"))

    presets = list(PRESETS) if args.preset == "all" else [args.preset]
    dump = {}
    for p in presets:
        cfg = PRESETS[p]
        res = analyse(df, p, args.tick, args.entry, args.stop_ticks,
                      args.buffer_ticks, args.rr_cap)
        print("\n" + "=" * 78)
        print(f"{p.upper()}  —  {cfg['label']}")
        print(f"  asia {cfg['asia'][0]:02d}-{cfg['asia'][1] % 24:02d}   "
              f"london {cfg['london'][0]:02d}-{cfg['london'][1] % 24:02d}   "
              f"ny {cfg['ny'][0]:02d}-{cfg['ny'][1] % 24:02d}  ET")
        print("=" * 78)
        print(block(res[res["pair"] == "London/Asia"], "LONDON vs the Asia range"))
        print(block(res[res["pair"] == "NY/London"], "NEW YORK vs the London range"))
        print(conditional_table(res))
        print(yearly(res, "London/Asia"))
        print(yearly(res, "NY/London"))
        if args.json:
            dump[p] = res.to_dict("records")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(dump, f, indent=2, default=str)


if __name__ == "__main__":
    main()

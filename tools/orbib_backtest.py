#!/usr/bin/env python3
"""Backtester for the break-then-pullback (ORB + IB) Pine strategy.

Reads a CSV of intraday bars - time,open,high,low,close, where time is a unix timestamp -
which is what both a TradingView chart export and tools/alpaca_fetch.py produce.

Levels use the same convention as the Pine script: DEPTH is measured from the level that
broke (0%) towards the far side of the range (100%), so longs and shorts read identically.

    # what the script's current defaults do on this data
    python3 tools/orbib_backtest.py data/mes_5m.csv --mode baseline --point-value 5

    # sweep entry/stop/target/break/cutoff/filter, ranked by the weaker walk-forward half
    python3 tools/orbib_backtest.py data/mes_5m.csv --mode sweep --range orb --point-value 5

    # one configuration in detail: monthly P&L, IS/OOS split, bootstrap, direction/weekday split
    python3 tools/orbib_backtest.py data/qqq_5m.csv --mode detail --range orb --point-value 1 \
        --commission 0 --tick 0.01 --entry-shallow 25 --stop-shallow 75 --break close \
        --tp-long "Ext 75%" --tp-short "Ext 100%" --cutoff 11:00 --min-range 0.20

Order handling matches the script and TradingView's emulator: an order placed on one bar is
live from the next, a limit fills at its price (or better on a gap), the stop/target attached
at the fill can only trigger from the following bar, the stop wins a bar that touches both,
and anything still open is flattened at the session's flatten time.
"""
import argparse, csv, datetime, itertools, random, sys, zoneinfo
from collections import defaultdict

TZ = zoneinfo.ZoneInfo("America/New_York")
TPS = [("Range level",0.0), ("Ext 10%",0.10), ("Ext 20%",0.20), ("Ext 30%",0.30),
       ("Ext 50%",0.50), ("Ext 75%",0.75), ("Ext 100%",1.00), ("HOD/LOD",None)]
TPNAMES = [n for n, _ in TPS]
NTP = len(TPS)

class Cost:
    def __init__(self, ptval, tick, comm):
        self.ptval, self.tick, self.comm = ptval, tick, comm

def hhmm(s):
    h, m = s.split(":"); return int(h)*60 + int(m)

def load(path, sess_start=hhmm("09:30"), sess_end=hhmm("16:00")):
    days = {}
    for r in csv.DictReader(open(path)):
        dt = datetime.datetime.fromtimestamp(int(float(r["time"])), TZ)
        tod = dt.hour*60 + dt.minute
        if not (sess_start <= tod < sess_end): continue
        days.setdefault(dt.date(), []).append(
            (tod, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    for d in days: days[d].sort()
    return {d: v for d, v in days.items() if len(v) >= 60}

def lvl(d, rH, rL, rng, depth):
    """depth 0 = the level that broke, 1 = the far side of the range"""
    return rH - depth*rng if d == 1 else rL + depth*rng

def run_day(bars, range_end, cfg, flat, cost, maxdep=0.5):
    """cfg = (entry_shallow, stop_shallow, entry_deep, stop_deep, avg_depth, thr, break_type),
    depths as fractions. Returns one trade record with the outcome under every target."""
    eS, sS, eD, sD, aDep, thr, brk = cfg
    n = len(bars)
    ri = [i for i, b in enumerate(bars) if b[0] < range_end]
    if not ri: return None
    rH = max(bars[i][2] for i in ri); rL = min(bars[i][3] for i in ri)
    if not rH > rL: return None
    hi = min(i for i in ri if bars[i][2] == rH); lo = min(i for i in ri if bars[i][3] == rL)
    rng = rH - rL; last = max(ri)
    pos = (bars[last][4] - rL) / rng
    # maxdep = how far the range may CLOSE into itself, measured from the level that will break
    if lo < hi and pos >= 1 - maxdep:   d, shallow = 1, pos >= thr
    elif lo > hi and pos <= maxdep:     d, shallow = -1, pos <= 1 - thr
    else: return None
    eDep, sDep = (eS, sS) if shallow else (eD, sD)
    if sDep <= eDep: return None                       # stop must sit deeper than the entry
    e1   = lvl(d, rH, rL, rng, eDep)
    stop = lvl(d, rH, rL, rng, sDep)
    e2   = lvl(d, rH, rL, rng, aDep) if eDep < aDep < sDep else None

    broke = False; placed = None; opp = None
    f1 = f2 = px1 = px2 = None
    for i in range(last+1, n):
        tod, o, h, l, c = bars[i]
        if not broke:
            broke = (h >= rH if d == 1 else l <= rL) if brk == "wick" else (c > rH if d == 1 else c < rL)
            if broke: placed = i                      # the order is live from the next bar
            continue
        if opp is None and ((d == 1 and l < rL) or (d == -1 and h > rH)): opp = i
        if f1 is None and i > placed and tod < flat:
            if (d == 1 and l <= e1) or (d == -1 and h >= e1):
                f1 = i; px1 = min(e1, o) if d == 1 else max(e1, o)
        if e2 is not None and f1 is not None and f2 is None and i > placed:
            if (d == 1 and l <= e2) or (d == -1 and h >= e2):
                f2 = i; px2 = min(e2, o) if d == 1 else max(e2, o)
        if f1 is not None and tod >= flat: break
    if f1 is None: return None

    dHiF = max(b[2] for b in bars[:f1+1]); dLoF = min(b[3] for b in bars[:f1+1])
    tps = [(dHiF if d == 1 else dLoF) if ext is None else
           (rH + ext*rng if d == 1 else rL - ext*rng) for _, ext in TPS]
    sBar = None; tBar = [None]*NTP; flatBar = None
    for i in range(f1+1, n):
        tod, o, h, l, c = bars[i]
        if tod >= flat: flatBar = i; break
        if sBar is None and ((d == 1 and l <= stop) or (d == -1 and h >= stop)): sBar = i
        for k, tp in enumerate(tps):
            if tBar[k] is None and ((d == 1 and h >= tp) or (d == -1 and l <= tp)): tBar[k] = i
        if sBar is not None and all(t is not None for t in tBar): break
    # strategy.close_all() is issued on the flatten bar, so the market order fills at the OPEN of the
    # bar after it - matching TradingView with process_orders_on_close = false.
    eodPx = (bars[flatBar+1][1] if (flatBar is not None and flatBar+1 < n)
             else (bars[flatBar][4] if flatBar is not None else bars[-1][4]))

    rec = dict(dir=d, shallow=shallow, rngPct=rng/rL*100, rng=rng, risk1=abs(px1-stop)*cost.ptval,
               fillTod=bars[f1][0], px1=px1, px2=px2, stop=stop,
               oppPending=(opp is not None and opp < f1),
               pnl1=[0.0]*NTP, pnl2=[0.0]*NTP, has2=[False]*NTP, exitK=[None]*NTP)
    for k in range(NTP):
        sb, tb = sBar, tBar[k]
        if sb is not None and (tb is None or sb <= tb):
            xpx, kind, xb = stop - cost.tick*d, "stop", sb          # stop = market, pays a tick
        elif tb is not None:
            xpx, kind, xb = tps[k], "target", tb
        else:
            xpx, kind, xb = eodPx - cost.tick*d, "eod", (flatBar if flatBar is not None else n-1)
        rec["exitK"][k] = kind
        rec["pnl1"][k] = (xpx - px1)*d*cost.ptval - 2*cost.comm
        if f2 is not None and f2 <= xb:
            rec["has2"][k] = True
            rec["pnl2"][k] = (xpx - px2)*d*cost.ptval - 2*cost.comm
    return rec

def build(days, range_end, cfg, flat, cost, maxdep=0.5):
    out = []
    for d in sorted(days):
        t = run_day(days[d], range_end, cfg, flat, cost, maxdep)
        if t: t["date"] = d; t["dow"] = d.weekday(); out.append(t)
    return out

def score(rec, tpL, tpS, avg, cutoff, minRng, noDbl=True, days=None):
    v = []
    for t in rec:
        if noDbl and t["oppPending"]: continue
        if t["fillTod"] > cutoff or t["rngPct"] < minRng: continue
        if days is not None and t["date"] not in days: continue
        k = tpL if t["dir"] == 1 else tpS
        v.append((t["date"], t["pnl1"][k] + (t["pnl2"][k] if (avg and t["has2"][k]) else 0.0)))
    if not v: return None
    p = [x[1] for x in v]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in p:
        eq += x; peak = max(peak, eq); dd = max(dd, peak - eq)
    gw = sum(x for x in p if x > 0); gl = -sum(x for x in p if x < 0)
    return dict(n=len(p), net=sum(p), pf=(gw/gl if gl > 0 else float("inf")),
                wr=100.0*sum(1 for x in p if x > 0)/len(p), dd=dd, avg=sum(p)/len(p), trades=v)

def fmt(s, name=""):
    if s is None: return f"{name:<40} -"
    return (f"{name:<40} n={s['n']:>4}  net={s['net']:>9.0f}  PF={s['pf']:>5.2f}  "
            f"win={s['wr']:>5.1f}%  maxDD={s['dd']:>8.0f}  avg={s['avg']:>7.1f}")

# ------------------------------------------------------------------ modes
DEPTHS_E = [0.0, 0.25, 0.50, 0.75]
DEPTHS_S = [0.25, 0.50, 0.75, 1.00]
THRS     = [0.70, 0.75, 0.80]
BRKS     = ["wick", "close"]
CUTS     = [11*60, 12*60, 13*60, 14*60, 15*60]
MINR     = [0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]

def mode_sweep(days, range_end, flat, cost, minTrades, split):
    res = []
    for eS, sS, eD, sD, thr, brk in itertools.product(DEPTHS_E, DEPTHS_S, [0.50], [0.75], THRS, BRKS):
        if sS <= eS: continue
        rec = build(days, range_end, (eS, sS, eD, sD, 0.50, thr, brk), flat, cost)
        if len(rec) < minTrades: continue
        IS = {d for d in days if d < split}; OS = {d for d in days if d >= split}
        for avg in (False, True):
            for cut in CUTS:
                for mr in MINR:
                    for i in range(NTP):
                        for j in range(NTP):
                            s = score(rec, i, j, avg, cut, mr)
                            if not s or s["n"] < minTrades or s["pf"] < 1.25: continue
                            si = score(rec, i, j, avg, cut, mr, days=IS)
                            so = score(rec, i, j, avg, cut, mr, days=OS)
                            if not si or not so or si["net"] <= 0 or so["net"] <= 0: continue
                            res.append((min(si["avg"], so["avg"]), eS, sS, thr, brk, avg, cut, mr,
                                        i, j, s, si, so))
    res.sort(key=lambda r: -r[0])
    print(f"{'entry':>6}{'stop':>6}{'thr':>6}{'brk':>7}{'avg':>5}{'cut':>7}{'minR':>6}"
          f"{'tpLong':>13}{'tpShort':>13}{'n':>5}{'net':>9}{'PF':>6}{'win%':>7}{'IS':>8}{'OOS':>8}")
    for r in res[:20]:
        _, eS, sS, thr, brk, avg, cut, mr, i, j, s, si, so = r
        print(f"{eS*100:>5.0f}%{sS*100:>5.0f}%{thr*100:>5.0f}%{brk:>7}{'Y' if avg else 'N':>5}"
              f"{cut//60:>5}:{cut%60:02d}{mr:>6.2f}{TPNAMES[i]:>13}{TPNAMES[j]:>13}"
              f"{s['n']:>5}{s['net']:>9.0f}{s['pf']:>6.2f}{s['wr']:>7.1f}{si['net']:>8.0f}{so['net']:>8.0f}")
    print(f"\n{len(res)} configurations profitable in both walk-forward halves with "
          f"n>={minTrades} and PF>=1.25")

def mode_detail(days, range_end, flat, cost, cfg, tpL, tpS, avg, cut, mr, split, label, maxdep=0.5):
    rec = build(days, range_end, cfg, flat, cost, maxdep)
    s  = score(rec, tpL, tpS, avg, cut, mr)
    if s is None: print("no trades"); return
    IS = {d for d in days if d < split}; OS = {d for d in days if d >= split}
    print("="*104); print(label)
    print(fmt(s, "  full period"))
    print(fmt(score(rec, tpL, tpS, avg, cut, mr, days=IS), "  in-sample (first 60%)"))
    print(fmt(score(rec, tpL, tpS, avg, cut, mr, days=OS), "  out-of-sample (last 40%)"))
    mon = defaultdict(float)
    for d, v in s["trades"]: mon[f"{d.year%100}-{d.month:02d}"] += v
    print("  monthly: " + " ".join(f"{k}:{v:+.0f}" for k, v in sorted(mon.items())))
    best = max(mon.values())
    print(f"  months +: {sum(1 for v in mon.values() if v>0)}/{len(mon)}   best month {best:+.0f} "
          f"({100*best/s['net']:.0f}% of net)   net without it {s['net']-best:+.0f}")
    p = [v for _, v in s["trades"]]; rnd = random.Random(7)
    wins = sum(1 for _ in range(4000) if sum(rnd.choice(p) for _ in p) > 0)
    print(f"  bootstrap P(net>0) = {100*wins/4000:.1f}%")
    grp = defaultdict(list)
    for t in rec:
        if t["oppPending"] or t["fillTod"] > cut or t["rngPct"] < mr: continue
        k = tpL if t["dir"] == 1 else tpS
        v = t["pnl1"][k] + (t["pnl2"][k] if (avg and t["has2"][k]) else 0)
        grp["LONG" if t["dir"] == 1 else "SHORT"].append(v)
        grp[["Mon","Tue","Wed","Thu","Fri"][t["dow"]]].append(v)
        grp["shallow" if t["shallow"] else "deeper"].append(v)
        grp["_risk"].append(t["risk1"])
    print("  " + "  ".join(f"{k}:{sum(x):+.0f}({len(x)})" for k, x in grp.items() if k != "_risk"))
    if grp["_risk"]:
        rk = sorted(grp["_risk"])
        print(f"  risk on unit 1: mean {sum(rk)/len(rk):.0f}, median {rk[len(rk)//2]:.0f} per contract/share")


# ------------------------------------------------------------------ joint ORB + IB
# The two engines share one position, so the script's priority rules decide who gets the seat:
# the ORB may enter only before the IB forms (unless carried), an open ORB holds the IB back, and
# an IB whose entry traded through during that hold is forfeited for the day.
ES_ORB = dict(cfg=(0.25, 1.00, 0.50, 1.00, 0.50, 0.75, "close"), tp=(0.75, 1.00), cut=11*60, minr=0.20)
ES_IB  = dict(cfg=(0.25, 1.00, 0.50, 1.00, 0.50, 0.75, "wick"),  tp=(0.30, 0.30), cut=12*60, minr=0.30)

def _levels(bars, range_end, cfg, maxdep=0.5):
    eS, sS, eD, sD, aDep, thr, brk = cfg
    ri = [i for i, b in enumerate(bars) if b[0] < range_end]
    if not ri: return None
    rH = max(bars[i][2] for i in ri); rL = min(bars[i][3] for i in ri)
    if not rH > rL: return None
    hi = min(i for i in ri if bars[i][2] == rH); lo = min(i for i in ri if bars[i][3] == rL)
    rng = rH - rL; last = max(ri); pos = (bars[last][4] - rL) / rng
    if lo < hi and pos >= 1 - maxdep:   d, sh = 1, pos >= thr
    elif lo > hi and pos <= maxdep:     d, sh = -1, pos <= 1 - thr
    else: return None
    eDep, sDep = (eS, sS) if sh else (eD, sD)
    if sDep <= eDep: return None
    return dict(d=d, rH=rH, rL=rL, rng=rng, start=last+1, brk=brk, rngPct=rng/rL*100,
                e1=lvl(d, rH, rL, rng, eDep), stop=lvl(d, rH, rL, rng, sDep),
                e2=lvl(d, rH, rL, rng, aDep) if eDep < aDep < sDep else None)

def joint_day(bars, ibOn, carry, cost, flat, avg=True):
    O = _levels(bars, hhmm("09:45"), ES_ORB["cfg"]); I = _levels(bars, hhmm("10:30"), ES_IB["cfg"]) if ibOn else None
    if O and O["rngPct"] < ES_ORB["minr"]: O = None
    if I and I["rngPct"] < ES_IB["minr"]:  I = None
    st = {t: dict(L=L, cut=c, tp=tp, broke=False, placed=False, f1=None, f2=None, px1=None, px2=None,
                  stopP=None, tpP=None, closed=False, missed=False, ein=None)
          for t, L, c, tp in (("ORB", O, ES_ORB["cut"], ES_ORB["tp"]), ("IB", I, ES_IB["cut"], ES_IB["tp"]))}
    out = []; dHi = -1e18; dLo = 1e18; ibFormed = False
    for i, (tod, o, h, l, c) in enumerate(bars):
        dHi = max(dHi, h); dLo = min(dLo, l)
        if tod >= hhmm("10:30"): ibFormed = True
        orbLive = st["ORB"]["f1"] is not None and not st["ORB"]["closed"]
        for tag in ("ORB", "IB"):
            S = st[tag]; L = S["L"]
            if L is None or S["closed"] or i < L["start"]: continue
            d = L["d"]
            if S["f1"] is not None and i > S["f1"]:
                hitS = (l <= S["stopP"]) if d == 1 else (h >= S["stopP"])
                hitT = (h >= S["tpP"])   if d == 1 else (l <= S["tpP"])
                px = kind = None
                if hitS:   px, kind = S["stopP"] - cost.tick*d, "stop"
                elif hitT: px, kind = S["tpP"], "target"
                elif tod >= flat:
                    px = (bars[i+1][1] if i+1 < len(bars) else c) - cost.tick*d; kind = "eod"
                if px is not None:
                    pnl = (px - S["px1"])*d*cost.ptval - 2*cost.comm
                    units = 1
                    if S["f2"] is not None and S["f2"] <= i:
                        pnl += (px - S["px2"])*d*cost.ptval - 2*cost.comm; units = 2
                    out.append(dict(tag=tag, pnl=pnl, ein=S["ein"], xout=tod, kind=kind, units=units))
                    S["closed"] = True
                    continue
            if tod >= flat: continue
            if not S["broke"]:
                S["broke"] = (h >= L["rH"] if d == 1 else l <= L["rL"]) if L["brk"] == "wick" else (c > L["rH"] if d == 1 else c < L["rL"])
                continue
            if tag == "IB" and orbLive and S["f1"] is None:                 # held behind an open ORB
                if (d == 1 and l <= L["e1"]) or (d == -1 and h >= L["e1"]): S["missed"] = True
                continue
            if tag == "IB" and S["missed"]: continue
            if tag == "ORB" and S["f1"] is None:
                if ibOn and ibFormed and not carry: continue                # cut when the IB forms
                if ibOn and carry and I is not None and st["IB"]["broke"]: continue   # carry yields to an armed IB
            if tod > S["cut"] and S["f1"] is None: continue
            if not S["placed"]: S["placed"] = True; continue                # live from the next bar
            if S["f1"] is None and ((d == 1 and l <= L["e1"]) or (d == -1 and h >= L["e1"])):
                S["f1"] = i; S["px1"] = min(L["e1"], o) if d == 1 else max(L["e1"], o); S["ein"] = tod
                S["stopP"] = L["stop"]
                ext = S["tp"][0] if d == 1 else S["tp"][1]
                S["tpP"] = (L["rH"] + ext*L["rng"]) if d == 1 else (L["rL"] - ext*L["rng"])
            if avg and L["e2"] is not None and S["f1"] is not None and S["f2"] is None and i >= S["f1"]:
                if (d == 1 and l <= L["e2"]) or (d == -1 and h >= L["e2"]):
                    S["f2"] = i; S["px2"] = min(L["e2"], o) if d == 1 else max(L["e2"], o)
    return out

def mode_joint(days, cost, flat, ibOn, carry, split):
    T = []
    for d in sorted(days):
        for t in joint_day(days[d], ibOn, carry, cost, flat): t["date"] = d; T.append(t)
    def stat(rows, name):
        v = [t["pnl"] for t in rows]
        if not v: print(f"  {name:<24} n=0"); return
        eq = peak = dd = 0.0
        for x in v:
            eq += x; peak = max(peak, eq); dd = max(dd, peak-eq)
        gw = sum(x for x in v if x > 0); gl = -sum(x for x in v if x < 0)
        print(f"  {name:<24} n={len(v):>4}  net={sum(v):>9.0f}  PF={(gw/gl if gl else float('inf')):>5.2f}  "
              f"win={100*sum(1 for x in v if x>0)/len(v):>5.1f}%  maxDD={dd:>8.0f}")
    print("="*104)
    print(f"JOINT  ORB{' + IB' if ibOn else ' only'}  (ES/MES preset rules, carry {'on' if carry else 'off'})")
    stat(T, "full period"); stat([t for t in T if t["date"] < split], "in-sample (first 60%)")
    stat([t for t in T if t["date"] >= split], "out-of-sample (last 40%)")
    for tag in ("ORB", "IB"): stat([t for t in T if t["tag"] == tag], f"  {tag} leg")
    mon = defaultdict(float)
    for t in T: mon[f"{t['date'].year%100}-{t['date'].month:02d}"] += t["pnl"]
    print("  monthly: " + " ".join(f"{k}:{v:+.0f}" for k, v in sorted(mon.items())))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--mode", default="baseline", choices=["baseline", "sweep", "detail", "joint"])
    ap.add_argument("--ib", default="on", choices=["on","off"], help="joint mode: run the IB leg")
    ap.add_argument("--carry", default="on", choices=["on","off"], help="joint mode: carry an unfilled ORB past the IB")
    ap.add_argument("--range", default="orb", choices=["orb", "ib"])
    ap.add_argument("--point-value", type=float, default=5.0, help="$ per 1.00 move per contract/share (MES 5, MNQ 2, MGC 10, an ETF 1)")
    ap.add_argument("--tick", type=float, default=0.25, help="one tick, charged as slippage on stop and market exits")
    ap.add_argument("--commission", type=float, default=0.5, help="$ per contract per side")
    ap.add_argument("--orb-end", default="09:45"); ap.add_argument("--ib-end", default="10:30")
    ap.add_argument("--flatten", default="15:30")
    ap.add_argument("--entry-shallow", type=float, default=25); ap.add_argument("--stop-shallow", type=float, default=100)
    ap.add_argument("--entry-deep", type=float, default=50);    ap.add_argument("--stop-deep", type=float, default=75)
    ap.add_argument("--avg-level", type=float, default=50);     ap.add_argument("--avg", default="on", choices=["on","off"])
    ap.add_argument("--threshold", type=float, default=75, help="shallow when the range closes beyond this %% of the range")
    ap.add_argument("--break", dest="brk", default=None, choices=["wick","close"])
    ap.add_argument("--tp-long", default=None); ap.add_argument("--tp-short", default=None)
    ap.add_argument("--cutoff", default=None); ap.add_argument("--min-range", type=float, default=None)
    ap.add_argument("--min-trades", type=int, default=40)
    ap.add_argument("--max-close-depth", type=float, default=50,
                    help="how far the range may close into itself, %% measured from the breaking level (50 = classic half rule)")
    a = ap.parse_args()

    cost = Cost(a.point_value, a.tick, a.commission)
    days = load(a.csv)
    if not days: sys.exit("no sessions parsed - is the CSV time column a unix timestamp?")
    dts = sorted(days); split = dts[int(len(dts)*0.6)]
    print(f"{a.csv}: {len(days)} sessions, {dts[0]} -> {dts[-1]}   "
          f"(point value {a.point_value}, tick {a.tick}, commission {a.commission}/side)\n")
    rend = hhmm(a.orb_end if a.range == "orb" else a.ib_end)
    flat = hhmm(a.flatten)

    if a.mode == "baseline":
        for nm, rE, brk, tl, ts, cut, mr in (
                ("ORB (script defaults)", hhmm(a.orb_end), "close", "Ext 10%", "Ext 30%", 15*60, 0.40),
                ("IB  (script defaults)", hhmm(a.ib_end),  "wick",  "Ext 30%", "Ext 30%", 14*60, 0.0)):
            rec = build(days, rE, (0.25, 1.00, 0.50, 0.75, 0.50, 0.75, brk), flat, cost, a.max_close_depth/100)
            print(fmt(score(rec, TPNAMES.index(tl), TPNAMES.index(ts), True, cut, mr), nm))
        return
    if a.mode == "joint":
        mode_joint(days, cost, flat, a.ib == "on", a.carry == "on", split); return
    if a.mode == "sweep":
        mode_sweep(days, rend, flat, cost, a.min_trades, split); return

    brk = a.brk or ("close" if a.range == "orb" else "wick")
    tl  = a.tp_long  or ("Ext 10%" if a.range == "orb" else "Ext 30%")
    ts  = a.tp_short or ("Ext 30%" if a.range == "orb" else "Ext 30%")
    cut = hhmm(a.cutoff) if a.cutoff else (15*60 if a.range == "orb" else 14*60)
    mr  = a.min_range if a.min_range is not None else 0.0
    cfg = (a.entry_shallow/100, a.stop_shallow/100, a.entry_deep/100, a.stop_deep/100,
           a.avg_level/100, a.threshold/100, brk)
    mode_detail(days, rend, flat, cost, cfg, TPNAMES.index(tl), TPNAMES.index(ts),
                a.avg == "on", cut, mr, split,
                f"{a.range.upper()}  entry {a.entry_shallow:.0f}%/{a.entry_deep:.0f}%  "
                f"stop {a.stop_shallow:.0f}%/{a.stop_deep:.0f}%  {brk}-break  TP {tl}/{ts}  "
                f"cutoff {cut//60:02d}:{cut%60:02d}  minRange {mr}%  avg {a.avg}  depth {a.max_close_depth:.0f}%",
                maxdep=a.max_close_depth/100)

if __name__ == "__main__":
    main()

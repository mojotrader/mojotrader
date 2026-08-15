"""Faithful Python replica of pinescript/aln_strategy.pine, for parameter sweeps.

Mirrors the Pine logic bar by bar:
  - ALN day anchored at 20:00 ET, flatten 16:00 ET; all times as minutes since the anchor
  - range built inside its session window; first bar at each extreme recorded (strict > / <)
  - arm on the first side breached (wick touch or close beyond); optional sequence veto
  - limits rest at the 25% and/or 50% retracement; a limit is fillable only from the bar
    AFTER it was placed; fills at the limit, or at the open when the open is already past it
  - stop at a selectable retracement depth (raised to 75% when the entry uses the 50% line)
  - exit bracket goes live the bar after the fill (Pine issues it at that bar's close); a unit
    that fills while a bracket already exists can be stopped on its own fill bar
  - stop assumed first when one bar covers both stop and target (conservative)
  - EOD flatten at the open of the first bar of the flatten window
Costs: $0.50/contract/side commission, 1 tick (0.25pt) adverse slippage on stop and market
exits, none on limit fills. MNQ = $2/point/contract.
"""
import csv, datetime, zoneinfo
from collections import namedtuple

NY = zoneinfo.ZoneInfo("America/New_York")
PT_VAL, TICK, COMM = 2.0, 0.25, 0.50
SLIP = TICK

Bar = namedtuple("Bar", "t o h l c tod minsIn")
DAY_ANCHOR = 20 * 60
FLAT_MINS = (16 * 60 - DAY_ANCHOR + 1440) % 1440
SESSIONS = {"Asia": (20 * 60, 2 * 60), "London": (2 * 60, 8 * 60), "NY": (8 * 60, 16 * 60)}

DEFAULT = dict(session="Asia", brk="wick", mode="split", stop=1.00, tp="e30", seq=False,
               noDbl=True, be=False, qty_all=2.0, qty25=1.0, qty50=1.0, cut_h=15, cut_m=0,
               min_rng=0.0, ho=True, allow_both=False)


def load(path):
    bars = []
    for r in csv.DictReader(open(path)):
        d = datetime.datetime.fromtimestamp(int(r["time"]), NY)
        tod = d.hour * 60 + d.minute
        bars.append(Bar(d, float(r["open"]), float(r["high"]), float(r["low"]),
                        float(r["close"]), tod, (tod - DAY_ANCHOR + 1440) % 1440))
    days, cur = [], []
    for b in bars:
        if cur and b.minsIn < cur[-1].minsIn:
            days.append(cur); cur = []
        cur.append(b)
    if cur:
        days.append(cur)
    return [d for d in days if len(d) > 50]


def in_sess(tod, name):
    s, e = SESSIONS[name]
    return (s <= tod or tod < e) if s > e else (s <= tod < e)


def lvl(direction, rL, rng, depth):
    return rL + ((1.0 - depth) if direction == 1 else depth) * rng


def tp_price(direction, rH, rL, rng, dHi, dLo, tp):
    if tp == "HOD/LOD":
        return dHi if direction == 1 else dLo
    ext = {"edge": 0.0, "e10": 0.10, "e30": 0.30, "e50": 0.50, "e100": 1.00}[tp]
    return rH + ext * rng if direction == 1 else rL - ext * rng


def run_day(bars, cfg, other=None):
    """One ALN day, one slot. `other` is the other slot's result for priority, or None.

    Returns a dict describing the day: whether it armed, filled, and the trade P&L.
    """
    c = {**DEFAULT, **cfg}
    entry_depth = 0.25 if c["mode"] == "all25" else 0.50
    stop_depth = max(c["stop"], 0.75 if entry_depth == 0.50 else 0.50)
    cut = ((c["cut_h"] * 60 + c["cut_m"]) - DAY_ANCHOR + 1440) % 1440

    rH = rL = rng = None; rHbar = rLbar = None; rdy = False; prev_in = False
    dHi = dLo = None
    d = 0; e1 = e2 = q1 = q2 = zstop = None
    broke = False; armed_bar = None; placed = None
    pend = []; units = []; closed = False; dead = False; opp_broke = False; missed = False
    cur_stop = cur_tp = None; be_done = False; first_fill = None
    veto = None

    for i, b in enumerate(bars):
        dHi = b.h if dHi is None else max(dHi, b.h)
        dLo = b.l if dLo is None else min(dLo, b.l)
        flat = b.minsIn >= FLAT_MINS
        cur_in = in_sess(b.tod, c["session"])

        if cur_in:
            if rH is None or b.h > rH:
                rH, rHbar = b.h, i
            if rL is None or b.l < rL:
                rL, rLbar = b.l, i
        elif prev_in and not rdy and rH is not None and rH > rL:
            rng, rdy = rH - rL, True
        prev_in = cur_in

        if flat:
            for u in units:
                if u["exit"] is None:
                    u["exit"] = (b.o - SLIP) if d == 1 else (b.o + SLIP)
                    u["why"] = "eod"; u["eb"] = i
            break

        # ---- exits for units filled on an earlier bar
        if cur_stop is not None:
            for u in units:
                if u["exit"] is not None or i <= u["fb"]:
                    continue
                hs = (b.l <= cur_stop) if d == 1 else (b.h >= cur_stop)
                ht = (b.h >= cur_tp) if d == 1 else (b.l <= cur_tp)
                if hs:
                    px = b.o if ((d == 1 and b.o <= cur_stop) or (d == -1 and b.o >= cur_stop)) else cur_stop
                    u["exit"], u["why"], u["eb"] = (px - SLIP) if d == 1 else (px + SLIP), "stop", i
                elif ht:
                    px = b.o if ((d == 1 and b.o >= cur_tp) or (d == -1 and b.o <= cur_tp)) else cur_tp
                    u["exit"], u["why"], u["eb"] = px, "tp", i
            if units and all(u["exit"] is not None for u in units):
                closed, pend = True, []

        # ---- arm
        if rdy and not broke:
            up = (b.h >= rH) if c["brk"] == "wick" else (b.c > rH)
            dn = (b.l <= rL) if c["brk"] == "wick" else (b.c < rL)
            if up or dn:
                cand = (1 if b.c >= rL + 0.5 * rng else -1) if (up and dn) else (1 if up else -1)
                broke = True
                seq_ok = (not c["seq"]) or rHbar == rLbar or (rLbar < rHbar if cand == 1 else rHbar < rLbar)
                size_ok = c["min_rng"] <= 0 or (rL > 0 and rng / rL * 100 >= c["min_rng"])
                if not seq_ok:
                    veto = "seq"
                elif not size_ok:
                    veto = "size"
                else:
                    d, armed_bar = cand, i
                    p25, p50 = lvl(cand, rL, rng, 0.25), rL + 0.5 * rng
                    e1 = p50 if c["mode"] == "all50" else p25
                    e2 = p50 if c["mode"] == "split" else None
                    q1 = c["qty25"] if c["mode"] == "split" else c["qty_all"]
                    q2 = c["qty50"] if c["mode"] == "split" else 0.0
                    zstop = lvl(cand, rL, rng, stop_depth)

        if d != 0 and ((d == 1 and b.l < rL) or (d == -1 and b.h > rH)):
            opp_broke = True

        # ---- slot priority against the other slot's day
        blocked = handover = False
        if other is not None and d != 0:
            if other["filled"] and other["fill_bar"] is not None and other["fill_bar"] <= i:
                blocked = True                      # it executed first: it owns the day
                if c["allow_both"] and other["exit_bar"] is not None and i > other["exit_bar"]:
                    blocked = False                 # its trade is finished, this slot may come back
            if (c["ho"] and other["armed_bar"] is not None and armed_bar is not None
                    and other["armed_bar"] > armed_bar and other["armed_bar"] <= i):
                handover = True                     # it armed later: it takes the pending seat
        if blocked and not units and e1 is not None:
            if (d == 1 and b.l <= e1) or (d == -1 and b.h >= e1):
                missed = True                       # the pullback came and went while held back
        if (blocked or handover) and not units:
            pend, placed = [], None
            if handover:
                dead = True

        win = b.minsIn <= cut
        if (d != 0 and broke and placed is None and not closed and not dead and win
                and (not c["noDbl"] or not opp_broke) and not blocked and not missed):
            pend = [[e1, q1]] + ([[e2, q2]] if e2 is not None and q2 > 0 else [])
            placed = i

        # ---- fills, only from the bar after placement
        if pend and placed is not None and i > placed and not closed:
            rest = []
            for px, q in pend:
                got = None
                if d == 1:
                    got = b.o if b.o <= px else (px if b.l <= px else None)
                else:
                    got = b.o if b.o >= px else (px if b.h >= px else None)
                if got is None:
                    rest.append([px, q]); continue
                pre_bracket = cur_stop is not None
                units.append(dict(px=got, q=q, fb=i, exit=None, why=None, eb=None))
                if first_fill is None:
                    first_fill = i
                    cur_stop, cur_tp = zstop, tp_price(d, rH, rL, rng, dHi, dLo, c["tp"])
                elif pre_bracket:                   # bracket already resting -> can stop on this bar
                    u = units[-1]
                    if (d == 1 and b.l <= cur_stop) or (d == -1 and b.h >= cur_stop):
                        u["exit"] = (cur_stop - SLIP) if d == 1 else (cur_stop + SLIP)
                        u["why"] = "stop"; u["eb"] = i
            pend = rest
            if units and all(u["exit"] is not None for u in units) and not pend:
                closed = True

        if closed or dead or (not win and not units) or (c["noDbl"] and opp_broke and not units):
            pend = []

        # ---- breakeven (only after the entry bar, only from the profitable side)
        if c["be"] and not be_done and units and cur_stop is not None and first_fill is not None and i > first_fill:
            live = [u for u in units if u["exit"] is None]
            if live:
                avg = sum(u["px"] * u["q"] for u in live) / sum(u["q"] for u in live)
                if d == 1 and b.h >= rH and b.c > avg:
                    cur_stop, be_done = max(cur_stop, avg), True
                if d == -1 and b.l <= rL and b.c < avg:
                    cur_stop, be_done = min(cur_stop, avg), True

    pnl = 0.0
    for u in units:
        if u["exit"] is None:
            u["exit"], u["why"], u["eb"] = bars[-1].c, "eod", len(bars) - 1
        pnl += (u["exit"] - u["px"]) * d * u["q"] * PT_VAL - 2 * COMM * u["q"]
    end = [b for b in bars if b.minsIn >= FLAT_MINS]
    return dict(day=bars[0].t.date(), label=(end[0].t.date() if end else bars[-1].t.date()),
                armed=d != 0, dir=d, armed_bar=armed_bar,
                filled=bool(units), fill_bar=first_fill, units=units, pnl=pnl,
                exit_bar=max([u["eb"] for u in units], default=None),
                rH=rH, rL=rL, rng=rng, rngpct=(rng / rL * 100) if rng else None, veto=veto,
                why=units[-1]["why"] if units else None)


def run(days, cfg):
    return [run_day(bars, cfg) for bars in days]


def stats(res):
    tr = [r for r in res if r["filled"]]
    n = len(tr)
    if n == 0:
        return dict(n=0, ev=0.0, total=0.0, win=0.0, pf=0.0, se=0.0, armed=sum(r["armed"] for r in res))
    p = [r["pnl"] for r in tr]
    tot = sum(p)
    wins = [x for x in p if x > 0]
    gl = -sum(x for x in p if x < 0)
    mean = tot / n
    var = sum((x - mean) ** 2 for x in p) / (n - 1) if n > 1 else 0.0
    return dict(n=n, ev=mean, total=tot, win=100.0 * len(wins) / n,
                pf=(sum(wins) / gl) if gl > 0 else float("inf"),
                se=(var ** 0.5) / (n ** 0.5) if n > 1 else 0.0,
                armed=sum(r["armed"] for r in res))

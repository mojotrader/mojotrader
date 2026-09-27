"""ORBIB - Halyard + ORB + IB in one account, ported from the TradingView script ``ORBIB``.

Run it on 1-MINUTE bars (any timeframe that divides 15 works). Halyard builds its own 15-minute candles
from the chart bars, exactly like the Pine version.

THE THREE SETUPS
  HALYARD  (pullback version) The 15m candle that opens at 05:00 UTC (00:00 EST / 01:00 EDT) is the range.
           The first 15m candle that CLOSES beyond it is the signal. Everything is measured on one ruler:
           signal close -> opposite range edge = 100%. Entry = a LIMIT pullback % back toward the edge (or
           market at the close), stop = stop % along the ruler, target = target % beyond the close. The limit
           is cancelled after N 15m bars, if the target trades first, or at session end. After the first trade
           (loss, or also target / no fill) an opposite 15m close beyond the range takes one reverse trade.
           Optional entry cutoff. Session day runs to 13:30 EST / 14:30 EDT, where it flattens.
           Range, signal, cutoff and expiry use 15m candles; fills and the target-first cancel use chart bars.
  ORB      09:30-09:45 ET range. Direction: low printed first -> long, high first -> short (optional close-depth
           filter). After the range BREAKS, a limit rests at the 25% (shallow close) or 50% fib pullback, with an
           optional average-down unit at 50%. Stop = far side of the range; target = extension of the range.
           Must fill before the IB forms (10:30); skipped if the range is < min % of price.
  IB       Same engine on the 09:30-10:30 range.

ONE SEAT (same precedence as the Pine script)
  1. A live Halyard trade holds ORB/IB limits BACK; if price trades through their entry meanwhile, that setup
     is forfeited for the day.
  2. A live ORB trade holds the IB back the same way.
  3. Halyard skips its break if an ORB/IB trade (or any position) is live - the break still consumes the day.
  ORB/IB flatten at 15:30 ET (closes everything). Optional daily max loss $ closes everything and stops the day.

DIFFERENCES FROM TRADINGVIEW (small, and on the realistic side)
  * Fills come from the backtest engine, so each setup knows exactly which of its orders filled.
  * A bracket (stop + target) is live from the moment an entry fills; Pine attaches it one bar later.
  * Positions are tracked per order (like separate bracket orders at IBKR) instead of Pine's single netted
    position. The seat rules above keep setups from overlapping, so this almost never matters.
  * A pending Halyard limit holds the seat like an open Halyard trade (ORB/IB are held back behind it).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..params import Param, defaults

TP_OPTIONS = ["Range level", "Ext 10%", "Ext 30%", "Ext 50%", "HOD/LOD"]
BREAK_OPTIONS = ["Close beyond", "Wick (touch)"]
DIR_OPTIONS = ["Both", "Long", "Short", "Off"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

G_RUN, G_SIZE, G_FILT = "Strategies", "Sizing - ORB / IB", "Filters - ORB / IB"
G_HAL, G_ORB, G_IB, G_DAYS = "Halyard", "ORB settings", "IB settings", "Trade days"

PARAMS: list[Param] = [
    Param("run_hal", "Run HALYARD", True, G_RUN),
    Param("run_orb", "Run ORB", True, G_RUN),
    Param("run_ib", "Run IB", True, G_RUN),
    # ---- ORB / IB sizing
    Param("size_mode", "Sizing mode", "Fixed contracts", G_SIZE, ["Fixed contracts", "Risk $ per entry"],
          help="Fixed: trade the contract counts below. Risk $: size each entry so entry-to-stop risk = the $ below."),
    Param("orb_qty1", "ORB contracts - first entry", 1, G_SIZE, min=1),
    Param("orb_qty2", "ORB contracts - avg-down entry", 1, G_SIZE, min=1),
    Param("ib_qty1", "IB contracts - first entry", 2, G_SIZE, min=1),
    Param("ib_qty2", "IB contracts - avg-down entry", 2, G_SIZE, min=1),
    Param("orb_risk1", "ORB $ risk - first entry", 150.0, G_SIZE, min=1.0, step=25.0),
    Param("orb_risk2", "ORB $ risk - avg-down entry", 150.0, G_SIZE, min=1.0, step=25.0),
    Param("ib_risk1", "IB $ risk - first entry", 150.0, G_SIZE, min=1.0, step=25.0),
    Param("ib_risk2", "IB $ risk - avg-down entry", 150.0, G_SIZE, min=1.0, step=25.0),
    Param("max_day_loss", "Daily max loss $ (0 = off)", 0.0, G_SIZE, min=0.0, step=25.0,
          help="Counted from the 09:30 open incl. open P&L. When hit: close everything, no more trades today."),
    # ---- ORB / IB filters
    Param("no_dbl", "Skip double-break days", True, G_FILT,
          help="If the opposite side of the range also breaks, cancel the setup for the day."),
    Param("orb_depth", "ORB bias: max close depth into range (%)", 100.0, G_FILT, min=0.0, max=100.0, step=5.0,
          help="100 = filter off. 50 = classic rule."),
    Param("ib_depth", "IB bias: max close depth into range (%)", 100.0, G_FILT, min=0.0, max=100.0, step=5.0),
    Param("orb_min_rng_pct", "Min ORB range (% of price, 0 = off)", 0.40, G_FILT, min=0.0, step=0.05),
    # ---- Halyard
    Param("hal_use_risk", "Halyard: size by $ risk", False, G_HAL),
    Param("hal_risk_usd", "Halyard: risk per trade $", 535.0, G_HAL, min=0.0, step=5.0),
    Param("hal_qty", "Halyard: contracts (if $ risk off)", 4, G_HAL, min=1),
    Param("hal_max_qty", "Halyard: max contracts", 1000, G_HAL, min=1),
    Param("hal_target_pct", "Halyard: target % (first trade)", 90.0, G_HAL, min=1.0, step=5.0,
          help="Ruler: signal 15m close -> opposite range edge = 100%. 90 = target 0.9 x that distance beyond the "
               "close. Fixed - a tighter stop or deeper pullback does not move it."),
    Param("hal_rev_on", "Halyard: enable reverse trade", True, G_HAL),
    Param("hal_rev_target_pct", "Halyard: target % (reverse trade)", 100.0, G_HAL, min=1.0, step=5.0),
    Param("hal_rev_after", "Halyard: reverse trade after", "Loss or Target", G_HAL, ["Loss or Target", "Loss Only"],
          help="Loss or Target: reversal arms after the first trade stops out, hits target, or its limit never "
               "fills. Loss Only: only after a stop-out."),
    Param("hal_entry_mode", "Halyard: entry type", "Pullback Limit", G_HAL, ["Pullback Limit", "Market at Close"]),
    Param("hal_pullback_pct", "Halyard: pullback %", 10.0, G_HAL, min=1.0, max=99.0, step=1.0,
          help="Limit sits this % of the way from the signal close back toward the stop edge."),
    Param("hal_stop_pct", "Halyard: stop loss %", 96.0, G_HAL, min=1.0, max=200.0, step=1.0,
          help="100 = stop on the opposite range edge. 96 = 4% tighter. 110 = 10% beyond it."),
    Param("hal_expiry_bars", "Halyard: cancel limit after (15m bars, 0 = session end)", 4, G_HAL, min=0),
    Param("hal_cancel_on_target", "Halyard: cancel limit if target reached first", True, G_HAL),
    Param("hal_pullback_on_rev", "Halyard: use pullback entry on reversal too", True, G_HAL),
    Param("hal_use_cutoff", "Halyard: stop new entries after cutoff", True, G_HAL),
    Param("hal_cutoff", "Halyard: entry cutoff (ET)", "10:30", G_HAL,
          help="Measured on the signal 15m candle's open time, within the Halyard session (ends 13:30 EST / 14:30 EDT)."),
    Param("hal_rng_min", "Halyard: min range (points, 0 = off)", 0.0, G_HAL, min=0.0, step=0.25),
    Param("hal_rng_max", "Halyard: max range (points, 0 = off)", 0.0, G_HAL, min=0.0, step=0.25),
    *[Param(f"hal_{d.lower()}", f"Halyard {n}", "Long" if d == "Mon" else "Both", G_DAYS, DIR_OPTIONS)
      for d, n in zip(DAYS, DAY_NAMES)],
    *[Param(f"orb_{d.lower()}", f"ORB {n}", True, G_DAYS) for d, n in zip(DAYS, DAY_NAMES)],
    *[Param(f"ib_{d.lower()}", f"IB {n}", True, G_DAYS) for d, n in zip(DAYS, DAY_NAMES)],
    # ---- ORB / IB engine settings (hardcoded config in the Pine script)
    Param("orb_long", "ORB longs", True, G_ORB),
    Param("orb_short", "ORB shorts", True, G_ORB),
    Param("orb_break", "ORB break type", "Close beyond", G_ORB, BREAK_OPTIONS),
    Param("orb_avg", "ORB average-down unit", True, G_ORB),
    Param("orb_tp_long", "ORB target - long", "Ext 10%", G_ORB, TP_OPTIONS),
    Param("orb_tp_short", "ORB target - short", "Ext 30%", G_ORB, TP_OPTIONS),
    Param("orb_be", "ORB breakeven when range level reclaimed", False, G_ORB),
    Param("orb_cutoff", "ORB last entry time (ET)", "15:00", G_ORB),
    Param("ib_long", "IB longs", True, G_IB),
    Param("ib_short", "IB shorts", True, G_IB),
    Param("ib_break", "IB break type", "Wick (touch)", G_IB, BREAK_OPTIONS),
    Param("ib_avg", "IB average-down unit", True, G_IB),
    Param("ib_tp_long", "IB target - long", "Ext 30%", G_IB, TP_OPTIONS),
    Param("ib_tp_short", "IB target - short", "Ext 30%", G_IB, TP_OPTIONS),
    Param("ib_be", "IB breakeven when range level reclaimed", False, G_IB),
    Param("ib_cutoff", "IB last entry time (ET)", "14:00", G_IB),
    Param("flatten_time", "ORB/IB flatten time (ET)", "15:30", G_IB),
]


def _hhmm(s: str) -> int:
    h, m = str(s).strip().split(":")
    return int(h) * 60 + int(m)


def f_size(budget: float, pt_dist: float, pt_val: float) -> int:
    """Contracts for a $ budget at a stop distance; fraction > .5 rounds up, otherwise down (Pine f_size)."""
    per_c = pt_dist * pt_val
    raw = budget / per_c if per_c > 0 else 0.0
    fr = raw - math.floor(raw)
    return math.ceil(raw) if fr > 0.5 else math.floor(raw)


def f_tp(d, rH, rL, rg, dHi, dLo, tp_long, tp_short) -> float:
    ext = {"Ext 10%": 0.10, "Ext 30%": 0.30, "Ext 50%": 0.50}
    if d == 1:
        return dHi if tp_long == "HOD/LOD" else rH + ext.get(tp_long, 0.0) * rg
    return dLo if tp_short == "HOD/LOD" else rL - ext.get(tp_short, 0.0) * rg


class _Pullback:
    """One ORB or IB pullback engine (the Pine ``or_*`` / ``ib_*`` blocks)."""

    def __init__(self, name: str, p: dict, range_start: int, range_end: int):
        self.name = name                        # "ORB" or "IB" - also the order group
        self.rs, self.re = range_start, range_end
        k = name.lower()
        self.long_ok, self.short_ok = p[f"{k}_long"], p[f"{k}_short"]
        self.brk_type, self.avg, self.be = p[f"{k}_break"], p[f"{k}_avg"], p[f"{k}_be"]
        self.tp_l, self.tp_s = p[f"{k}_tp_long"], p[f"{k}_tp_short"]
        self.depth = p[f"{k}_depth"] / 100.0
        self.cutoff = _hhmm(p[f"{k}_cutoff"])
        self.days = [p[f"{k}_{d.lower()}"] for d in DAYS]
        self.by_risk = p["size_mode"] == "Risk $ per entry"
        self.q1, self.q2 = int(p[f"{k}_qty1"]), int(p[f"{k}_qty2"])
        self.r1, self.r2 = p[f"{k}_risk1"], p[f"{k}_risk2"]
        self.tags = (f"{name}-1", f"{name}-2")
        self.prev_in_range = False
        self.reset()

    def reset(self):
        self.rH = self.rL = None
        self.rHi = self.rLi = None
        self.dir = 0
        self.shallow = False
        self.e1 = self.e2 = self.zstop = self.rng = None
        self.broke = self.placed = self.f1 = self.f2 = False
        self.cur_stop = self.cur_tp = None
        self.be_done = self.closed = self.opp_broke = self.missed = False
        self.sz1 = self.sz2 = 0

    @property
    def filled(self) -> bool:
        return self.f1 or self.f2

    @property
    def use_add(self) -> bool:
        return self.avg and self.shallow and self.e2 is not None

    def live(self) -> bool:
        return self.filled and not self.closed

    def update_range(self, i, bar, tod, prev_close, pt_val):
        """Build the range; on the first bar after it, decide direction, levels and size.
        Returns True on that 'range done' bar."""
        in_r = self.rs <= tod < self.re
        done = (not in_r) and self.prev_in_range
        self.prev_in_range = in_r
        if in_r:
            if self.rH is None or bar.high > self.rH:
                self.rH, self.rHi = bar.high, i
            if self.rL is None or bar.low < self.rL:
                self.rL, self.rLi = bar.low, i
        if done and self.rH is not None and self.rH > self.rL:
            rng = self.rng = self.rH - self.rL
            low_first = self.rLi < self.rHi
            pos = (prev_close - self.rL) / rng
            if self.long_ok and low_first and pos >= 1.0 - self.depth:
                self.dir, self.shallow = 1, pos >= 0.75
                self.e1 = self.rL + (0.75 if self.shallow else 0.5) * rng
                self.zstop = self.rL
            elif self.short_ok and not low_first and pos <= self.depth:
                self.dir, self.shallow = -1, pos <= 0.25
                self.e1 = self.rL + (0.25 if self.shallow else 0.5) * rng
                self.zstop = self.rH
            if self.dir:
                self.e2 = self.rL + 0.5 * rng
                if self.by_risk:
                    self.sz1 = f_size(self.r1, abs(self.e1 - self.zstop), pt_val)
                    self.sz2 = f_size(self.r2, abs(self.e2 - self.zstop), pt_val)
                else:
                    self.sz1, self.sz2 = self.q1, self.q2
        return done

    def update_breaks(self, bar):
        d = self.dir
        if d == 0:
            return
        if self.brk_type == "Wick (touch)":
            brk = bar.high >= self.rH if d == 1 else bar.low <= self.rL
        else:
            brk = bar.close > self.rH if d == 1 else bar.close < self.rL
        if brk:
            self.broke = True
        if (d == 1 and bar.low < self.rL) or (d == -1 and bar.high > self.rH):
            self.opp_broke = True

    def touched_entry(self, bar) -> bool:
        return (self.dir == 1 and bar.low <= self.e1) or (self.dir == -1 and bar.high >= self.e1)

    def place(self, ctx, dHigh, dLow):
        tp = f_tp(self.dir, self.rH, self.rL, self.rng, dHigh, dLow, self.tp_l, self.tp_s)
        pos = "long" if self.dir == 1 else "short"
        ctx.place_entry(self.tags[0], self.dir, self.sz1, self.e1, self.zstop, tp, self.name, f"{pos} first")
        if self.use_add and self.sz2 >= 1:
            ctx.place_entry(self.tags[1], self.dir, self.sz2, self.e2, self.zstop, tp, self.name, f"{pos} avg-down")
        self.placed = True
        ctx.annotate(kind="setup", group=self.name, time=ctx.time, dir=self.dir, rH=self.rH, rL=self.rL,
                     e1=self.e1, e2=self.e2 if self.use_add else None, stop=self.zstop, tp=tp)

    def track_fills(self, ctx, dHigh, dLow):
        first_before = self.filled
        for f in ctx.bar_fills:
            if f.kind == "entry" and f.tag == self.tags[0]:
                self.f1 = True
            if f.kind == "entry" and f.tag == self.tags[1]:
                self.f2 = True
        if self.filled and not first_before:
            self.cur_stop = self.zstop
            self.cur_tp = f_tp(self.dir, self.rH, self.rL, self.rng, dHigh, dLow, self.tp_l, self.tp_s)
            self.be_done = False
            for t in self.tags:
                ctx.set_exits(t, self.cur_stop, self.cur_tp)
        if any(t.tag in self.tags for t in ctx.bar_exits):
            self.closed = True

    def manage(self, ctx, bar):
        if self.be and not self.be_done and self.filled and not self.closed:
            avg_px = (self.e1 + self.e2) / 2 if (self.f1 and self.f2) else (self.e1 if self.f1 else self.e2)
            if (self.dir == 1 and bar.high >= self.rH) or (self.dir == -1 and bar.low <= self.rL):
                self.cur_stop = max(self.cur_stop, avg_px) if self.dir == 1 else min(self.cur_stop, avg_px)
                self.be_done = True
                for t in self.tags:
                    ctx.set_exits(t, stop=self.cur_stop)

    def cancel(self, ctx):
        for t in self.tags:
            ctx.cancel(t)


class ORBIB:
    name = "ORBIB (Halyard + ORB + IB)"
    description = __doc__
    params_spec = PARAMS
    recommended_timeframe = 1

    def __init__(self, params: dict | None = None):
        self.params = {**defaults(PARAMS), **(params or {})}
        p = self.params
        if p["hal_entry_mode"] == "Pullback Limit" and p["hal_pullback_pct"] >= p["hal_stop_pct"]:
            raise ValueError("Halyard: Pullback % must be smaller than Stop Loss % - otherwise the entry would be "
                             "at or past the stop.")
        if p["hal_rng_max"] > 0 and p["hal_rng_min"] > p["hal_rng_max"]:
            raise ValueError("Halyard range filter: Min range is above Max range, so no day can ever pass.")

    # ------------------------------------------------------------------ vectorised per-bar clocks
    def prepare(self, bars: pd.DataFrame):
        idx = bars.index
        utc = idx.tz_convert("UTC")
        epoch_ms = utc.as_unit("ms").asi8.astype(np.int64)
        diffs = np.diff(epoch_ms)
        self.chart_ms = int(np.median(diffs)) if len(diffs) else 60_000
        self.chart_ms = min(self.chart_ms, 15 * 60_000)
        tf = 15 * 60_000
        self.bkt = epoch_ms // tf
        self.bkt_last = (epoch_ms + self.chart_ms) // tf != self.bkt
        self.tod = (idx.hour * 60 + idx.minute).to_numpy()
        self.dow = idx.dayofweek.to_numpy()                 # 0 = Monday (ET calendar day)
        # Halyard session = the Asia/Kolkata calendar day (ends 18:30 UTC = 13:30 EST / 14:30 EDT)
        ist = utc + pd.Timedelta(hours=5, minutes=30)
        self.hal_key = (ist.year * 10000 + ist.month * 100 + ist.day).to_numpy()
        ist_next = ist + pd.Timedelta(milliseconds=self.chart_ms)
        self.hal_key_next = (ist_next.year * 10000 + ist_next.month * 100 + ist_next.day).to_numpy()
        self.hal_dow = ist.dayofweek.to_numpy()
        self.utc_min = (utc.hour * 60 + utc.minute).to_numpy()
        # Halyard session starts 13:30 EST / 14:30 EDT (ET offset 300 / 240 minutes)
        et_off = (self.utc_min - self.tod) % 1440
        self.hal_sess_start = np.where(et_off == 240, 870, 810)
        self._reset_state()

    def _reset_state(self):
        p = self.params
        self.flat_min = _hhmm(p["flatten_time"])
        self.orb = _Pullback("ORB", p, 9 * 60 + 30, 9 * 60 + 45)
        self.ib = _Pullback("IB", p, 9 * 60 + 30, 10 * 60 + 30)
        self.prev_in_sess = False
        self.dHigh = self.dLow = None
        self.day_start_eq = None
        self.day_halt = False
        self.ib_formed = False
        self.prev_close = None
        self.orbib_live = False
        # Halyard 15m builder
        self.agg_bkt = None
        self.aggH = self.aggL = self.aggC = None
        self.aggT = None
        self.agg_done = False
        self.prev_hal_key = None
        self.hal_reset_day()

    def hal_reset_day(self):
        self.halRH = self.halRL = None
        self.hal_broke = False
        self.hal_state = 0       # 0 wait | 1 first trade live | 2 first stopped (reverse armed) | 3 done
        self.hal_first_dir = 0
        self.hal_trades = 0
        self.hal_pending = None  # resting Halyard limit: dict(tag, dir, tp, first, bars)

    # ------------------------------------------------------------------ main loop
    def on_bar(self, ctx, bar):
        p, i = self.params, ctx.i
        pv = ctx.point_value
        tod = int(self.tod[i])
        prev_close = self.prev_close if self.prev_close is not None else bar.close
        self.prev_close = bar.close

        # ---- shared session clock
        in_sess = 570 <= tod < 960
        new_session = in_sess and not self.prev_in_sess
        self.prev_in_sess = in_sess
        if new_session:
            self.dHigh, self.dLow = bar.high, bar.low
        elif in_sess:
            self.dHigh, self.dLow = max(self.dHigh, bar.high), min(self.dLow, bar.low)
        flatten = tod >= self.flat_min
        if new_session:
            self.day_start_eq = ctx.equity
            self.day_halt = False
        if p["max_day_loss"] > 0 and self.day_start_eq is not None and ctx.equity - self.day_start_eq <= -p["max_day_loss"]:
            self.day_halt = True
        if new_session:
            self.orb.reset()
            self.ib.reset()
            self.ib_formed = False

        hal_live = self._halyard(ctx, bar, i)

        # ---- ORB
        orb, ib = self.orb, self.ib
        dow = int(self.dow[i])
        orb.update_range(i, bar, tod, prev_close, pv)
        ib_done = ib.update_range(i, bar, tod, prev_close, pv)
        if ib_done:
            self.ib_formed = True
        orb.update_breaks(bar)
        size_ok = p["orb_min_rng_pct"] <= 0 or (orb.rng is not None and orb.rL > 0
                                               and orb.rng / orb.rL * 100 >= p["orb_min_rng_pct"])
        orb_win = (in_sess and not self.day_halt and dow < 5 and orb.days[dow] and tod <= orb.cutoff
                   and (not p["run_ib"] or not self.ib_formed) and size_ok)
        if p["run_orb"] and orb.dir and orb.broke and not orb.placed and not orb.closed and hal_live and orb.e1 is not None:
            if orb.touched_entry(bar):
                orb.missed = True
        if (p["run_orb"] and orb.dir and orb.broke and not orb.placed and not orb.closed and orb_win and not flatten
                and (not p["no_dbl"] or not orb.opp_broke) and orb.sz1 >= 1 and not hal_live and not orb.missed
                and ctx.position() == 0):
            orb.place(ctx, self.dHigh, self.dLow)
        orb.track_fills(ctx, self.dHigh, self.dLow)
        orb.manage(ctx, bar)
        if (orb.closed or flatten or not p["run_orb"] or orb.missed or (p["no_dbl"] and orb.opp_broke)
                or (not orb_win and not orb.filled)):
            orb.cancel(ctx)

        # ---- IB
        orb_live = p["run_orb"] and orb.live()
        ib_held = orb_live or hal_live
        ib.update_breaks(bar)
        ib_win = in_sess and not self.day_halt and dow < 5 and ib.days[dow] and tod <= ib.cutoff
        if p["run_ib"] and ib.dir and ib.broke and not ib.placed and not ib.closed and ib_held and ib.e1 is not None:
            if ib.touched_entry(bar):
                ib.missed = True
        if (p["run_ib"] and ib.dir and ib.broke and not ib.placed and not ib.closed and ib_win and not flatten
                and (not p["no_dbl"] or not ib.opp_broke) and ib.sz1 >= 1 and not ib_held and not ib.missed
                and ctx.position() == 0):
            ib.place(ctx, self.dHigh, self.dLow)
        ib.track_fills(ctx, self.dHigh, self.dLow)
        ib.manage(ctx, bar)
        if (ib.closed or flatten or not p["run_ib"] or ib.missed or (p["no_dbl"] and ib.opp_broke)
                or (not ib_win and not ib.filled)):
            ib.cancel(ctx)
        self.orbib_live = orb_live or (p["run_ib"] and ib.live())

        # ---- EOD flatten + daily loss limit (reach across all setups)
        if flatten:
            ctx.cancel_all()
            ctx.close_all("EOD flat")
        if self.day_halt:
            ctx.cancel_all()
            ctx.close_all("Daily max loss")

    # ------------------------------------------------------------------ Halyard
    def _halyard(self, ctx, bar, i) -> bool:
        p = self.params
        # 15-minute candle builder (UTC-aligned buckets)
        bkt = int(self.bkt[i])
        new15 = False
        c15 = None
        if self.agg_bkt is not None and bkt != self.agg_bkt and not self.agg_done:   # catch-up after a gap
            new15, c15 = True, (self.aggH, self.aggL, self.aggC, self.aggT)
        if self.agg_bkt is None or bkt != self.agg_bkt:
            self.agg_bkt, self.aggH, self.aggL, self.aggT, self.agg_done = bkt, bar.high, bar.low, i, False
        else:
            self.aggH, self.aggL = max(self.aggH, bar.high), min(self.aggL, bar.low)
        self.aggC = bar.close
        if not self.agg_done and self.bkt_last[i]:
            self.agg_done = True
            new15, c15 = True, (self.aggH, self.aggL, self.aggC, self.aggT)

        key = int(self.hal_key[i])
        new_day = self.prev_hal_key is not None and key != self.prev_hal_key
        self.prev_hal_key = key
        if new_day:
            if self.hal_pending:
                ctx.cancel(self.hal_pending["tag"])
            self.hal_reset_day()

        is_range_bar = new15 and int(self.utc_min[c15[3]]) == 5 * 60
        if is_range_bar:
            self.halRH, self.halRL = c15[0], c15[1]
            self.hal_broke = False
            ctx.annotate(kind="hal_range", group="HAL", time=ctx.bars_time(c15[3]), rH=self.halRH, rL=self.halRL)

        levels = self.halRH is not None
        rng = (self.halRH - self.halRL) if levels else None
        range_ok = levels and (p["hal_rng_min"] <= 0 or rng >= p["hal_rng_min"]) and \
            (p["hal_rng_max"] <= 0 or rng <= p["hal_rng_max"])

        rev_lot = p["hal_rev_after"] == "Loss or Target"
        # ---- fills / exits of Halyard orders on this bar (engine truth)
        pend = self.hal_pending
        if pend and any(f.kind == "entry" and f.tag == pend["tag"] for f in ctx.bar_fills):
            self.hal_pending = pend = None
        if pend and not ctx.is_pending(pend["tag"]):          # cancelled by the EOD flatten / daily halt
            self.hal_pending = pend = None
        for t in ctx.bar_exits:
            if t.group == "HAL" and self.hal_state == 1:
                self.hal_state = 2 if (t.net_pnl < 0 or rev_lot) else 3
        # ---- resting limit: expired, or did the target trade first?
        if pend:
            if new15:
                pend["bars"] += 1
            expired = p["hal_expiry_bars"] > 0 and pend["bars"] >= p["hal_expiry_bars"]
            target_ran = p["hal_cancel_on_target"] and (bar.high >= pend["tp"] if pend["dir"] == 1 else bar.low <= pend["tp"])
            if expired or target_ran:
                ctx.cancel(pend["tag"])
                self.hal_pending = None
                if pend["first"]:
                    self.hal_state = 2 if rev_lot else 3        # never filled: counts like a target
                else:
                    self.hal_state = 3
        hal_open = ctx.group_open("HAL")

        c = c15[2] if new15 else None
        brk_up = p["run_hal"] and new15 and not is_range_bar and range_ok and not self.hal_broke and c > self.halRH
        brk_dn = p["run_hal"] and new15 and not is_range_bar and range_ok and not self.hal_broke and c < self.halRL
        if brk_up or brk_dn:
            self.hal_broke = True

        mode = [p[f"hal_{d.lower()}"] for d in DAYS][int(self.hal_dow[i])] if self.hal_dow[i] < 5 else "Off"
        day_ok = mode != "Off"
        long_ok, short_ok = mode in ("Both", "Long"), mode in ("Both", "Short")
        window_ok = True
        if p["hal_use_cutoff"] and new15:
            start_min = int(self.hal_sess_start[i])
            elapsed = (int(self.tod[c15[3]]) - start_min) % 1440
            window_ok = elapsed <= (_hhmm(p["hal_cutoff"]) - start_min) % 1440
        seat_free = not self.orbib_live and not self.day_halt and ctx.position() == 0
        max_trades = 2 if p["hal_rev_on"] else 1
        can = (day_ok and window_ok and seat_free and self.hal_trades < max_trades and not hal_open
               and self.hal_pending is None)
        use_limit = p["hal_entry_mode"] == "Pullback Limit"

        def enter(d, tgt_pct, allow_pullback, tag, note, first):
            edge = self.halRL if d == 1 else self.halRH
            full = abs(c - edge)
            stop = ctx.round_tick(c - d * full * p["hal_stop_pct"] / 100)
            is_lim = use_limit and allow_pullback
            entry = ctx.round_tick(c - d * full * p["hal_pullback_pct"] / 100) if is_lim else c
            tgt = ctx.round_tick(c + d * full * tgt_pct / 100)
            if p["hal_use_risk"]:
                per_c = abs(entry - stop) * ctx.point_value or ctx.tick_size * ctx.point_value
                q = max(math.floor(p["hal_risk_usd"] / per_c), 1)
            else:
                q = int(p["hal_qty"])
            q = min(q, int(p["hal_max_qty"]))
            if is_lim:
                ctx.place_entry(tag, d, q, entry, stop, tgt, "HAL", note + " (limit)")
                self.hal_pending = dict(tag=tag, dir=d, tp=tgt, first=first, bars=0)
            else:
                ctx.market_entry(tag, d, q, stop, tgt, "HAL", note)
            self.hal_trades += 1
            ctx.annotate(kind="hal_order", group="HAL", time=ctx.time, dir=d, entry=entry, stop=stop, tp=tgt,
                         limit=is_lim)

        if brk_up and long_ok and can and self.hal_state == 0:
            self.hal_first_dir, self.hal_state = 1, 1
            enter(1, p["hal_target_pct"], True, "HAL-L", "long first", True)
        elif brk_dn and short_ok and can and self.hal_state == 0:
            self.hal_first_dir, self.hal_state = -1, 1
            enter(-1, p["hal_target_pct"], True, "HAL-S", "short first", True)
        elif p["hal_rev_on"] and p["run_hal"] and new15 and self.hal_state == 2 and levels and can:
            if self.hal_first_dir == -1 and long_ok and c > self.halRH:
                self.hal_state = 3
                enter(1, p["hal_rev_target_pct"], p["hal_pullback_on_rev"], "HAL-RL", "long reverse", False)
            elif self.hal_first_dir == 1 and short_ok and c < self.halRL:
                self.hal_state = 3
                enter(-1, p["hal_rev_target_pct"], p["hal_pullback_on_rev"], "HAL-RS", "short reverse", False)

        # ---- session end: cancel a resting limit and flatten Halyard's own position
        busy = ctx.group_open("HAL") or self.hal_pending is not None
        if busy and (int(self.hal_key_next[i]) != key or new_day):
            if self.hal_pending:
                ctx.cancel(self.hal_pending["tag"])
                self.hal_pending = None
            ctx.close_group("HAL", "Halyard day flat")
        # a resting Halyard limit holds the seat like an open Halyard trade
        return ctx.group_open("HAL") or self.hal_pending is not None

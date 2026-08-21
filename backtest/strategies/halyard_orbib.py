"""Halyard + Break-then-Pullback (ORB + IB) -- three setups, one equity curve.

Port of ``halyard_orbib_combined.pine``. Three independent setups share a
single position through a strict precedence ("the seat"):

1. A live Halyard trade holds the seat. ORB/IB limits are held back, not
   cancelled; if price trades through their entry during the hold, that setup
   is forfeited for the day.
2. A live ORB trade holds the seat against IB.
3. Halyard skips its break entirely if ORB or IB is live -- it is a market
   order at a 15m close and cannot rest and wait.

HALYARD IS NOT AN RTH STRATEGY. Its range candle is 10:30 Asia/Kolkata
(05:00 UTC), which lands at 00:00 EST or 01:00 EDT, and its trading day rolls
at 13:30/14:30 ET. India has no daylight saving and the US does, so the New
York hour of both anchors *moves* twice a year; the offset is read off each
bar rather than assumed. Backtesting this file therefore needs overnight data
-- roughly 00:00 to 15:30 ET. RTH-only bars cannot exercise Halyard at all.

Two deliberate departures from the Pine, both documented at their site:
``_ORB_FILL_TIMING`` and ``_HALYARD_SEAT_LAG``. Everything else follows the
source, including its shipped configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..bars import Bar, NY
from ..engine import Side, Trade
from ..portfolio import Portfolio
from ..tf import TFAggregator, HTFBar

# ---------------------------------------------------------------- clock helpers

def et_offset_minutes(ts: datetime) -> int:
    """Minutes New York is behind UTC on this bar: 300 under EST, 240 under EDT.

    Read from the timestamp rather than assumed, because the whole point of
    Halyard's anchors is that their New York hour moves with US daylight saving
    while the India-side anchor does not.
    """
    ny = ts.astimezone(NY)
    return int(-(ny.utcoffset().total_seconds()) // 60)


def hal_range_start_min(ts: datetime) -> int:
    """10:30 Asia/Kolkata on the New York clock: 00:00 EST, 01:00 EDT."""
    return 60 if et_offset_minutes(ts) == 240 else 0


def hal_session_end_min(ts: datetime) -> int:
    """00:00 Asia/Kolkata on the New York clock: 13:30 EST, 14:30 EDT."""
    return 870 if et_offset_minutes(ts) == 240 else 810


def hal_session_key(ts: datetime) -> int:
    """YYYYMMDD of the session this bar belongs to.

    Shifting a bar forward to its own session boundary makes the session's date
    simply the New York date of the shifted instant, with no wrap-around
    arithmetic. Incrementing a YYYYMMDD key instead would break at every month
    end: 20260531 + 1 is not 20260601.
    """
    shifted = ts + timedelta(minutes=1440 - hal_session_end_min(ts))
    d = shifted.astimezone(NY).date()
    return d.year * 10000 + d.month * 100 + d.day


def hal_session_weekday(ts: datetime) -> int:
    """Weekday of the SESSION (Monday=0). A bar past the roll is already tomorrow."""
    shifted = ts + timedelta(minutes=1440 - hal_session_end_min(ts))
    return shifted.astimezone(NY).weekday()


# ---------------------------------------------------------------- configuration

@dataclass
class PullbackConfig:
    """One break-then-pullback setup. ORB and IB are the same engine."""

    name: str
    ids_long: tuple[str, str]
    ids_short: tuple[str, str]
    range_start: int          # NY minute the range window opens
    range_end: int            # NY minute it closes
    break_type: str           # "close" or "wick"
    tp_long: str
    tp_short: str
    cutoff: int               # NY minute after which no new entry is armed
    depth_pct: float          # max close depth into the range, 0..100
    qty1: float
    qty2: float
    use_avg: bool
    min_range_pct: float = 0.0    # 0 = off (ORB only in the shipped config)


@dataclass
class Config:
    """The Pine file's shipped settings, transcribed."""

    # session
    rth_start: int = 570      # 09:30 NY
    rth_end: int = 960        # 16:00 NY
    flatten_at: int = 930     # 15:30 NY

    # contract
    point_value: float = 20.0     # NQ. The Pine's ptVal input defaults to 2.0 (MNQ).
    tick_size: float = 0.25
    commission_per_contract: float = 0.5
    slippage_ticks: float = 1.0

    # Halyard
    run_halyard: bool = True
    hal_rr: float = 0.8
    hal_reverse: bool = False
    hal_reverse_rr: float = 1.0
    hal_sl_buffer: float = 0.0
    hal_risk_usd: float = 535.0
    hal_use_risk_size: bool = True
    hal_qty_fixed: int = 1
    hal_max_qty: int = 1000
    hal_max_long_per_day: int = 1
    hal_max_short_per_day: int = 1
    hal_range_min: float = 0.0
    hal_range_max: float = 0.0
    # Monday is long-only in the shipped config; the rest trade both ways.
    hal_days: dict = field(default_factory=lambda: {
        0: "Long", 1: "Both", 2: "Both", 3: "Both", 4: "Both", 5: "Off", 6: "Off",
    })

    # ORB / IB
    run_orb: bool = True
    run_ib: bool = True
    no_double_break: bool = True
    orb_carry: bool = False
    max_day_loss: float = 0.0   # 0 = off, as shipped

    orb: PullbackConfig = field(default_factory=lambda: PullbackConfig(
        name="ORB", ids_long=("ORL", "ORL2"), ids_short=("ORS", "ORS2"),
        range_start=570, range_end=585, break_type="close",
        tp_long="Ext 10%", tp_short="Ext 30%", cutoff=900,
        depth_pct=75.0, qty1=2, qty2=2, use_avg=True, min_range_pct=0.40))

    ib: PullbackConfig = field(default_factory=lambda: PullbackConfig(
        name="IB", ids_long=("IBL", "IBL2"), ids_short=("IBS", "IBS2"),
        range_start=570, range_end=630, break_type="wick",
        tp_long="Ext 30%", tp_short="Ext 30%", cutoff=840,
        depth_pct=75.0, qty1=1, qty2=1, use_avg=True, min_range_pct=0.0))


def resolve_tp(direction: int, r_high: float, r_low: float, rng: float,
               day_high: float, day_low: float, tp_long: str, tp_short: str) -> float:
    """Target from the range extension table."""
    spec = tp_long if direction == 1 else tp_short
    if direction == 1:
        return {"HOD/LOD": day_high, "Ext 10%": r_high + 0.10 * rng,
                "Ext 30%": r_high + 0.30 * rng, "Ext 50%": r_high + 0.50 * rng}.get(spec, r_high)
    return {"HOD/LOD": day_low, "Ext 10%": r_low - 0.10 * rng,
            "Ext 30%": r_low - 0.30 * rng, "Ext 50%": r_low - 0.50 * rng}.get(spec, r_low)


# ---------------------------------------------------------- pullback engine

class PullbackEngine:
    """Break-then-pullback: build a range, wait for a break, then rest a limit
    back inside it at a fib level, with an optional second unit deeper in.

    The stop is always the far side of the range, so the two units share it and
    the deeper unit necessarily risks less per contract.
    """

    def __init__(self, cfg: PullbackConfig, day_gate: dict[int, bool] | None = None) -> None:
        self.cfg = cfg
        self.day_gate = day_gate or {i: True for i in range(7)}
        self.reset()

    def reset(self) -> None:
        self.r_high: float | None = None
        self.r_low: float | None = None
        self.r_high_bar = -1
        self.r_low_bar = -1
        self.rng: float | None = None
        self.direction = 0
        self.shallow = False
        self.e1: float | None = None
        self.e2: float | None = None
        self.zstop: float | None = None
        self.qty1 = 0.0
        self.qty2 = 0.0
        self.broke = False
        self.placed = False
        self.opp_broke = False
        self.missed = False
        self.closed = False
        self.filled_any = False
        self.cur_stop: float | None = None
        self.cur_tp: float | None = None
        self._in_range_prev = False

    # -- range construction -------------------------------------------------
    def observe(self, bar: Bar, bar_index: int, prev_close: float | None) -> None:
        """Accumulate the range, and resolve the setup when the window closes."""
        minute = bar.ny_minute()
        in_range = self.cfg.range_start <= minute < self.cfg.range_end

        if in_range:
            if self.r_high is None or bar.high > self.r_high:
                self.r_high = bar.high
                self.r_high_bar = bar_index
            if self.r_low is None or bar.low < self.r_low:
                self.r_low = bar.low
                self.r_low_bar = bar_index

        range_done = (not in_range) and self._in_range_prev
        self._in_range_prev = in_range

        if range_done and self.r_high is not None and self.r_high > self.r_low:
            self._resolve(prev_close)

    def _resolve(self, prev_close: float | None) -> None:
        """Pick a direction from which extreme formed first and where it closed.

        ``prev_close`` is the close of the range window's final bar -- the last
        information available before the window ended, so there is no lookahead
        in reading it here.
        """
        if prev_close is None:
            return
        c = self.cfg
        self.rng = self.r_high - self.r_low
        low_first = self.r_low_bar < self.r_high_bar
        pos = (prev_close - self.r_low) / self.rng
        depth = c.depth_pct / 100.0

        if low_first and pos >= 1.0 - depth:
            self.direction = 1
            self.shallow = pos >= 0.75
            self.e1 = self.r_low + (0.75 if self.shallow else 0.5) * self.rng
            self.e2 = self.r_low + 0.5 * self.rng
            self.zstop = self.r_low
        elif (not low_first) and pos <= depth:
            self.direction = -1
            self.shallow = pos <= 0.25
            self.e1 = self.r_low + (0.25 if self.shallow else 0.5) * self.rng
            self.e2 = self.r_low + 0.5 * self.rng
            self.zstop = self.r_high

        if self.direction != 0:
            self.qty1, self.qty2 = c.qty1, c.qty2

    # -- per-bar state ------------------------------------------------------
    @property
    def use_add(self) -> bool:
        return self.cfg.use_avg and self.shallow and self.e2 is not None

    @property
    def ids(self) -> tuple[str, str]:
        return self.cfg.ids_long if self.direction == 1 else self.cfg.ids_short

    def size_ok(self) -> bool:
        if self.cfg.min_range_pct <= 0:
            return True
        return self.rng is not None and self.r_low > 0 and \
            (self.rng / self.r_low * 100.0) >= self.cfg.min_range_pct

    def update_break(self, bar: Bar) -> None:
        """Latch the break, and note a violation of the opposite extreme."""
        if self.direction == 0:
            return
        if self.cfg.break_type == "wick":
            hit = (self.direction == 1 and bar.high >= self.r_high) or \
                  (self.direction == -1 and bar.low <= self.r_low)
        else:
            hit = (self.direction == 1 and bar.close > self.r_high) or \
                  (self.direction == -1 and bar.close < self.r_low)
        if hit:
            self.broke = True
        if self.direction == 1 and bar.low < self.r_low:
            self.opp_broke = True
        if self.direction == -1 and bar.high > self.r_high:
            self.opp_broke = True

    def traded_through_entry(self, bar: Bar) -> bool:
        """Did price reach the pullback entry while another setup held the seat?"""
        if self.e1 is None:
            return False
        return (self.direction == 1 and bar.low <= self.e1) or \
               (self.direction == -1 and bar.high >= self.e1)


# ------------------------------------------------------------ combined strategy

class HalyardORBIB:
    """Drives all three setups against one :class:`Portfolio`.

    The per-bar order of operations mirrors the Pine file top to bottom: shared
    session clock, then Halyard, then ORB, then IB, then the end-of-day
    flatten. That order matters -- Halyard reads the ORB/IB seat flag as it
    stood on the previous bar, because those engines run below it in the source.
    """

    HAL_IDS = ("HAL-L", "HAL-S", "HAL-RL", "HAL-RS")

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config()
        c = self.cfg

        self.agg = TFAggregator(15, 60)
        self.orb = PullbackEngine(c.orb)
        self.ib = PullbackEngine(c.ib)

        # Halyard state
        self.hal_session_key: int | None = None
        self.hal_r_high: float | None = None
        self.hal_r_low: float | None = None
        self.hal_broke = False
        self.hal_state = 0            # 0 waiting, 1 running, 2 stopped out, 3 done
        self.hal_first_dir = 0
        self.hal_trades = 0
        self.hal_dir = 0
        self.hal_entry: float | None = None
        self.hal_open_id: str | None = None
        self.hal_longs_today = 0
        self.hal_shorts_today = 0
        self._et_day: int | None = None

        # shared session state
        self.day_high: float | None = None
        self.day_low: float | None = None
        self.day_halt = False
        self.day_start_equity = 0.0
        self.ib_formed = False
        self.orbib_live = False       # read by Halyard one bar stale, as in Pine
        self._prev_in_rth = False
        self._prev_close: float | None = None
        self._prev_bar: Bar | None = None

        self.log: list[str] = []

    @property
    def hal_max_trades(self) -> int:
        """Derived from the reversal switch: 2 when armed, 1 otherwise."""
        return 2 if self.cfg.hal_reverse else 1

    # ------------------------------------------------------------------ main
    def on_bar(self, bar: Bar, pf: Portfolio, i: int, exits: list[Trade]) -> None:
        c = self.cfg
        minute = bar.ny_minute()
        in_rth = c.rth_start <= minute < c.rth_end and bar.ny().weekday() < 5
        new_rth = in_rth and not self._prev_in_rth

        # ---- shared session clock -------------------------------------
        if new_rth:
            self.day_high, self.day_low = bar.high, bar.low
            self.day_start_equity = pf.equity(bar.close)
            self.day_halt = False
            self.ib_formed = False
            self.orb.reset()
            self.ib.reset()
        elif in_rth:
            self.day_high = max(self.day_high, bar.high) if self.day_high is not None else bar.high
            self.day_low = min(self.day_low, bar.low) if self.day_low is not None else bar.low

        if c.max_day_loss > 0:
            if pf.equity(bar.close) - self.day_start_equity <= -c.max_day_loss:
                self.day_halt = True

        flatten = minute >= c.flatten_at
        weekday = bar.ny().weekday()

        # ---- Halyard's daily caps roll on the NY calendar day ---------
        d = bar.ny().date()
        et_day = d.year * 10000 + d.month * 100 + d.day
        if et_day != self._et_day:
            self._et_day = et_day
            self.hal_longs_today = 0
            self.hal_shorts_today = 0

        # ---- resolve exits before anything reads the seat -------------
        self._absorb_exits(exits, bar)

        # ---- Halyard --------------------------------------------------
        candle = self.agg.update(bar)
        self._halyard(bar, pf, i, candle)

        # ---- ORB then IB ----------------------------------------------
        if in_rth:
            self.orb.observe(bar, i, self._prev_close)
            self.ib.observe(bar, i, self._prev_close)
            if self.ib._in_range_prev is False and self.ib.rng is not None:
                self.ib_formed = True

        hal_live = self.hal_dir != 0
        self._pullback(self.orb, bar, pf, i, in_rth, flatten, weekday,
                       held=hal_live, extra_gate=(not c.run_ib or not self.ib_formed),
                       enabled=c.run_orb)
        orb_live = c.run_orb and pf.any_open(*c.orb.ids_long, *c.orb.ids_short)
        self._pullback(self.ib, bar, pf, i, in_rth, flatten, weekday,
                       held=hal_live or orb_live, extra_gate=True, enabled=c.run_ib)
        ib_live = c.run_ib and pf.any_open(*c.ib.ids_long, *c.ib.ids_short)

        # Carry hand-over: an unfilled carried ORB gives way once IB arms.
        if c.run_orb and c.orb_carry and not self.orb.closed and not orb_live \
                and self.ib.direction != 0 and self.ib.broke:
            pf.cancel(*c.orb.ids_long, *c.orb.ids_short)
            self.orb.closed = True

        # Seat flag Halyard will read on the NEXT bar, as in the source.
        self.orbib_live = orb_live or ib_live

        # ---- EOD flatten (ORB/IB ids only) + daily halt ---------------
        if flatten:
            pf.cancel(*c.orb.ids_long, *c.orb.ids_short, *c.ib.ids_long, *c.ib.ids_short)
            pf.close(*c.orb.ids_long, *c.orb.ids_short,
                     *c.ib.ids_long, *c.ib.ids_short, reason="EOD flat")
        if self.day_halt:
            pf.close_all("daily max loss")
            pf.cancel_all()

        self._prev_in_rth = in_rth
        self._prev_close = bar.close
        self._prev_bar = bar

    # -------------------------------------------------------------- Halyard
    def _absorb_exits(self, exits: list[Trade], bar: Bar) -> None:
        """Advance Halyard's state machine from the broker's actual fills."""
        for t in exits:
            if t.entry_tag in self.HAL_IDS:
                if self.hal_state == 1:
                    self.hal_state = 2 if t.exit_reason == "stop" else 3
                self.hal_dir = 0
                self.hal_open_id = None

    def _halyard(self, bar: Bar, pf: Portfolio, i: int, candle: HTFBar | None) -> None:
        c = self.cfg
        if not c.run_halyard:
            return

        key = hal_session_key(bar.ts)
        new_day = key != self.hal_session_key

        # Day flat BEFORE the roll clears the state: never carry a position
        # past the session whose levels it belongs to.
        if new_day and self.hal_dir != 0:
            pf.close(*self.HAL_IDS, reason="day flat")
            if self.hal_state == 1:
                lost = (bar.close < self.hal_entry) if self.hal_dir == 1 \
                    else (bar.close > self.hal_entry)
                self.hal_state = 2 if lost else 3
            self.hal_dir = 0

        if new_day:
            self.hal_session_key = key
            self.hal_r_high = None
            self.hal_r_low = None
            self.hal_broke = False
            self.hal_state = 0
            self.hal_first_dir = 0
            self.hal_trades = 0

        if candle is None:
            return

        # The range candle is the 15m bar opening at the India-derived anchor.
        c_min = candle.open_ts.astimezone(NY).hour * 60 + candle.open_ts.astimezone(NY).minute
        is_range_bar = c_min == hal_range_start_min(candle.open_ts)
        if is_range_bar:
            self.hal_r_high = candle.high
            self.hal_r_low = candle.low
            self.hal_broke = False
            return

        if self.hal_r_high is None or self.hal_r_low is None:
            return

        rng = self.hal_r_high - self.hal_r_low
        range_ok = (c.hal_range_min <= 0 or rng >= c.hal_range_min) and \
                   (c.hal_range_max <= 0 or rng <= c.hal_range_max)
        if not range_ok:
            return

        brk_up = not self.hal_broke and candle.close > self.hal_r_high
        brk_dn = not self.hal_broke and candle.close < self.hal_r_low
        if brk_up or brk_dn:
            # The first break consumes the day whichever side it fires on, and
            # whether or not it passes its filters -- a filter can only remove a
            # trade, never substitute a later one.
            self.hal_broke = True

        mode = c.hal_days.get(hal_session_weekday(bar.ts), "Off")
        day_ok = mode != "Off"
        long_ok = mode in ("Both", "Long")
        short_ok = mode in ("Both", "Short")

        # _HALYARD_SEAT_LAG: orbib_live is the previous bar's value, exactly as
        # in the source, where the ORB/IB engines run below this one.
        seat_free = (not self.orbib_live) and (not self.day_halt) and pf.net_position() == 0

        if self.hal_dir != 0 or not day_ok or not seat_free:
            return
        if self.hal_trades >= self.hal_max_trades:
            return

        entry = candle.close
        if brk_up and long_ok and self.hal_state == 0 and \
                self.hal_longs_today < c.hal_max_long_per_day:
            self._hal_enter(pf, bar, i, 1, entry, c.hal_rr, "HAL-L", first=True)
        elif brk_dn and short_ok and self.hal_state == 0 and \
                self.hal_shorts_today < c.hal_max_short_per_day:
            self._hal_enter(pf, bar, i, -1, entry, c.hal_rr, "HAL-S", first=True)
        elif c.hal_reverse and self.hal_state == 2:
            # The reversal is gated on the losing first trade, the weekday and
            # the direction only; the entry filters belong to the first trade.
            if candle.close > self.hal_r_high and self.hal_first_dir == -1 and long_ok \
                    and self.hal_longs_today < c.hal_max_long_per_day:
                self._hal_enter(pf, bar, i, 1, entry, c.hal_reverse_rr, "HAL-RL", first=False)
            elif candle.close < self.hal_r_low and self.hal_first_dir == 1 and short_ok \
                    and self.hal_shorts_today < c.hal_max_short_per_day:
                self._hal_enter(pf, bar, i, -1, entry, c.hal_reverse_rr, "HAL-RS", first=False)

    def _hal_enter(self, pf: Portfolio, bar: Bar, i: int, direction: int,
                   entry: float, rr: float, oid: str, first: bool) -> None:
        c = self.cfg
        stop = (self.hal_r_low - c.hal_sl_buffer) if direction == 1 \
            else (self.hal_r_high + c.hal_sl_buffer)
        risk = abs(entry - stop)
        target = entry + risk * rr if direction == 1 else entry - risk * rr

        if c.hal_use_risk_size:
            per_contract = risk * c.point_value
            floor_unit = c.tick_size * c.point_value
            qty = max(int(c.hal_risk_usd // (per_contract if per_contract > 0 else floor_unit)), 1)
        else:
            qty = c.hal_qty_fixed
        qty = min(qty, c.hal_max_qty)

        side = Side.LONG if direction == 1 else Side.SHORT
        pf.market_entry(oid, side, float(qty), group="HAL")
        # Halyard fills at the 15m close, so the bracket is known now and is
        # attached as soon as the fill lands.
        self._pending_hal_bracket = (oid, stop, target)

        self.hal_dir = direction
        self.hal_entry = entry
        self.hal_open_id = oid
        self.hal_trades += 1
        if first:
            self.hal_first_dir = direction
            self.hal_state = 1
        else:
            self.hal_state = 3
        if direction == 1:
            self.hal_longs_today += 1
        else:
            self.hal_shorts_today += 1

    # ------------------------------------------------------------- pullback
    def _pullback(self, eng: PullbackEngine, bar: Bar, pf: Portfolio, i: int,
                  in_rth: bool, flatten: bool, weekday: int,
                  held: bool, extra_gate: bool, enabled: bool) -> None:
        c = self.cfg
        cfg = eng.cfg
        if eng.direction == 0:
            return

        eng.update_break(bar)
        minute = bar.ny_minute()
        ids = eng.ids
        live = pf.any_open(*ids)

        window = (in_rth and not self.day_halt and minute <= cfg.cutoff
                  and extra_gate and eng.size_ok())

        # Held behind a higher-precedence setup: the limit is not cancelled,
        # but if price trades through it during the hold the setup is forfeit.
        if enabled and eng.broke and not eng.placed and not eng.closed and held:
            if eng.traded_through_entry(bar):
                eng.missed = True

        can_place = (enabled and eng.broke and not eng.placed and not eng.closed
                     and window and not flatten
                     and (not c.no_double_break or not eng.opp_broke)
                     and eng.qty1 >= 1 and not held and not eng.missed)
        if can_place:
            side = Side.LONG if eng.direction == 1 else Side.SHORT
            pf.limit_entry(ids[0], side, eng.e1, eng.qty1, group=cfg.name)
            if eng.use_add and eng.qty2 >= 1:
                pf.limit_entry(ids[1], side, eng.e2, eng.qty2, group=cfg.name)
            eng.placed = True

        # On the first fill, fix the shared stop and target.
        if live and not eng.filled_any:
            eng.filled_any = True
            eng.cur_stop = eng.zstop
            eng.cur_tp = resolve_tp(eng.direction, eng.r_high, eng.r_low, eng.rng,
                                    self.day_high, self.day_low, cfg.tp_long, cfg.tp_short)
        if eng.filled_any and eng.cur_stop is not None:
            pf.set_bracket_group(ids, eng.cur_stop, eng.cur_tp)

        if eng.filled_any and not live:
            eng.closed = True

        expired = (not window) and not live
        if eng.closed or flatten or not enabled or eng.missed \
                or (c.no_double_break and eng.opp_broke) or expired:
            if not live:
                pf.cancel(*ids)


def run_combined(strategy: HalyardORBIB, bars: list[Bar],
                 progress_every: int = 0) -> tuple[list[Trade], Portfolio]:
    """Feed bars through the portfolio and the combined strategy."""
    c = strategy.cfg
    pf = Portfolio(
        point_value=c.point_value, tick_size=c.tick_size,
        commission_per_contract=c.commission_per_contract,
        slippage_ticks=c.slippage_ticks, process_on_close=True,
    )
    strategy._pending_hal_bracket = None

    for i, bar in enumerate(bars):
        exits = pf.process_bar(bar, i)
        strategy.on_bar(bar, pf, i, exits)
        pf.process_close(bar, i)
        # Halyard's market order fills at this bar's close; attach its bracket
        # immediately so it is live from the next bar like any other.
        pending = getattr(strategy, "_pending_hal_bracket", None)
        if pending is not None:
            oid, stop, target = pending
            if pf.is_open(oid):
                pf.set_bracket(oid, stop, target)
                strategy._pending_hal_bracket = None
        if progress_every and i and i % progress_every == 0:
            print(f"  ...{i:,}/{len(bars):,} bars, {len(pf.trades)} trades", flush=True)

    if bars:
        pf.force_flat(bars[-1])
    return pf.trades, pf

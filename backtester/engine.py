"""Bar-by-bar backtesting engine that simulates a futures broker.

How a bar is processed
----------------------
1. Market orders requested on the previous bar's close (close / flatten) fill at this bar's OPEN.
2. Resting orders are checked against the bar's price path. Because we only know Open/High/Low/Close,
   the path inside the bar is assumed to be (same rule as TradingView's broker emulator):
       open -> high -> low -> close   if the open is closer to the high
       open -> low -> high -> close   otherwise
   Orders trigger in the order the price reaches them, so an entry can fill and then hit its stop or
   target later in the same bar.
3. The strategy's ``on_bar`` runs at the bar's CLOSE and can place / cancel / modify orders. New orders
   become active from the next bar.

Orders
------
* Entries are LIMIT orders with an optional bracket (stop-loss + take-profit) that becomes active the
  moment the entry fills. Stop and target are one-cancels-other.
* Each entry has a unique ``tag`` (like a Pine ``strategy.entry`` id). Several tags can be open at once
  (e.g. a first unit and an average-down unit).

Realism settings (see ``BacktestConfig``)
-----------------------------------------
* ``limit_through_ticks``: 0 = a limit fills as soon as price touches it (TradingView default);
  1 = price must trade 1 tick THROUGH the limit (more realistic - you are not always first in the queue).
* ``slippage_ticks``: applied against you on stop and market fills (never on limit fills).
* ``commission``: $ per contract per side.
* ``pessimistic``: if a trade's stop and target are both inside one bar, assume the stop was hit.
* ``orders_on_close``: market orders sent from ``on_bar`` fill at THAT bar's close (TradingView
  ``process_orders_on_close = true``). Off: they fill at the next bar's open.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

TZ = "America/New_York"


@dataclass
class BacktestConfig:
    point_value: float = 2.0
    tick_size: float = 0.25
    commission: float = 0.62
    slippage_ticks: int = 1
    limit_through_ticks: int = 1
    pessimistic: bool = False
    orders_on_close: bool = True
    initial_capital: float = 150000.0


@dataclass
class Bar:
    time: pd.Timestamp   # bar OPEN time, America/New_York
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class PendingEntry:
    tag: str
    side: int            # +1 long, -1 short
    qty: int
    limit: float
    stop: float | None
    target: float | None
    group: str
    placed_time: pd.Timestamp
    note: str = ""


@dataclass
class Lot:
    tag: str
    side: int
    qty: int
    entry_price: float
    entry_time: pd.Timestamp
    stop: float | None
    target: float | None
    initial_stop: float | None
    group: str
    note: str = ""


@dataclass
class Trade:
    tag: str
    group: str
    side: int
    qty: int
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str
    pnl_points: float
    gross_pnl: float
    commission: float
    net_pnl: float
    r_multiple: float | None
    note: str = ""


@dataclass
class Fill:
    time: pd.Timestamp
    tag: str
    group: str
    kind: str            # "entry" | "stop" | "target" | "market"
    side: int            # +1 buy, -1 sell
    qty: int
    price: float


class Broker:
    """The object a strategy talks to (``ctx`` in ``on_bar``)."""

    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.pending: dict[str, PendingEntry] = {}
        self.lots: dict[str, Lot] = {}
        self.trades: list[Trade] = []
        self.fills: list[Fill] = []
        self.bar_fills: list[Fill] = []       # fills that happened during the current bar
        self.bar_exits: list[Trade] = []      # trades closed during the current bar
        self._market_close: dict[str, str] = {}   # tag -> reason, filled at next open
        self._market_entry: dict[str, PendingEntry] = {}   # filled at next open
        self.realized = 0.0
        self.bar: Bar | None = None
        self.i = -1                          # index of the current bar
        self.times = None                    # every bar's open time (set by run_backtest)
        self.annotations: list[dict] = []     # levels / markers a strategy wants drawn on the chart

    # ------------------------------------------------------------------ strategy API
    @property
    def time(self) -> pd.Timestamp:
        return self.bar.time

    @property
    def point_value(self) -> float:
        return self.cfg.point_value

    @property
    def tick_size(self) -> float:
        return self.cfg.tick_size

    def bars_time(self, i: int) -> pd.Timestamp:
        return self.times[i]

    def round_tick(self, price: float) -> float:
        t = self.cfg.tick_size
        return round(round(price / t) * t, 10)

    def place_entry(self, tag: str, side: int, qty: int, limit: float, stop: float | None = None,
                    target: float | None = None, group: str = "", note: str = "") -> None:
        """Rest a LIMIT entry order with an optional stop-loss / take-profit bracket."""
        if qty < 1 or tag in self.lots:
            return
        self.pending[tag] = PendingEntry(tag, side, int(qty), self.round_tick(limit),
                                         None if stop is None else self.round_tick(stop),
                                         None if target is None else self.round_tick(target),
                                         group, self.bar.time, note)

    def cancel(self, tag: str) -> None:
        self.pending.pop(tag, None)

    def cancel_all(self) -> None:
        self.pending.clear()

    def set_exits(self, tag: str, stop: float | None = None, target: float | None = None) -> None:
        """Move the stop and/or target of an open position or a pending entry's bracket."""
        obj = self.lots.get(tag) or self.pending.get(tag)
        if obj is None:
            return
        if stop is not None:
            obj.stop = self.round_tick(stop)
        if target is not None:
            obj.target = self.round_tick(target)

    def market_entry(self, tag: str, side: int, qty: int, stop: float | None = None,
                     target: float | None = None, group: str = "", note: str = "") -> float | None:
        """Enter at market: this bar's close (orders_on_close) or the next bar's open. Returns the fill
        price when it fills immediately."""
        if qty < 1 or tag in self.lots:
            return None
        p = PendingEntry(tag, side, int(qty), self.bar.close, None if stop is None else self.round_tick(stop),
                         None if target is None else self.round_tick(target), group, self.bar.time, note)
        if self.cfg.orders_on_close:
            self.pending[tag] = p
            px = self.bar.close + side * self.cfg.slippage_ticks * self.cfg.tick_size
            self._open_lot(p, px, self.bar.time)
            return px
        self._market_entry[tag] = p
        return None

    def close(self, tag: str, reason: str = "Close") -> None:
        """Close an open position at market: this bar's close (orders_on_close) or the next bar's open."""
        lot = self.lots.get(tag)
        if lot is None:
            return
        if self.cfg.orders_on_close:
            slip = self.cfg.slippage_ticks * self.cfg.tick_size
            self._close_lot(lot, self.bar.close - lot.side * slip, self.bar.time, "market", reason)
        else:
            self._market_close.setdefault(tag, reason)

    def close_all(self, reason: str = "Close all") -> None:
        for tag in list(self.lots):
            self.close(tag, reason)

    def close_group(self, group: str, reason: str) -> None:
        for tag, lot in list(self.lots.items()):
            if lot.group == group:
                self.close(tag, reason)

    def cancel_group(self, group: str) -> None:
        for tag, p in list(self.pending.items()):
            if p.group == group:
                del self.pending[tag]

    def group_open(self, group: str) -> bool:
        return any(l.group == group for l in self.lots.values())

    def is_pending(self, tag: str) -> bool:
        return tag in self.pending

    def is_open(self, tag: str) -> bool:
        return tag in self.lots

    def position(self) -> int:
        return sum(l.side * l.qty for l in self.lots.values())

    def open_pnl(self, price: float | None = None) -> float:
        px = self.bar.close if price is None else price
        return sum((px - l.entry_price) * l.side * l.qty * self.cfg.point_value for l in self.lots.values())

    @property
    def equity(self) -> float:
        """Account equity at the current bar's close (realized + open P&L)."""
        return self.cfg.initial_capital + self.realized + self.open_pnl()

    def annotate(self, **kw) -> None:
        self.annotations.append(kw)

    # ------------------------------------------------------------------ fills
    def _commission(self, qty: int) -> float:
        return qty * self.cfg.commission

    def _open_lot(self, p: PendingEntry, price: float, time: pd.Timestamp) -> None:
        del self.pending[p.tag]
        self.lots[p.tag] = Lot(p.tag, p.side, p.qty, price, time, p.stop, p.target, p.stop, p.group, p.note)
        self.realized -= self._commission(p.qty)
        f = Fill(time, p.tag, p.group, "entry", p.side, p.qty, price)
        self.fills.append(f)
        self.bar_fills.append(f)

    def _close_lot(self, lot: Lot, price: float, time: pd.Timestamp, kind: str, reason: str) -> None:
        del self.lots[lot.tag]
        self._market_close.pop(lot.tag, None)
        pts = (price - lot.entry_price) * lot.side
        gross = pts * lot.qty * self.cfg.point_value
        comm = 2 * self._commission(lot.qty)
        self.realized += gross - self._commission(lot.qty)   # entry commission was charged at entry
        risk = None
        if lot.initial_stop is not None and lot.initial_stop != lot.entry_price:
            risk = abs(lot.entry_price - lot.initial_stop)
        t = Trade(lot.tag, lot.group, lot.side, lot.qty, lot.entry_time, lot.entry_price, time, price,
                  reason, pts, gross, comm, gross - comm, (pts / risk) if risk else None, lot.note)
        self.trades.append(t)
        self.bar_exits.append(t)
        f = Fill(time, lot.tag, lot.group, kind, -lot.side, lot.qty, price)
        self.fills.append(f)
        self.bar_fills.append(f)

    # ------------------------------------------------------------------ bar simulation
    def process_bar(self, bar: Bar) -> None:
        self.bar = bar
        self.bar_fills = []
        self.bar_exits = []
        slip = self.cfg.slippage_ticks * self.cfg.tick_size

        # 1) market orders from the previous close fill at the open
        for tag, reason in list(self._market_close.items()):
            lot = self.lots.get(tag)
            if lot is not None:
                self._close_lot(lot, bar.open - lot.side * slip, bar.time, "market", reason)
        self._market_close.clear()
        for tag, p in list(self._market_entry.items()):
            self.pending[tag] = p
            self._open_lot(p, bar.open + p.side * slip, bar.time)
        self._market_entry.clear()

        if not self.pending and not self.lots:
            return

        # 2) resting orders against the intrabar path
        if abs(bar.open - bar.high) < abs(bar.open - bar.low):
            path = [bar.high, bar.low, bar.close]
        else:
            path = [bar.low, bar.high, bar.close]
        cur = bar.open
        self._trigger_at(cur, bar, at_open=True)
        for target in path:
            self._walk(cur, target, bar)
            cur = target

    def _orders(self):
        """Every live order as (trigger level, fill price, direction it triggers on, action)."""
        th = self.cfg.limit_through_ticks * self.cfg.tick_size
        slip = self.cfg.slippage_ticks * self.cfg.tick_size
        out = []
        for p in self.pending.values():
            # buy limit triggers on the way DOWN, sell limit on the way UP
            out.append((p.limit - p.side * th, p.limit, -p.side, ("entry", p)))
        for lot in self.lots.values():
            if lot.stop is not None:   # long stop triggers going down, short stop going up
                out.append((lot.stop, lot.stop - lot.side * slip, -lot.side, ("stop", lot)))
            if lot.target is not None:
                out.append((lot.target + lot.side * th, lot.target, lot.side, ("target", lot)))
        return out

    def _execute(self, action, fill_price: float, bar: Bar) -> None:
        kind, obj = action
        if kind == "entry":
            if obj.tag in self.pending:
                self._open_lot(obj, fill_price, bar.time)
            return
        lot = obj
        if lot.tag not in self.lots:
            return
        if kind == "target" and self.cfg.pessimistic and lot.stop is not None and bar.low <= lot.stop <= bar.high:
            slip = self.cfg.slippage_ticks * self.cfg.tick_size
            self._close_lot(lot, lot.stop - lot.side * slip, bar.time, "stop", "Stop (pessimistic)")
            return
        self._close_lot(lot, fill_price, bar.time, kind, "Stop" if kind == "stop" else "Target")

    def _trigger_at(self, price: float, bar: Bar, at_open: bool) -> None:
        """Fill every order that is already marketable at ``price`` (used for gaps at the open)."""
        for _ in range(20):
            hit = None
            for level, fill, direction, action in self._orders():
                if (direction < 0 and price <= level) or (direction > 0 and price >= level):
                    hit = (fill, action, direction)
                    break
            if hit is None:
                return
            fill, action, direction = hit
            kind, obj = action
            if kind == "stop":
                slip = self.cfg.slippage_ticks * self.cfg.tick_size
                px = price - obj.side * slip           # gap through a stop -> fill at the open, worse
            elif kind == "entry":
                px = min(fill, price) if obj.side > 0 else max(fill, price)   # gap -> better price
            else:
                px = max(fill, price) if obj.side > 0 else min(fill, price)
            self._execute(action, px, bar)

    def _walk(self, start: float, end: float, bar: Bar) -> None:
        """Move price from ``start`` to ``end`` and fill orders in the order they are reached."""
        if end == start:
            return
        direction = 1 if end > start else -1
        cur = start
        for _ in range(50):
            best = None
            for level, fill, d, action in self._orders():
                if d != direction:
                    continue
                if direction > 0 and cur <= level <= end or direction < 0 and end <= level <= cur:
                    dist = abs(level - cur)
                    if best is None or dist < best[0]:
                        best = (dist, level, fill, action)
            if best is None:
                return
            _, level, fill, action = best
            self._execute(action, fill, bar)
            cur = level


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series            # equity at each bar close
    fills: pd.DataFrame
    bars: pd.DataFrame
    config: BacktestConfig
    params: dict
    annotations: list = field(default_factory=list)


def run_backtest(bars: pd.DataFrame, strategy, config: BacktestConfig) -> BacktestResult:
    """Run ``strategy`` over ``bars`` (DataFrame indexed by America/New_York bar-open times)."""
    broker = Broker(config)
    if hasattr(strategy, "prepare"):
        strategy.prepare(bars)
    times = bars.index
    broker.times = times
    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    v = bars["volume"].to_numpy() if "volume" in bars else [0.0] * len(bars)
    eq = []
    for i in range(len(bars)):
        bar = Bar(times[i], float(o[i]), float(h[i]), float(l[i]), float(c[i]), float(v[i]))
        broker.i = i
        broker.process_bar(bar)
        strategy.on_bar(broker, bar)
        eq.append(broker.equity)
    # close anything still open at the last close
    if broker.lots and len(bars):
        for lot in list(broker.lots.values()):
            broker._close_lot(lot, broker.bar.close, broker.bar.time, "market", "End of data")
        eq[-1] = broker.equity

    trades = pd.DataFrame([t.__dict__ for t in broker.trades])
    fills = pd.DataFrame([f.__dict__ for f in broker.fills])
    return BacktestResult(trades, pd.Series(eq, index=times, name="equity"), fills, bars, config,
                          dict(getattr(strategy, "params", {})), broker.annotations)

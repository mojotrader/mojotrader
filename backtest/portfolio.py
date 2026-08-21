"""Multi-entry broker for strategies that hold several named units at once.

The single-position :class:`~backtest.engine.Broker` cannot express a Pine
script with ``pyramiding > 0``: several ``strategy.entry`` ids live at the same
time, each closable on its own with ``strategy.close(id)``, sharing a bracket
per setup. This broker models that.

Fill timing follows the same rules as the single-position engine -- an order
placed while bar N is evaluated is eligible from bar N+1, a bar covering both
stop and target resolves to the stop, and gapped stops fill worse while gapped
limits fill better. The difference here is bookkeeping, not semantics.

On netting: Pine nets every id into one position, so an opposite-side entry
would reverse rather than hedge. This broker keeps each id as its own position,
which is equivalent *provided the strategy never holds opposing ids at once*.
The combined Halyard/ORB/IB script guarantees that through its seat rules --
only one setup is live at a time, and a setup's two units always share a
direction. :meth:`net_position` is exposed so a caller can assert it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bars import Bar
from .engine import Side, Trade


@dataclass(slots=True)
class PendingOrder:
    oid: str
    side: Side
    qty: float
    kind: str                 # "market" | "limit" | "stop"
    level: float | None
    group: str
    placed_bar: int


@dataclass(slots=True)
class OpenEntry:
    oid: str
    side: Side
    qty: float
    entry_price: float
    entry_ts: datetime
    entry_bar: int
    group: str
    stop: float | None = None
    target: float | None = None
    bracket_live_from: int = 0


class Portfolio:
    """Named-entry broker with per-id brackets and closes."""

    def __init__(
        self,
        point_value: float = 20.0,
        tick_size: float = 0.25,
        commission_per_contract: float = 0.5,
        slippage_ticks: float = 1.0,
        process_on_close: bool = True,
    ) -> None:
        self.point_value = point_value
        self.tick_size = tick_size
        self.commission_per_contract = commission_per_contract
        self.slippage_ticks = slippage_ticks
        self.process_on_close = process_on_close

        self.orders: dict[str, PendingOrder] = {}
        self.positions: dict[str, OpenEntry] = {}
        self.trades: list[Trade] = []
        self.realised: float = 0.0

        self._pending_close: dict[str, str] = {}
        self._bar_index = 0
        self._exits_this_bar: list[Trade] = []

    # ---------------------------------------------------------------- orders
    def market_entry(self, oid: str, side: Side, qty: float, group: str = "") -> None:
        if oid in self.positions or qty < 1:
            return
        self.orders[oid] = PendingOrder(oid, side, qty, "market", None, group, self._bar_index)

    def limit_entry(self, oid: str, side: Side, level: float, qty: float, group: str = "") -> None:
        if oid in self.positions or qty < 1:
            return
        self.orders[oid] = PendingOrder(oid, side, qty, "limit", level, group, self._bar_index)

    def stop_entry(self, oid: str, side: Side, level: float, qty: float, group: str = "") -> None:
        if oid in self.positions or qty < 1:
            return
        self.orders[oid] = PendingOrder(oid, side, qty, "stop", level, group, self._bar_index)

    def cancel(self, *oids: str) -> None:
        for oid in oids:
            self.orders.pop(oid, None)

    def cancel_all(self) -> None:
        self.orders.clear()

    def set_bracket(self, oid: str, stop: float | None, target: float | None) -> None:
        pos = self.positions.get(oid)
        if pos is None:
            return
        pos.stop = stop
        pos.target = target

    def set_bracket_group(self, oids: tuple[str, ...], stop: float | None, target: float | None) -> None:
        """Apply one stop/target to every open id of a setup, as Pine does."""
        for oid in oids:
            self.set_bracket(oid, stop, target)

    def close(self, *oids: str, reason: str = "close") -> None:
        for oid in oids:
            if oid in self.positions:
                self._pending_close[oid] = reason

    def close_all(self, reason: str = "close") -> None:
        for oid in list(self.positions):
            self._pending_close[oid] = reason

    # ----------------------------------------------------------------- state
    def is_open(self, oid: str) -> bool:
        return oid in self.positions

    def any_open(self, *oids: str) -> bool:
        return any(o in self.positions for o in oids)

    def has_order(self, oid: str) -> bool:
        return oid in self.orders

    def net_position(self) -> float:
        return sum(p.qty * p.side.value for p in self.positions.values())

    def equity(self, mark: float) -> float:
        """Realised P&L plus open P&L marked at ``mark``."""
        floating = sum(
            (mark - p.entry_price) * p.side.value * p.qty * self.point_value
            for p in self.positions.values()
        )
        return self.realised + floating

    # ------------------------------------------------------------ bar machine
    def process_bar(self, bar: Bar, bar_index: int) -> list[Trade]:
        """Resolve brackets, then resting entry orders. Returns exits filled here."""
        self._bar_index = bar_index
        self._exits_this_bar = []

        for oid in list(self.positions):
            pos = self.positions.get(oid)
            if pos is not None and bar_index >= pos.bracket_live_from:
                self._check_bracket(bar, pos)

        for oid, order in list(self.orders.items()):
            if order.placed_bar >= bar_index:
                continue          # placed this bar; not eligible until the next
            if oid in self.positions:
                continue
            self._try_fill(bar, bar_index, order)

        return list(self._exits_this_bar)

    def process_close(self, bar: Bar, bar_index: int) -> list[Trade]:
        """On-close fills: market entries and requested closes."""
        self._bar_index = bar_index
        filled: list[Trade] = []

        for oid, reason in list(self._pending_close.items()):
            if oid in self.positions:
                t = self._exit(bar, self.positions[oid], bar.close, reason, slip=True)
                filled.append(t)
        self._pending_close.clear()

        if self.process_on_close:
            for oid, order in list(self.orders.items()):
                if order.kind == "market" and oid not in self.positions:
                    self._open(order, bar.close, bar, bar_index, slip=True)
                    del self.orders[oid]
        return filled

    def force_flat(self, bar: Bar, reason: str = "end of data") -> None:
        for oid in list(self.positions):
            self._exit(bar, self.positions[oid], bar.close, reason, slip=False)
        self.orders.clear()

    # ---------------------------------------------------------------- internals
    def _try_fill(self, bar: Bar, bar_index: int, order: PendingOrder) -> None:
        if order.kind == "market":
            if not self.process_on_close:
                self._open(order, bar.open, bar, bar_index, slip=True)
                del self.orders[order.oid]
            return

        level = order.level
        assert level is not None
        if order.kind == "limit":
            # A buy limit rests below the market; a gap below fills better.
            if order.side is Side.LONG and bar.low <= level:
                self._open(order, min(level, bar.open), bar, bar_index, slip=False)
                del self.orders[order.oid]
            elif order.side is Side.SHORT and bar.high >= level:
                self._open(order, max(level, bar.open), bar, bar_index, slip=False)
                del self.orders[order.oid]
        else:  # stop entry: a gap through fills worse
            if order.side is Side.LONG and bar.high >= level:
                self._open(order, max(level, bar.open), bar, bar_index, slip=True)
                del self.orders[order.oid]
            elif order.side is Side.SHORT and bar.low <= level:
                self._open(order, min(level, bar.open), bar, bar_index, slip=True)
                del self.orders[order.oid]

    def _check_bracket(self, bar: Bar, pos: OpenEntry) -> None:
        stop, target = pos.stop, pos.target
        if stop is None and target is None:
            return
        if pos.side is Side.LONG:
            if stop is not None and bar.low <= stop:
                self._exit(bar, pos, min(stop, bar.open), "stop", slip=True)
            elif target is not None and bar.high >= target:
                self._exit(bar, pos, max(target, bar.open), "target", slip=False)
        else:
            if stop is not None and bar.high >= stop:
                self._exit(bar, pos, max(stop, bar.open), "stop", slip=True)
            elif target is not None and bar.low <= target:
                self._exit(bar, pos, min(target, bar.open), "target", slip=False)

    def _open(self, order: PendingOrder, price: float, bar: Bar,
              bar_index: int, slip: bool) -> None:
        fill = self._slip(price, order.side.value) if slip else price
        self.positions[order.oid] = OpenEntry(
            oid=order.oid, side=order.side, qty=order.qty, entry_price=fill,
            entry_ts=bar.ts, entry_bar=bar_index, group=order.group,
            bracket_live_from=bar_index + 1,
        )

    def _exit(self, bar: Bar, pos: OpenEntry, price: float, reason: str, slip: bool) -> Trade:
        fill = self._slip(price, -pos.side.value) if slip else price
        commission = self.commission_per_contract * pos.qty * 2.0
        trade = Trade(
            side=pos.side, qty=pos.qty,
            entry_ts=pos.entry_ts, entry_price=pos.entry_price,
            exit_ts=bar.ts, exit_price=fill, exit_reason=reason,
            commission=commission,
            bars_held=max(self._bar_index - pos.entry_bar, 0),
            entry_tag=pos.oid,
        )
        self.trades.append(trade)
        self._exits_this_bar.append(trade)
        self.realised += trade.points * trade.qty * self.point_value - commission
        del self.positions[pos.oid]
        return trade

    def _slip(self, price: float, direction: int) -> float:
        return price + direction * self.slippage_ticks * self.tick_size
